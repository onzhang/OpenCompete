"""OpenCompete 本地正式生命周期的离线系统验收。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from opencompete import cli
from opencompete.application import ApplicationRunner
from opencompete.cli import main
from opencompete.evaluation import (
    EvaluationRequest,
    EvaluationService,
    EvaluationTarget,
    MetricStatus,
)
from opencompete.models import RunStatus, ScopeConfirmation
from opencompete.persistence import RunRepository
from opencompete.settings import OpenCompeteSettings

sys.path.insert(0, str(Path(__file__).parent / "helpers"))

from acceptance_support import (  # noqa: E402
    CASE_ID,
    REPORT_MARKDOWN,
    AcceptanceExtractor,
    AcceptanceJudge,
    acceptance_request,
    build_acceptance_research_graph,
    write_acceptance_dataset,
)


def _runner(
    data_root: Path,
    *,
    partial: bool,
) -> ApplicationRunner:
    repository = RunRepository(data_root)
    return ApplicationRunner(
        OpenCompeteSettings(data_dir=data_root),
        repository=repository,
        research_graph=build_acceptance_research_graph(),
        extractor=AcceptanceExtractor(partial=partial),
        evaluation_service=EvaluationService(
            repository=repository,
            judge=AcceptanceJudge(),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("partial", "expected_status"),
    [
        (False, RunStatus.COMPLETED),
        (True, RunStatus.PARTIAL),
    ],
)
async def test_real_local_lifecycle_reopens_runner_loads_and_evaluates(
    tmp_path: Path,
    partial: bool,
    expected_status: RunStatus,
) -> None:
    """真实 SQLite 与产物仓库共同覆盖创建、恢复、发布、读取和评测。"""
    data_root = tmp_path / "data"
    first_runner = _runner(data_root, partial=partial)

    pending = await first_runner.create_run(acceptance_request())

    assert pending.status is RunStatus.AWAITING_CONFIRMATION
    assert pending.pending_confirmation is True
    assert pending.scope is not None and pending.scope.frozen is False
    assert (data_root / "checkpoints.sqlite3").is_file()
    run_dir = data_root / "runs" / pending.run_id
    assert (run_dir / "manifest.json").is_file()
    assert not (run_dir / "bundle.json").exists()
    assert not (run_dir / "report.md").exists()

    reopened_runner = _runner(data_root, partial=partial)
    restored = await reopened_runner.resume_run(pending.run_id)

    assert restored.status is RunStatus.AWAITING_CONFIRMATION
    assert restored.pending_confirmation is True
    assert restored.scope == pending.scope

    published = await reopened_runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=restored.scope.revision,
        ),
    )
    loaded = await reopened_runner.load_run(pending.run_id)
    summaries = await reopened_runner.list_runs()

    assert published.status is expected_status
    assert published.bundle is not None
    assert loaded == published.bundle
    assert "Alpha 与 Beta 都提供核心能力" in loaded.report.markdown
    assert (
        '<https://example.com/alpha>' in loaded.report.markdown
        and '<https://example.org/beta>' in loaded.report.markdown
    )
    assert "Alpha 与 Beta 都提供核心能力" in (run_dir / "report.md").read_text(
        encoding="utf-8"
    )
    assert [summary.run_id for summary in summaries] == [pending.run_id]
    assert summaries[0].status is expected_status
    if partial:
        assert "一个研究单元失败" in "".join(loaded.report.limitations)
    else:
        assert any("范围单元" in item for item in loaded.report.limitations)

    verified = RunRepository(data_root).load_verified(pending.run_id)
    assert verified.manifest.file_hashes.keys() == {"bundle.json", "report.md"}
    assert verified.manifest.artifact_revision == loaded.artifact_revision
    assert verified.manifest.scope_revision == loaded.scope.revision

    dataset = write_acceptance_dataset(tmp_path / "benchmark", pending.scope)
    batch = await reopened_runner.evaluate_dataset(
        EvaluationRequest(
            dataset=dataset,
            targets=(
                EvaluationTarget(case_id=CASE_ID, run_id=pending.run_id),
            ),
            allow_draft=True,
        )
    )

    result = batch.results[0]
    assert result.target.artifact_revision == loaded.artifact_revision
    assert result.hard_gates.passed is True
    assert result.race.status is MetricStatus.COMPLETED
    assert result.fact.status is MetricStatus.COMPLETED
    assert result.fact.support_ratio == 1.0
    evaluation_files = list((run_dir / "evaluations").glob("*.json"))
    assert len(evaluation_files) == 1


def test_show_command_reads_published_report_from_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """show 在研究进程结束后只凭长期业务产物展示报告。"""
    data_root = tmp_path / "data"

    async def publish() -> str:
        runner = _runner(data_root, partial=False)
        pending = await runner.create_run(acceptance_request())
        assert pending.scope is not None
        completed = await runner.resume_run(
            pending.run_id,
            ScopeConfirmation(
                accepted=True,
                expected_revision=pending.scope.revision,
            ),
        )
        assert completed.status is RunStatus.COMPLETED
        return pending.run_id

    run_id = asyncio.run(publish())

    exit_code = main(["--data-dir", str(data_root), "show", run_id])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert run_id in output
    assert "Alpha 与 Beta 竞品分析" in output


def test_default_cli_new_reaches_confirmation_and_cancels_without_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """默认 Runner 到确认点前不需要模型、Tavily 或 Agent Server。"""
    data_root = tmp_path / "default-cli-data"
    monkeypatch.setattr(cli.console, "input", lambda _prompt: "n")

    exit_code = cli.main(
        [
            "--data-dir",
            str(data_root),
            "new",
            "比较 Alpha 与 Beta",
            "--entity",
            "Alpha",
            "--entity",
            "Beta",
            "--level",
            "product_service",
        ]
    )

    assert exit_code == 0
    summaries = RunRepository(data_root).list_runs()
    assert len(summaries) == 1
    assert summaries[0].status is RunStatus.CANCELLED
    output = capsys.readouterr().out
    assert "建议研究范围" in output
    assert "已取消" in output
