"""OpenCompete 本地交互式命令行入口。."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text

from opencompete.errors import DataValidationError, OpenCompeteError
from opencompete.models import (
    AnalysisLevel,
    CandidateChoice,
    CandidateSelection,
    ConfirmationKind,
    DiscoveryCandidate,
    ErrorRecord,
    ResearchEntity,
    ResearchRequest,
    RunBundle,
    RunHandle,
    RunStatus,
    RunSummary,
    ScopeConfirmation,
    ScopeSpec,
)
from opencompete.security import sanitized_error
from opencompete.settings import OpenCompeteSettings, load_settings

if TYPE_CHECKING:
    from opencompete.evaluation.models import EvaluationRequest


console = Console(highlight=False)
error_console = Console(stderr=True, highlight=False)


class ApplicationRunnerProtocol(Protocol):
    """CLI 唯一可调用的应用层接口。."""

    async def create_run(self, request: ResearchRequest) -> RunHandle:
        """创建任务并执行到确认点或终态。."""
        ...

    async def resume_run(
        self,
        run_id: str,
        user_input: CandidateSelection | ScopeConfirmation | None = None,
    ) -> RunHandle:
        """恢复已有任务并可提交范围确认结果。."""
        ...

    async def create_fixed_scope_run(
        self,
        request: ResearchRequest,
        scope: ScopeSpec,
        *,
        benchmark_case_id: str | None = None,
    ) -> RunHandle:
        """从正式 freeze_scope 后继路径运行固定案例。."""
        ...

    async def list_runs(self) -> list[RunSummary]:
        """读取历史任务摘要。."""
        ...

    async def load_run(self, run_id: str) -> RunBundle:
        """读取已持久化的业务产物。."""
        ...

    async def evaluate_dataset(
        self,
        request: Any,
    ) -> Any:
        """离线评测固定数据集与历史任务绑定。."""
        ...

    async def evaluate_competitive_dataset(
        self,
        request: Any,
    ) -> Any:
        """运行 Bench v2 的竞品分析专用评测。."""
        ...

    async def calibrate_competitive_case(
        self,
        dataset_path: str | Path,
        case_id: str,
    ) -> Any:
        """运行一个固定案例的六变体 Judge 校准。."""
        ...


class RunnerFactory(Protocol):
    """根据已加载的非秘密配置创建 Application Runner。."""

    def __call__(
        self,
        settings: OpenCompeteSettings,
    ) -> ApplicationRunnerProtocol:
        """创建不依赖 Agent Server 的 Runner。."""
        ...


def _default_runner_factory(
    settings: OpenCompeteSettings,
) -> ApplicationRunnerProtocol:
    """延迟导入应用层，使帮助命令不加载 Graph 或持久化实现。."""
    from opencompete.application import ApplicationRunner

    return ApplicationRunner(settings, progress_sink=_CliProgress(console))


runner_factory: RunnerFactory = _default_runner_factory


_LEVEL_LABELS = {
    AnalysisLevel.PRODUCT_SERVICE: "产品与服务",
    AnalysisLevel.COMPANY_ORGANIZATION: "公司与组织",
    AnalysisLevel.INDUSTRY_MARKET: "产业与市场",
}

_STATUS_LABELS = {
    RunStatus.DRAFT: "草稿",
    RunStatus.AWAITING_CONFIRMATION: "等待确认",
    RunStatus.SCOPE_FROZEN: "范围已冻结",
    RunStatus.RESEARCHING: "研究中",
    RunStatus.SYNTHESIZING: "报告生成中",
    RunStatus.COMPLETED: "已完成",
    RunStatus.PARTIAL: "部分完成",
    RunStatus.FAILED: "失败",
    RunStatus.CANCELLED: "已取消",
}

_NODE_LABELS = {
    "discovery": "发现候选竞品",
    "write_research_brief": "生成研究简报",
    "research_supervisor": "规划研究任务",
    "supervisor": "规划研究任务",
    "researcher": "搜索与证据收集",
    "compress_research": "整理研究证据",
    "final_report_generation": "生成最终报告",
    "materialize": "校验并发布报告",
}


class _CliProgress:
    """把应用事件转换为稳定的两层中文终端反馈。."""

    def __init__(self, output: Console) -> None:
        """绑定输出终端并记住最近阶段，避免重复刷屏。."""
        self._output = output
        self._last_node: str | None = None
        self._last_model_context: tuple[str, str, bool] | None = None

    def __call__(self, event: dict[str, object]) -> None:
        """展示阶段、重试动作和脱敏异常摘要。."""
        event_type = event.get("event")
        if event_type == "call_attempt_started":
            node = event.get("node")
            if isinstance(node, str) and node != self._last_node:
                self._last_node = node
                label = _NODE_LABELS.get(node, node)
                self._output.print(Text(f"当前阶段：{label}", style="cyan"))
            provider = event.get("provider")
            model = event.get("model")
            fallback = bool(event.get("fallback"))
            if isinstance(provider, str) and isinstance(model, str):
                current = (provider, model, fallback)
                if current != self._last_model_context:
                    self._last_model_context = current
                    role = "备用" if fallback else "主"
                    self._output.print(
                        Text(f"当前模型：{provider} / {model}（{role}）", style="dim")
                    )
            return
        if event_type != "model_retry_scheduled":
            return
        next_attempt = event.get("next_attempt", "?")
        total = event.get("total_attempts", "?")
        delay = event.get("delay_seconds", 0)
        switching = bool(event.get("next_fallback")) and not bool(
            event.get("fallback")
        )
        action = "正在切换备用模型" if switching else "正在重新连接"
        wait_label = "立即" if delay == 0 else f"{delay} 秒后"
        next_provider = event.get("next_provider")
        next_model = event.get("next_model")
        destination = (
            f"：{next_provider} / {next_model}"
            if isinstance(next_provider, str) and isinstance(next_model, str)
            else ""
        )
        self._output.print(
            Text(
                f"{action} {next_attempt}/{total}（{wait_label}）{destination}",
                style="yellow",
            )
        )
        detail = event.get("error_detail") or event.get("error_message")
        if isinstance(detail, str) and detail.strip():
            self._output.print(Text(detail, style="dim"))


def _display_error(record: ErrorRecord) -> None:
    """展示错误结论和可安全公开的 Provider 排障字段。."""
    show_code = record.exception_type is not None or record.code in {
        "unexpected_error",
        "temporary_provider_error",
        "provider_error",
        "authentication_error",
    }
    prefix = f"错误 [{record.code}]" if show_code else "错误"
    error_console.print(
        Text(f"{prefix}：{record.message}", style="red")
    )
    if record.detail and record.detail != record.message:
        error_console.print(Text(record.detail))
    fields: list[str] = []
    if record.exception_type:
        fields.append(f"异常类型：{record.exception_type}")
    if record.cause_type:
        fields.append(f"底层原因：{record.cause_type}")
    if record.transport_reason:
        fields.append(f"传输原因：{record.transport_reason}")
    if record.http_status:
        fields.append(f"HTTP：{record.http_status}")
    if record.provider_error_code:
        fields.append(f"供应商错误码：{record.provider_error_code}")
    if record.request_id:
        fields.append(f"请求 ID：{record.request_id}")
    if fields:
        error_console.print(Text(" | ".join(fields), style="dim"))


def _localize_argparse_text(text: str) -> str:
    """本地化 argparse 固定生成的界面文字。."""
    replacements = (
        ("usage: ", "用法："),
        ("options:\n", "选项：\n"),
        ("positional arguments:\n", "位置参数：\n"),
        ("show this help message and exit", "显示此帮助并退出"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return text


def _localize_argparse_error(message: str) -> str:
    """本地化常见参数校验错误，不回显任何秘密值。."""
    replacements = (
        ("the following arguments are required:", "缺少必需参数："),
        ("unrecognized arguments:", "无法识别的参数："),
        ("one of the arguments", "必须提供以下参数之一："),
        ("is required", ""),
        ("argument ", "参数 "),
        ("invalid choice:", "无效选项："),
        ("choose from", "可选值"),
    )
    for source, target in replacements:
        message = message.replace(source, target)
    return message.strip()


class _ChineseArgumentParser(argparse.ArgumentParser):
    """为所有主命令和子命令提供一致的中文 argparse 外壳。."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self.add_argument(
            "-h",
            "--help",
            action="help",
            help="显示此帮助并退出",
        )

    def format_help(self) -> str:
        """返回中文标题和帮助说明。."""
        return _localize_argparse_text(super().format_help())

    def format_usage(self) -> str:
        """返回中文用法标题。."""
        return _localize_argparse_text(super().format_usage())

    def error(self, message: str) -> NoReturn:
        """使用中文参数错误前缀并保持 argparse 的退出码语义。."""
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 参数错误：{_localize_argparse_error(message)}\n")


def build_parser() -> argparse.ArgumentParser:
    """构建不依赖 Agent Server 的本地命令解析器。."""
    parser = _ChineseArgumentParser(
        prog="opencompete",
        description="OpenCompete 本地通用竞品分析",
    )
    parser.add_argument(
        "--config",
        type=Path,
        metavar="路径",
        help="非秘密 TOML 配置文件路径",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        metavar="目录",
        help="覆盖本地数据目录",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="命令",
        required=True,
        title="可用命令",
    )

    new_parser = subparsers.add_parser(
        "new",
        help="创建新的竞品分析任务",
        description="创建任务，展示建议研究范围并等待确认。",
    )
    new_parser.add_argument(
        "query",
        nargs="?",
        help="研究问题；省略时交互输入",
    )
    new_parser.add_argument(
        "--entity",
        dest="entities",
        action="append",
        metavar="名称",
        help="研究对象；可重复传入",
    )
    new_parser.add_argument(
        "--level",
        dest="levels",
        action="append",
        choices=[level.value for level in AnalysisLevel],
        metavar="层级",
        help="分析层级；可重复传入，默认包含全部三个层级",
    )
    new_parser.add_argument(
        "--exclude",
        dest="exclusions",
        action="append",
        default=[],
        metavar="排除项",
        help="明确不研究的内容；可重复传入",
    )
    new_parser.add_argument(
        "--url",
        dest="allowed_urls",
        action="append",
        default=[],
        metavar="公开网址",
        help="用户确认允许使用的公开 HTTP/HTTPS 网址；可重复传入",
    )
    new_parser.add_argument(
        "--discover",
        action="store_true",
        help="搜索候选竞品，并在加入研究范围前等待用户选择",
    )

    resume_parser = subparsers.add_parser(
        "resume",
        help="恢复已有任务",
        description="按 run_id 恢复任务；若停在确认点会继续候选选择或范围确认。",
    )
    resume_parser.add_argument("run_id", help="已有任务的 run_id")

    subparsers.add_parser(
        "list",
        help="列出历史任务",
        description="只读取本地长期产物中的任务摘要。",
    )

    show_parser = subparsers.add_parser(
        "show",
        help="查看已保存报告",
        description="按 run_id 读取并展示已持久化的报告。",
    )
    show_parser.add_argument("run_id", help="已有任务的 run_id")

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="离线评测历史报告",
        description="读取固定数据集和历史任务产物，不重新搜索网络。",
    )
    evaluate_parser.add_argument("dataset", type=Path, help="固定数据集文件或目录")
    target_group = evaluate_parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--targets",
        type=Path,
        metavar="JSON",
        help="批量案例与 run_id 绑定文件",
    )
    target_group.add_argument(
        "--case",
        dest="case_id",
        metavar="案例ID",
        help="单个 Benchmark 案例 ID，必须同时提供 --run",
    )
    evaluate_parser.add_argument(
        "--run",
        dest="run_id",
        metavar="RUN_ID",
        help="单项评测的历史 run_id，只能与 --case 同时使用",
    )

    benchmark_parser = subparsers.add_parser(
        "benchmark-run",
        help="运行固定 Scope 的真实竞品研究案例",
        description=(
            "加载 OpenCompete Bench v2 案例，跳过候选发现和 UX 确认，"
            "从正式 freeze_scope 后继路径运行。"
        ),
    )
    benchmark_parser.add_argument("dataset", type=Path, help="Bench v2 数据集目录")
    benchmark_parser.add_argument(
        "--case",
        dest="case_id",
        required=True,
        metavar="案例ID",
        help="要运行的固定案例 ID",
    )
    benchmark_parser.add_argument(
        "--allow-authored",
        action="store_true",
        help="仅用于校准：允许尚未完成独立复核的专家作者版本",
    )

    competitive_parser = subparsers.add_parser(
        "evaluate-competitive",
        help="运行 Bench v2 竞品分析专用评测",
        description="读取固定 v2 案例与历史 RunBundle，运行双顺序 Judge 和 FACT。",
    )
    competitive_parser.add_argument(
        "dataset",
        type=Path,
        help="Bench v2 数据集目录",
    )
    competitive_group = competitive_parser.add_mutually_exclusive_group(required=True)
    competitive_group.add_argument(
        "--targets",
        type=Path,
        metavar="JSON",
        help="批量案例与 run_id 绑定文件",
    )
    competitive_group.add_argument(
        "--case",
        dest="case_id",
        metavar="案例ID",
        help="单个 v2 案例 ID，必须同时提供 --run",
    )
    competitive_parser.add_argument(
        "--run",
        dest="run_id",
        metavar="RUN_ID",
        help="单项评测的历史 run_id",
    )

    calibration_parser = subparsers.add_parser(
        "calibrate-competitive",
        help="运行 Bench v2.2 六变体 Judge 校准",
        description="使用同一匿名双顺序 Judge 校准五种单缺陷和纯文风变体。",
    )
    calibration_parser.add_argument(
        "dataset",
        type=Path,
        help="Bench v2.2 数据集目录",
    )
    calibration_parser.add_argument(
        "--case",
        dest="case_id",
        required=True,
        metavar="案例ID",
        help="要校准的固定案例 ID",
    )
    competitive_parser.add_argument(
        "--allow-authored",
        action="store_true",
        help="仅用于校准：允许尚未完成独立复核的 Reference",
    )

    return parser


def _required_console_input(prompt: str) -> str:
    """交互读取一个不可为空的值。."""
    while True:
        value = console.input(prompt).strip()
        if value:
            return value
        error_console.print("[red]输入不能为空，请重新输入。[/red]")


def _split_entity_names(values: Sequence[str]) -> tuple[str, ...]:
    """展开中英文逗号并保持用户给定的对象顺序。."""
    names: list[str] = []
    for value in values:
        for name in value.replace("，", ",").split(","):
            normalized = name.strip()
            if normalized and normalized not in names:
                names.append(normalized)
    return tuple(names)


def _build_research_request(args: argparse.Namespace) -> ResearchRequest:
    """把 new 参数和交互输入转换为领域请求。."""
    query = args.query or _required_console_input("请输入竞品分析问题：")
    raw_entities = args.entities or [
        _required_console_input("请输入研究对象（多个对象用逗号分隔）：")
    ]
    entity_names = _split_entity_names(raw_entities)
    if not entity_names:
        raise DataValidationError("至少需要一个研究对象")
    levels = (
        tuple(AnalysisLevel(value) for value in args.levels)
        if args.levels
        else tuple(AnalysisLevel)
    )
    return ResearchRequest(
        query=query,
        entities=tuple(ResearchEntity(name=name) for name in entity_names),
        levels=levels,
        exclusions=tuple(args.exclusions),
        allowed_urls=tuple(args.allowed_urls),
        discover_candidates=bool(args.discover),
    )


def _display_discovery_candidates(
    candidates: Sequence[DiscoveryCandidate],
) -> None:
    """展示候选对象及其可核验发现来源。."""
    console.print("候选名称来自网页标题；添加前请核对，并在下一步确认或改名。")
    table = Table(title="发现的候选竞品")
    table.add_column("序号", justify="right", no_wrap=True)
    table.add_column("候选对象")
    table.add_column("发现来源")
    table.add_column("摘要")
    for index, candidate in enumerate(candidates, start=1):
        source = f"{candidate.source_title}\n{candidate.source_url}"
        table.add_row(
            str(index),
            candidate.entity.name,
            source,
            candidate.summary or "无",
        )
    console.print(table)


def _ask_candidate_selection(
    candidates: Sequence[DiscoveryCandidate],
    revision: int,
) -> CandidateSelection:
    """把用户选择转换为稳定候选 ID；未选择项即为明确拒绝。."""
    rejected_values = {"0", "无", "none", "全部拒绝"}
    all_values = {"all", "全部", "全部添加"}
    while True:
        answer = console.input(
            "请输入要添加的候选序号（逗号分隔；all 全部添加；0 全部拒绝）："
        ).strip()
        if answer.casefold() in rejected_values:
            console.print(f"已拒绝全部 {len(candidates)} 个候选。")
            return CandidateSelection(expected_revision=revision)
        if answer.casefold() in all_values:
            indexes = tuple(range(1, len(candidates) + 1))
        else:
            try:
                indexes = tuple(
                    dict.fromkeys(
                        int(value.strip())
                        for value in answer.replace("，", ",").split(",")
                        if value.strip()
                    )
                )
            except ValueError:
                indexes = ()
        if indexes and all(1 <= index <= len(candidates) for index in indexes):
            choices: list[CandidateChoice] = []
            for index in indexes:
                candidate = candidates[index - 1]
                renamed = ""
                while not renamed:
                    renamed = console.input(
                        f"请输入候选 {index} 的最终对象名称"
                        f"（网页标题仅供参考：“{candidate.entity.name}”）："
                    ).strip()
                    if not renamed:
                        error_console.print(
                            "[red]对象名称必须由用户明确确认，不能直接采用网页标题。[/red]"
                        )
                choices.append(
                    CandidateChoice(
                        candidate_id=candidate.candidate_id,
                        entity_name=renamed,
                    )
                )
            console.print(
                f"已添加 {len(choices)} 个候选，"
                f"已拒绝 {len(candidates) - len(choices)} 个候选。"
            )
            return CandidateSelection(
                expected_revision=revision,
                selected_candidates=tuple(choices),
            )
        error_console.print("[red]请输入表格中的序号，或输入 0 全部拒绝。[/red]")


def _display_scope(scope: ScopeSpec) -> None:
    """以确定顺序展示待确认范围。."""
    table = Table(title="建议研究范围", show_header=False)
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("内容")
    table.add_row(
        "研究对象",
        Text("、".join(entity.name for entity in scope.entities)),
    )
    table.add_row(
        "分析层级",
        Text("、".join(_LEVEL_LABELS[level] for level in scope.levels)),
    )
    for level in scope.levels:
        table.add_row(
            f"{_LEVEL_LABELS[level]}维度",
            Text("、".join(scope.dimensions[level])),
        )
    exclusions = "、".join(scope.exclusions) if scope.exclusions else "无"
    table.add_row("排除项", Text(exclusions))
    table.add_row("范围版本", str(scope.revision))
    console.print(table)


def _ask_scope_confirmation() -> bool:
    """只接受明确的确认或拒绝，避免误启动外部调用。."""
    accepted_values = {"y", "yes", "是", "确认"}
    rejected_values = {"n", "no", "否", "拒绝", "取消"}
    while True:
        answer = console.input("确认该研究范围并开始研究吗？[y/n]：").strip().casefold()
        if answer in accepted_values:
            return True
        if answer in rejected_values:
            return False
        error_console.print("[red]请输入 y/是 或 n/否。[/red]")


def _display_handle(handle: RunHandle) -> None:
    """展示一次应用操作后的中文状态与可用缺口。."""
    console.print(
        Text.assemble(
            "任务 ",
            (handle.run_id, "bold"),
            f"：{_STATUS_LABELS[handle.status]}",
        )
    )
    if handle.bundle is not None and handle.bundle.report.limitations:
        console.print("已知缺口：")
        for limitation in handle.bundle.report.limitations:
            console.print(Text(f"- {limitation}"))


async def _finish_pending_confirmation(
    runner: ApplicationRunnerProtocol,
    handle: RunHandle,
) -> RunHandle:
    """如处于 durable interrupt，则把用户决定交回同一任务。."""
    current = handle
    _display_handle(current)
    while current.pending_confirmation:
        if current.scope is None:
            raise DataValidationError("任务等待确认，但没有可展示的研究范围")
        if current.confirmation_kind is ConfirmationKind.CANDIDATE_SELECTION:
            if not current.discovery_candidates:
                raise DataValidationError("任务等待候选确认，但没有可展示的候选")
            _display_discovery_candidates(current.discovery_candidates)
            user_input: CandidateSelection | ScopeConfirmation = (
                _ask_candidate_selection(
                    current.discovery_candidates,
                    current.scope.revision,
                )
            )
        else:
            _display_scope(current.scope)
            user_input = ScopeConfirmation(
                accepted=_ask_scope_confirmation(),
                expected_revision=current.scope.revision,
            )
        current = await runner.resume_run(current.run_id, user_input=user_input)
        _display_handle(current)
    return current


def _display_run_list(summaries: Sequence[RunSummary]) -> None:
    """展示长期产物中的任务摘要。."""
    if not summaries:
        console.print("暂无历史任务。")
        return
    table = Table(title="OpenCompete 历史任务")
    table.add_column("run_id")
    table.add_column("状态")
    table.add_column("范围版本", justify="right")
    table.add_column("产物版本", justify="right")
    table.add_column("更新时间")
    for summary in summaries:
        table.add_row(
            Text(summary.run_id),
            _STATUS_LABELS[summary.status],
            str(summary.scope_revision),
            str(summary.artifact_revision),
            summary.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        )
    console.print(table)
    if any(len(summary.run_id) > 24 for summary in summaries):
        console.print("完整任务编号：")
        for summary in summaries:
            console.print(Text(f"- {summary.run_id}"))


def _display_bundle(bundle: RunBundle) -> None:
    """展示已验证的长期报告，不触发研究。."""
    console.print(
        Text.assemble(
            "任务 ",
            (bundle.run_id, "bold"),
            f"：{_STATUS_LABELS[bundle.status]}",
        )
    )
    console.print(bundle.report.markdown, markup=False)
    if bundle.report.limitations:
        console.print("已知缺口：")
        for limitation in bundle.report.limitations:
            console.print(Text(f"- {limitation}"))


def _build_evaluation_request(args: argparse.Namespace) -> EvaluationRequest:
    """构造批量或单项离线评测请求。."""
    from opencompete.evaluation import (
        DatasetValidationError,
        EvaluationRequest,
        EvaluationTarget,
        load_targets,
    )

    if args.targets is not None:
        if args.run_id is not None:
            raise DataValidationError("--run 只能与 --case 同时使用")
        try:
            targets = load_targets(args.targets)
        except DatasetValidationError as exc:
            raise DataValidationError(f"targets 文件无效：{args.targets}") from exc
    else:
        if args.case_id is None or args.run_id is None:
            raise DataValidationError("单项评测必须同时提供 --case 和 --run")
        targets = (
            EvaluationTarget(
                case_id=args.case_id,
                run_id=args.run_id,
            ),
        )
    return EvaluationRequest(dataset=args.dataset, targets=targets)


def _build_competitive_evaluation_request(args: argparse.Namespace) -> Any:
    """构造 Bench v2 的单项或批量固定绑定。."""
    from opencompete.evaluation import load_targets
    from opencompete.evaluation.competitive_models import (
        CompetitiveEvaluationRequest,
    )
    from opencompete.evaluation.models import EvaluationTarget

    if args.targets is not None:
        if args.run_id is not None:
            raise DataValidationError("--run 只能与 --case 同时使用")
        targets = load_targets(args.targets)
    else:
        if args.case_id is None or args.run_id is None:
            raise DataValidationError("单项 v2 评测必须同时提供 --case 和 --run")
        targets = (
            EvaluationTarget(case_id=args.case_id, run_id=args.run_id),
        )
    return CompetitiveEvaluationRequest(
        dataset=args.dataset,
        targets=targets,
        allow_authored=bool(args.allow_authored),
    )


async def _dispatch(
    args: argparse.Namespace,
    runner: ApplicationRunnerProtocol,
) -> None:
    """把已解析命令分派到唯一的应用层入口。."""
    if args.command == "new":
        handle = await runner.create_run(_build_research_request(args))
        await _finish_pending_confirmation(runner, handle)
        return
    if args.command == "resume":
        handle = await runner.resume_run(args.run_id)
        await _finish_pending_confirmation(runner, handle)
        return
    if args.command == "list":
        _display_run_list(await runner.list_runs())
        return
    if args.command == "show":
        _display_bundle(await runner.load_run(args.run_id))
        return
    if args.command == "evaluate":
        await runner.evaluate_dataset(_build_evaluation_request(args))
        console.print("评测已完成，结果已保存。")
        return
    if args.command == "benchmark-run":
        from opencompete.evaluation.competitive_dataset import (
            load_competitive_dataset,
        )
        from opencompete.models import utc_now

        dataset = load_competitive_dataset(
            args.dataset,
            allow_authored=bool(args.allow_authored),
        )
        try:
            case = dataset.get_case(args.case_id)
        except KeyError as exc:
            raise DataValidationError(f"固定案例不存在：{args.case_id}") from exc
        frozen_scope = case.expected_scope.model_copy(
            update={
                "confirmed_at": utc_now(),
                "revision": 1,
                "frozen": True,
            }
        )
        benchmark_request = case.research_request.model_copy(
            update={"query": case.normalized_task_text}
        )
        handle = await runner.create_fixed_scope_run(
            benchmark_request,
            frozen_scope,
            benchmark_case_id=case.case_id,
        )
        _display_handle(handle)
        return
    if args.command == "evaluate-competitive":
        await runner.evaluate_competitive_dataset(
            _build_competitive_evaluation_request(args)
        )
        console.print("Bench v3 评测已完成，结果已保存。")
        return
    if args.command == "calibrate-competitive":
        result = await runner.calibrate_competitive_case(
            args.dataset,
            args.case_id,
        )
        console.print(
            f"Bench v2.2 校准完成：缺陷通过率 {result.defect_pass_rate:.0%}，"
            f"纯文风={'通过' if result.style_only_passed else '失败'}。"
        )
        return
    raise DataValidationError(f"不支持的命令：{args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    """解析参数并在本地进程中运行 Application Runner。."""
    parser = build_parser()
    args = parser.parse_args(argv)
    overrides = {"data_dir": args.data_dir} if args.data_dir is not None else None
    try:
        settings = load_settings(config_path=args.config, cli_overrides=overrides)
        runner = runner_factory(settings)
        asyncio.run(_dispatch(args, runner))
    except OpenCompeteError as exc:
        _display_error(sanitized_error(exc))
        _display_resume_hint(args)
        return 1
    except ValidationError:
        error_console.print("[red]错误：输入不符合数据契约，请检查参数后重试。[/red]")
        return 2
    except (EOFError, KeyboardInterrupt):
        error_console.print("[yellow]当前命令已中止；已创建的任务可稍后恢复。[/yellow]")
        return 130
    except Exception as exc:
        _display_error(sanitized_error(exc))
        _display_resume_hint(args)
        return 1
    return 0


def _display_resume_hint(args: argparse.Namespace) -> None:
    """失败任务若有已知 ID，则明确告诉用户如何继续。."""
    run_id = getattr(args, "run_id", None)
    if args.command not in {"resume", "show"} or not isinstance(run_id, str):
        return
    command = "uv run opencompete"
    config_path = getattr(args, "config", None)
    if config_path is not None:
        command += f" --config {config_path}"
    error_console.print("任务状态已保存在本地。稍后可执行：")
    error_console.print(Text(f"{command} resume {run_id}", style="bold"))


if __name__ == "__main__":
    raise SystemExit(main())
