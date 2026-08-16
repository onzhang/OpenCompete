"""OpenCompete 交互式 CLI 的离线契约测试。"""

from __future__ import annotations

import argparse
import ast
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console

from opencompete import cli
from opencompete.analysis import build_draft_scope
from opencompete.models import (
    AnalysisLevel,
    BudgetSnapshot,
    CandidateSelection,
    ConfirmationKind,
    DiscoveryCandidate,
    ErrorCategory,
    ErrorRecord,
    ReportArtifact,
    ResearchEntity,
    ResearchRequest,
    RunBundle,
    RunHandle,
    RunStatus,
    RunSummary,
    ScopeConfirmation,
    ScopeSpec,
)


def _request() -> ResearchRequest:
    return ResearchRequest(
        query="比较甲公司与乙公司",
        entities=(ResearchEntity(name="甲公司"), ResearchEntity(name="乙公司")),
        levels=(AnalysisLevel.COMPANY_ORGANIZATION,),
    )


def _scope() -> ScopeSpec:
    return build_draft_scope(_request())


def _bundle(run_id: str = "run-show") -> RunBundle:
    return RunBundle(
        run_id=run_id,
        scope=_scope(),
        report=ReportArtifact(markdown="# 已保存的竞品分析报告"),
        budget=BudgetSnapshot(),
        status=RunStatus.PARTIAL,
    )


class FakeRunner:
    """记录 CLI 到应用层的调用，不接触 Graph 或持久化实现。"""

    def __init__(
        self,
        *,
        create_result: RunHandle | None = None,
        resume_results: Sequence[RunHandle] = (),
        summaries: Sequence[RunSummary] = (),
        bundle: RunBundle | None = None,
        evaluation_result: object | None = None,
    ) -> None:
        self.create_result = create_result
        self.resume_results = list(resume_results)
        self.summaries = list(summaries)
        self.bundle = bundle
        self.evaluation_result = evaluation_result or object()
        self.calls: list[tuple[str, object]] = []

    async def create_run(self, request: ResearchRequest) -> RunHandle:
        self.calls.append(("create_run", request))
        assert self.create_result is not None
        return self.create_result

    async def resume_run(
        self,
        run_id: str,
        user_input: CandidateSelection | ScopeConfirmation | None = None,
    ) -> RunHandle:
        self.calls.append(("resume_run", (run_id, user_input)))
        assert self.resume_results
        return self.resume_results.pop(0)

    async def list_runs(self) -> list[RunSummary]:
        self.calls.append(("list_runs", None))
        return self.summaries

    async def load_run(self, run_id: str) -> RunBundle:
        self.calls.append(("load_run", run_id))
        assert self.bundle is not None
        return self.bundle

    async def evaluate_dataset(self, request: Any) -> object:
        self.calls.append(("evaluate_dataset", request))
        return self.evaluation_result


def _install_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> list[Any]:
    settings_seen: list[Any] = []

    def factory(settings: Any) -> FakeRunner:
        settings_seen.append(settings)
        return runner

    monkeypatch.setattr(cli, "runner_factory", factory)
    return settings_seen


def _all_option_names(parser: argparse.ArgumentParser) -> list[str]:
    names: list[str] = []
    pending = [parser]
    while pending:
        current = pending.pop()
        for action in current._actions:
            names.extend(action.option_strings)
            if isinstance(action, cli.argparse._SubParsersAction):
                pending.extend(action.choices.values())
    return names


@pytest.mark.parametrize("command", ["new", "resume", "list", "show", "evaluate"])
def test_parser_exposes_all_official_commands_with_chinese_help(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """五个正式命令都有中文帮助，且不需要加载 Application。"""
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([command, "--help"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    output = captured.out
    assert command in output
    assert "帮助" in output or "研究" in output or "任务" in output or "评测" in output


def test_parser_never_exposes_api_key_arguments() -> None:
    """任何命令都不能把 API Key 放进命令历史。"""
    option_names = " ".join(_all_option_names(cli.build_parser())).casefold()

    assert "api-key" not in option_names
    assert "api_key" not in option_names
    assert "secret" not in option_names
    assert "token" not in option_names


def test_cli_has_no_direct_infrastructure_or_remote_runtime_imports() -> None:
    """CLI 边界不允许越过 Application 去调用基础设施或远程运行层。"""
    source = Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "langgraph",
        "langsmith",
        "sqlite3",
        "tavily",
        "mcp",
        "open_deep_research",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )


def test_argparse_structural_help_is_localized_to_chinese(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """argparse 自带的标题和帮助说明也不能暴露英文界面。"""
    with pytest.raises(SystemExit) as exc_info:
        cli.build_parser().parse_args(["new", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "用法：" in output
    assert "选项：" in output
    assert "usage:" not in output
    assert "options:" not in output
    assert "show this help message and exit" not in output


def test_new_displays_scope_and_resumes_same_run_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """创建命令在确认前展示建议范围，确认后恢复同一 run_id。"""
    scope = _scope()
    runner = FakeRunner(
        create_result=RunHandle(
            run_id="run-new",
            status=RunStatus.AWAITING_CONFIRMATION,
            scope=scope,
            pending_confirmation=True,
        ),
        resume_results=[
            RunHandle(run_id="run-new", status=RunStatus.COMPLETED),
        ],
    )
    settings_seen = _install_runner(monkeypatch, runner)
    monkeypatch.setattr(cli.console, "input", lambda _prompt: "y")

    exit_code = cli.main(
        [
            "new",
            "比较甲公司与乙公司",
            "--entity",
            "甲公司",
            "--entity",
            "乙公司",
            "--level",
            "company_organization",
        ]
    )

    assert exit_code == 0
    assert len(settings_seen) == 1
    assert [name for name, _payload in runner.calls] == ["create_run", "resume_run"]
    created_request = runner.calls[0][1]
    assert isinstance(created_request, ResearchRequest)
    run_id, confirmation = cast(
        tuple[str, ScopeConfirmation | None], runner.calls[1][1]
    )
    assert run_id == "run-new"
    assert isinstance(confirmation, ScopeConfirmation)
    assert confirmation.accepted is True
    assert confirmation.expected_revision == scope.revision
    output = capsys.readouterr().out
    assert "建议研究范围" in output
    assert "甲公司" in output
    assert "乙公司" in output
    assert "已完成" in output


def test_new_discovery_shows_sources_accepts_rename_then_confirms_scope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """候选选择与最终范围使用两个明确确认步骤。"""
    initial_scope = _scope()
    candidate = DiscoveryCandidate(
        entity=ResearchEntity(name="候选页面标题", user_provided=False),
        source_url="https://example.com/candidate",
        source_title="候选来源页",
        summary="候选来源摘要",
    )
    final_scope = build_draft_scope(
        ResearchRequest(
            query="比较甲公司、乙公司与丙公司",
            entities=(
                ResearchEntity(name="甲公司"),
                ResearchEntity(name="乙公司"),
                ResearchEntity(name="丙公司", user_provided=False),
            ),
            levels=(AnalysisLevel.COMPANY_ORGANIZATION,),
        )
    )
    runner = FakeRunner(
        create_result=RunHandle(
            run_id="run-discovery",
            status=RunStatus.AWAITING_CONFIRMATION,
            scope=initial_scope,
            pending_confirmation=True,
            confirmation_kind=ConfirmationKind.CANDIDATE_SELECTION,
            discovery_candidates=(candidate,),
        ),
        resume_results=[
            RunHandle(
                run_id="run-discovery",
                status=RunStatus.AWAITING_CONFIRMATION,
                scope=final_scope,
                pending_confirmation=True,
                confirmation_kind=ConfirmationKind.SCOPE_CONFIRMATION,
                discovery_candidates=(candidate,),
            ),
            RunHandle(run_id="run-discovery", status=RunStatus.COMPLETED),
        ],
    )
    _install_runner(monkeypatch, runner)
    answers = iter(("1", "", "丙公司", "y"))
    monkeypatch.setattr(cli.console, "input", lambda _prompt: next(answers))

    assert (
        cli.main(
            [
                "new",
                "比较甲公司与乙公司",
                "--entity",
                "甲公司",
                "--entity",
                "乙公司",
                "--level",
                "company_organization",
                "--discover",
            ]
        )
        == 0
    )

    created = cast(ResearchRequest, runner.calls[0][1])
    assert created.discover_candidates is True
    first_input = cast(tuple[str, object], runner.calls[1][1])[1]
    second_input = cast(tuple[str, object], runner.calls[2][1])[1]
    assert isinstance(first_input, CandidateSelection)
    assert first_input.selected_candidates[0].entity_name == "丙公司"
    assert isinstance(second_input, ScopeConfirmation)
    captured = capsys.readouterr()
    output = captured.out
    assert "发现的候选竞品" in output
    assert "https://example.com/candidate" in output
    assert "对象名称必须由用户明确确认" in captured.err
    assert "已添加 1 个候选，已拒绝 0 个候选" in output
    assert "建议研究范围" in output


def test_new_rejection_is_delegated_to_runner_for_durable_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """拒绝范围也恢复同一任务，由 Runner 保存 cancelled 状态。"""
    scope = _scope()
    runner = FakeRunner(
        create_result=RunHandle(
            run_id="run-cancel",
            status=RunStatus.AWAITING_CONFIRMATION,
            scope=scope,
            pending_confirmation=True,
        ),
        resume_results=[
            RunHandle(run_id="run-cancel", status=RunStatus.CANCELLED),
        ],
    )
    _install_runner(monkeypatch, runner)
    monkeypatch.setattr(cli.console, "input", lambda _prompt: "n")

    exit_code = cli.main(
        ["new", "比较甲乙", "--entity", "甲公司", "--entity", "乙公司"]
    )

    assert exit_code == 0
    run_id, confirmation = cast(
        tuple[str, ScopeConfirmation | None], runner.calls[1][1]
    )
    assert run_id == "run-cancel"
    assert isinstance(confirmation, ScopeConfirmation)
    assert confirmation.accepted is False
    assert "已取消" in capsys.readouterr().out


def test_resume_prompts_when_existing_run_is_at_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复到确认点时仍使用相同 run_id 提交用户决定。"""
    scope = _scope()
    runner = FakeRunner(
        resume_results=[
            RunHandle(
                run_id="run-resume",
                status=RunStatus.AWAITING_CONFIRMATION,
                scope=scope,
                pending_confirmation=True,
            ),
            RunHandle(run_id="run-resume", status=RunStatus.PARTIAL),
        ]
    )
    _install_runner(monkeypatch, runner)
    monkeypatch.setattr(cli.console, "input", lambda _prompt: "是")

    assert cli.main(["resume", "run-resume"]) == 0

    assert runner.calls[0] == ("resume_run", ("run-resume", None))
    resumed_id, confirmation = cast(
        tuple[str, ScopeConfirmation | None], runner.calls[1][1]
    )
    assert resumed_id == "run-resume"
    assert isinstance(confirmation, ScopeConfirmation)
    assert confirmation.accepted is True


def test_list_only_calls_application_read_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """列表命令只调用 Runner 的历史读取用例。"""
    runner = FakeRunner(
        summaries=[
            RunSummary(
                run_id="run-list",
                status=RunStatus.PARTIAL,
                scope_revision=1,
                artifact_revision=2,
                updated_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
            )
        ]
    )
    _install_runner(monkeypatch, runner)

    assert cli.main(["list"]) == 0

    assert runner.calls == [("list_runs", None)]
    output = capsys.readouterr().out
    assert "run-list" in output
    assert "部分完成" in output


def test_show_only_calls_application_read_path_and_displays_saved_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """查看命令从 Runner 读取长期产物，不创建或恢复研究。"""
    runner = FakeRunner(bundle=_bundle())
    _install_runner(monkeypatch, runner)

    assert cli.main(["show", "run-show"]) == 0

    assert runner.calls == [("load_run", "run-show")]
    output = capsys.readouterr().out
    assert "部分完成" in output
    assert "已保存的竞品分析报告" in output


def test_evaluate_loads_targets_json_and_calls_offline_application_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批量评测只把固定数据集和目标绑定交给 Runner。"""
    targets_path = tmp_path / "targets.json"
    targets_path.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "case_id": "ocb-001",
                        "run_id": "run-evaluate",
                        "artifact_revision": 3,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runner = FakeRunner()
    _install_runner(monkeypatch, runner)

    assert (
        cli.main(
            ["evaluate", str(tmp_path / "dataset.json"), "--targets", str(targets_path)]
        )
        == 0
    )

    assert runner.calls[0][0] == "evaluate_dataset"
    request = cast(Any, runner.calls[0][1])
    assert request.dataset == tmp_path / "dataset.json"
    assert len(request.targets) == 1
    assert request.targets[0].case_id == "ocb-001"
    assert request.targets[0].run_id == "run-evaluate"
    assert request.targets[0].artifact_revision == 3


def test_evaluate_builds_single_target_from_case_and_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单案例评测要求 case/run 成对出现。"""
    runner = FakeRunner()
    _install_runner(monkeypatch, runner)

    assert (
        cli.main(
            [
                "evaluate",
                str(tmp_path / "dataset.json"),
                "--case",
                "ocb-002",
                "--run",
                "run-single",
            ]
        )
        == 0
    )

    request = cast(Any, runner.calls[0][1])
    assert request.targets[0].case_id == "ocb-002"
    assert request.targets[0].run_id == "run-single"
    assert request.targets[0].artifact_revision is None


def test_evaluate_reports_invalid_targets_as_chinese_input_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """损坏的 targets 文件属于可读输入错误，不能向用户输出堆栈。"""
    targets_path = tmp_path / "targets.json"
    targets_path.write_text("不是 JSON", encoding="utf-8")
    _install_runner(monkeypatch, FakeRunner())

    assert (
        cli.main(
            ["evaluate", str(tmp_path / "dataset.json"), "--targets", str(targets_path)]
        )
        == 1
    )

    captured = capsys.readouterr()
    compact_error = "".join(captured.err.split())
    assert "错误：" in compact_error
    assert "targets文件无效" in compact_error
    assert "Invalid JSON" not in captured.err
    assert "Traceback" not in captured.err


def test_expected_application_errors_are_shown_in_chinese(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """已分类错误只显示中文摘要，不输出堆栈。"""

    class FailingRunner(FakeRunner):
        async def load_run(self, run_id: str) -> RunBundle:
            from opencompete.errors import PersistenceError

            raise PersistenceError(f"找不到任务：{run_id}")

    _install_runner(monkeypatch, FailingRunner())

    assert cli.main(["show", "missing-run"]) == 1

    captured = capsys.readouterr()
    assert "错误：找不到任务：missing-run" in captured.err
    assert "Traceback" not in captured.err


def test_unexpected_error_is_redacted_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """未分类供应商异常不能把环境密钥或认证头输出到控制台。"""
    secret = "sk-cli-secret-sentinel"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    class UnexpectedFailingRunner(FakeRunner):
        async def load_run(self, run_id: str) -> RunBundle:
            del run_id
            raise RuntimeError(f"Authorization: Bearer {secret}")

    _install_runner(monkeypatch, UnexpectedFailingRunner())

    assert cli.main(["show", "run-secret"]) == 1

    captured = capsys.readouterr()
    assert "unexpected_error" in captured.err
    assert "[REDACTED]" in captured.err
    assert secret not in captured.err
    assert "Bearer" not in captured.err
    assert "Traceback" not in captured.err


def test_progress_shows_reconnect_count_and_specific_safe_detail() -> None:
    """重试反馈使用稳定动作行和供应商异常摘要行。"""
    stream = StringIO()
    progress = cli._CliProgress(Console(file=stream, color_system=None))

    progress(
        {
            "event": "model_retry_scheduled",
            "attempt": 1,
            "next_attempt": 2,
            "total_attempts": 5,
            "delay_seconds": 2,
            "fallback": False,
            "next_fallback": False,
            "error_detail": (
                "stream disconnected before completion: Upstream request failed"
            ),
        }
    )

    output = stream.getvalue()
    assert "正在重新连接 2/5" in output
    assert "2 秒后" in output
    assert "stream disconnected before completion" in output


def test_progress_announces_switch_to_fallback_provider() -> None:
    """第三次主供应商失败后明确显示切换备用模型。"""
    stream = StringIO()
    progress = cli._CliProgress(Console(file=stream, color_system=None))

    progress(
        {
            "event": "model_retry_scheduled",
            "attempt": 3,
            "next_attempt": 4,
            "total_attempts": 5,
            "delay_seconds": 10,
            "fallback": False,
            "next_fallback": True,
            "error_detail": "Upstream request failed",
        }
    )

    assert "正在切换备用模型 4/5" in stream.getvalue()


def test_provider_error_displays_safe_diagnostic_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户能看到准确排障字段，但不会看到秘密或响应正文。"""
    stream = StringIO()
    monkeypatch.setattr(
        cli,
        "error_console",
        Console(file=stream, color_system=None),
    )
    record = ErrorRecord(
        code="temporary_provider_error",
        category=ErrorCategory.PROVIDER,
        message="模型 Provider 暂时不可用",
        detail="Upstream request failed",
        exception_type="InternalServerError",
        cause_type="RemoteProtocolError",
        transport_reason="peer_closed_incomplete_body",
        http_status=502,
        provider_error_code="upstream_failed",
        request_id="req-safe",
        retryable=True,
    )

    cli._display_error(record)

    output = stream.getvalue()
    assert "Upstream request failed" in output
    assert "InternalServerError" in output
    assert "RemoteProtocolError" in output
    assert "peer_closed_incomplete_body" in output
    assert "HTTP：502" in output
    assert "upstream_failed" in output
    assert "req-safe" in output
