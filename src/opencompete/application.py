"""OpenCompete 本地应用生命周期编排。."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import weakref
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import ValidationError

from open_deep_research.utils import model_connection_context
from opencompete.analysis import freeze_scope as freeze_scope_spec
from opencompete.budget import BudgetGuard, BudgetLedger
from opencompete.callbacks import BudgetCallback
from opencompete.connections import runtime_model_connections
from opencompete.errors import (
    BudgetExceededError,
    DataValidationError,
    PersistenceError,
)
from opencompete.extractors import (
    ArtifactExtraction,
    ArtifactExtractor,
    LangChainArtifactExtractor,
    StaticArtifactExtractor,
)
from opencompete.materialize import materialize_bundle
from opencompete.models import (
    BudgetEstimate,
    BudgetSnapshot,
    CandidateSelection,
    ConfirmationKind,
    DiscoveryCandidate,
    ResearchCommission,
    ResearchRequest,
    RunBundle,
    RunHandle,
    RunManifest,
    RunStatus,
    RunSummary,
    ScopeConfirmation,
    ScopeSpec,
    utc_now,
)
from opencompete.persistence import (
    CheckpointSnapshot,
    PublishedRun,
    ReconcileAction,
    RunRepository,
    SqliteCheckpointStore,
    SqliteExecutionLedger,
    reconcile,
)
from opencompete.providers import DiscoveryProvider, LazyLlmDiscoveryProvider
from opencompete.research_contracts import estimated_report_output_tokens
from opencompete.scope_planning import LazyScopePlanner, ScopePlanner
from opencompete.security import sanitized_error, validate_public_http_url
from opencompete.settings import OpenCompeteSettings, thinking_disable_options

if TYPE_CHECKING:
    from opencompete.evaluation.competitive_models import (
        CompetitiveCalibrationResult,
        CompetitiveEvaluationRequest,
    )
    from opencompete.evaluation.models import (
        EvaluationBatchResult,
        EvaluationRequest,
    )

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommissionRunStartup:
    """两段式创建结果：draft 句柄与待启动的后台执行协程。."""

    handle: RunHandle
    execution: Coroutine[Any, Any, RunHandle]


_PUBLISHED_STATUSES = {RunStatus.COMPLETED, RunStatus.PARTIAL}
_STATUS_NAMES = {
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
_RUN_LOCKS_GUARD = threading.Lock()
_RUN_LOCKS_BY_LOOP: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    dict[str, asyncio.Lock],
] = weakref.WeakKeyDictionary()
_RESUME_RUNTIME_FIELDS = (
    "research_model",
    "summarization_model",
    "compression_model",
    "final_report_model",
    "fallback_model",
    "primary_connection_format",
    "fallback_connection_format",
    "primary_provider_name",
    "fallback_provider_name",
    "primary_model_attempts",
    "fallback_model_attempts",
    "model_retry_delays_seconds",
    "research_model_max_tokens",
    "summarization_model_max_tokens",
    "compression_model_max_tokens",
    "final_report_model_max_tokens",
    "artifact_extraction_model_max_tokens",
    "final_report_input_max_chars",
    "llm_request_timeout_seconds",
    "llm_sdk_max_retries",
)


class ApplicationGraph(Protocol):
    """Runner 使用的最小 LangGraph 编译产物接口。."""

    async def ainvoke(
        self,
        value: object,
        config: RunnableConfig | None = None,
    ) -> Mapping[str, Any]:
        """执行或恢复任务。."""

    async def aget_state(self, config: RunnableConfig) -> object:
        """读取同一 thread_id 的 durable 状态。."""

    async def aupdate_state(
        self,
        config: RunnableConfig,
        values: Mapping[str, Any],
        *,
        as_node: str,
    ) -> RunnableConfig:
        """在正式节点边界写入固定状态并返回新的 durable 配置。."""


GraphFactory = Callable[..., ApplicationGraph]
ProgressSink = Callable[[dict[str, object]], object]


@dataclass(frozen=True)
class RunInspection:
    """只读 Checkpoint 检查结果，不承担任何恢复或索引写入职责。."""

    handle: RunHandle
    request: ResearchRequest | None
    has_confirmation_interrupt: bool
    recoverable: bool


class InitializableBudgetLedger(BudgetLedger, Protocol):
    """生产 SQLite Ledger 在首次使用前还需要建表。."""

    async def initialize(self) -> None:
        """初始化 durable ledger。."""


class EvaluationServiceProtocol(Protocol):
    """Application 对评测实现依赖的最小边界。."""

    async def evaluate(
        self,
        request: EvaluationRequest,
    ) -> EvaluationBatchResult:
        """只读取固定数据集与历史产物运行评测。."""


class ApplicationRunner:
    """组装 Graph、Checkpoint、预算账本和长期业务产物。."""

    def __init__(
        self,
        settings: OpenCompeteSettings,
        *,
        repository: RunRepository | None = None,
        checkpoint_store: SqliteCheckpointStore | None = None,
        execution_ledger: InitializableBudgetLedger | None = None,
        graph_factory: GraphFactory | None = None,
        research_graph: object | None = None,
        extractor: ArtifactExtractor | None = None,
        discovery_provider: DiscoveryProvider | None = None,
        scope_planner: ScopePlanner | None = None,
        evaluation_service: EvaluationServiceProtocol | None = None,
        progress_sink: ProgressSink | None = None,
    ) -> None:
        """绑定本地运行时；Saver 仍在每次命令内打开和关闭。."""
        self.settings = settings
        self._default_settings_revision = 0
        database_path = Path(settings.data_dir) / "checkpoints.sqlite3"
        self._repository = repository or RunRepository(settings.data_dir)
        self._checkpoint_store = checkpoint_store or SqliteCheckpointStore(
            database_path
        )
        self._execution_ledger = execution_ledger or SqliteExecutionLedger(
            database_path
        )
        if graph_factory is None:
            from opencompete.graph import build_graph

            graph_factory = cast(GraphFactory, build_graph)
        self._graph_factory = graph_factory
        self._research_graph = research_graph
        self._extractor_override = extractor
        self._discovery_provider_override = discovery_provider
        self._scope_planner_override = scope_planner
        self._evaluation_service = evaluation_service
        self._progress_sink = progress_sink

    def reconfigure_default_settings(
        self,
        settings: OpenCompeteSettings,
        *,
        revision: int,
    ) -> None:
        """替换仅供后续新任务使用的默认设置。."""
        if settings.data_dir != self.settings.data_dir:
            raise DataValidationError("运行中的服务不允许通过设置页变更数据目录")
        self.settings = settings
        self._default_settings_revision = revision

    async def create_run(self, request: ResearchRequest) -> RunHandle:
        """兼容 CLI 与旧调用方：用已规划的 ResearchRequest 创建任务。."""
        return await self._create_graph_run(
            {"request": request.model_dump(mode="json")}
        )

    async def create_commission_run(
        self,
        commission: ResearchCommission,
    ) -> RunHandle:
        """为 Web 原始委托动态规划 Scope 后执行到第一个确认点。."""
        return await self._create_graph_run(
            {"commission": commission.model_dump(mode="json")}
        )

    async def initialize_commission_run(
        self,
        commission: ResearchCommission,
    ) -> CommissionRunStartup:
        """初始化任务但不执行；返回 draft 句柄与后台执行协程。.

        供 API 层 202 异步创建使用：轻量初始化（分配 run_id、写 manifest
        与 ``run_created`` 事件）在调用方完成，模型范围规划与候选发现由
        ``execution`` 协程承担。调用方负责启动协程；放弃启动时必须
        ``close()`` 协程。
        """
        initial_input = {"commission": commission.model_dump(mode="json")}
        run_id = self._initialize_graph_run(initial_input)
        # 202 返回前完成全部建表 DDL：后台任务与随后的详情轮询各自打开
        # 新 SQLite 连接，不能把 executescript 留给并发窗口。
        await self._execution_ledger.initialize()
        async with self._checkpoint_store.open_saver():
            # 触发官方 Saver 的 checkpoint 表创建后立即释放连接。
            pass
        handle = RunHandle(run_id=run_id, status=RunStatus.DRAFT)
        return CommissionRunStartup(
            handle=handle,
            execution=self._execute_initial_graph_run(run_id, initial_input),
        )

    def _initialize_graph_run(self, initial_input: Mapping[str, Any]) -> str:
        """同步初始化 manifest 与 run_created 事件，返回 run_id。."""
        del initial_input  # 输入只在执行期进入 Graph
        run_id = str(uuid4())
        settings = self.settings
        limit = self._budget_limit(settings)
        initial_budget = BudgetSnapshot(limit=limit, remaining=limit)
        manifest = RunManifest(
            run_id=run_id,
            status=RunStatus.DRAFT,
            scope_revision=0,
            artifact_revision=0,
            config_snapshot=self._config_snapshot(settings),
            budget_summary=initial_budget,
        )
        self._repository.initialize(manifest)
        self._append_event(run_id, "run_created", status=RunStatus.DRAFT)
        return run_id

    async def _execute_initial_graph_run(
        self,
        run_id: str,
        initial_input: Mapping[str, Any],
    ) -> RunHandle:
        """把单一入口输入交给外层 Scope Graph，执行到第一个确认点。."""
        lock = self._lock_for(run_id)
        async with lock:
            try:
                await self._execution_ledger.initialize()
                result = await self._invoke_graph(
                    run_id,
                    {
                        "run_id": run_id,
                        **initial_input,
                    },
                )
                return await self._finalize_state(run_id, result)
            except Exception as exc:
                await self._record_failure(run_id, exc)
                raise

    async def _create_graph_run(
        self,
        initial_input: Mapping[str, Any],
    ) -> RunHandle:
        """初始化一次 Run，并把单一入口输入交给外层 Scope Graph。."""
        run_id = self._initialize_graph_run(initial_input)
        return await self._execute_initial_graph_run(run_id, initial_input)

    async def create_fixed_scope_run(
        self,
        request: ResearchRequest,
        scope: ScopeSpec,
        *,
        benchmark_case_id: str | None = None,
    ) -> RunHandle:
        """跳过发现和 UX 确认，从正式 ``freeze_scope`` 后继路径运行。."""
        _validate_fixed_scope_input(request, scope)
        run_id = str(uuid4())
        settings = self.settings
        limit = self._budget_limit(settings)
        initial_budget = BudgetSnapshot(limit=limit, remaining=limit)
        manifest = RunManifest(
            run_id=run_id,
            status=RunStatus.DRAFT,
            scope_revision=0,
            artifact_revision=0,
            config_snapshot={
                **self._config_snapshot(settings),
                "entry_mode": "fixed_scope_after_freeze_scope",
                "benchmark_case_id": benchmark_case_id,
                "task_input_sha256": hashlib.sha256(
                    request.query.encode("utf-8")
                ).hexdigest(),
            },
            budget_summary=initial_budget,
        )
        self._repository.initialize(manifest)
        self._append_event(
            run_id,
            "run_created",
            status=RunStatus.DRAFT,
            mode="fixed_scope",
            benchmark_case_id=benchmark_case_id,
        )

        lock = self._lock_for(run_id)
        async with lock:
            try:
                await self._execution_ledger.initialize()
                result = await self._invoke_graph_from_fixed_scope(
                    run_id,
                    request,
                    scope,
                )
                return await self._finalize_state(run_id, result)
            except Exception as exc:
                await self._record_failure(run_id, exc)
                raise

    async def resume_run(
        self,
        run_id: str,
        user_input: CandidateSelection | ScopeConfirmation | None = None,
    ) -> RunHandle:
        """恢复同一 thread_id；确认点无输入时只返回待确认范围。."""
        # 注意：不在此处拦截 CANCELLED manifest——对账语义允许存活的
        # awaiting checkpoint 覆盖取消标记（checkpoint 是真相源）；
        # HTTP 层的取消终态拒绝由 API 路由负责。
        lock = self._lock_for(run_id)
        async with lock:
            prepared = await self._prepare_resume(run_id)
            if isinstance(prepared, RunHandle):
                return prepared

            try:
                await self._execution_ledger.initialize()
                inspection = await self.inspect_run(run_id)
                scope = inspection.handle.scope
                if (
                    isinstance(user_input, ScopeConfirmation)
                    and user_input.accepted
                    and scope is not None
                ):
                    scope = freeze_scope_spec(scope, user_input)
                return await self._resume_graph(
                    run_id,
                    user_input,
                    scope=scope,
                    settings=self._resume_settings_for_run(run_id),
                    resumed_with_current_settings=True,
                )
            except Exception as exc:
                await self._record_failure(run_id, exc)
                raise

    async def _prepare_resume(self, run_id: str) -> RunManifest | RunHandle:
        """按 Checkpoint 与发布产物的确定性对账结果准备恢复索引。."""
        checkpoint, recovered = await self._checkpoint_recovery_state(run_id)
        artifacts = self._repository.inspect(run_id)
        try:
            readable_manifest = self._repository.load_manifest(run_id)
        except PersistenceError:
            readable_manifest = None
        published_artifacts = artifacts
        # 不可解析或尚未发布且不超前的索引不属于正式业务产物。
        if checkpoint is not None and (
            readable_manifest is None
            or (
                not readable_manifest.file_hashes
                and readable_manifest.scope_revision
                <= checkpoint.scope_revision
                and readable_manifest.artifact_revision
                <= checkpoint.artifact_revision
            )
        ):
            published_artifacts = None
        decision = reconcile(checkpoint, published_artifacts)

        if decision.action in {
            ReconcileAction.NOOP,
            ReconcileAction.HISTORY_ONLY,
        }:
            published = self._repository.load_verified(run_id)
            return self._handle_from_published(run_id, published)
        if decision.action in {
            ReconcileAction.CONTINUE_GRAPH,
            ReconcileAction.REBUILD_ARTIFACTS,
        }:
            assert recovered is not None
            manifest = await self._load_or_rebuild_manifest(run_id, recovered)
            return self._replace_manifest_from_checkpoint(manifest, recovered)
        if decision.action is ReconcileAction.CONFLICT:
            raise PersistenceError(f"任务 {run_id} 恢复对账冲突：{decision.reason}")
        raise PersistenceError(f"任务 {run_id} 无法恢复：{decision.reason}")

    def _handle_from_published(
        self, run_id: str, published: PublishedRun
    ) -> RunHandle:
        """把已发布业务产物投影为运行句柄（纯本地只读）。."""
        return RunHandle(
            run_id=run_id,
            status=published.bundle.status,
            scope=published.bundle.scope,
            bundle=published.bundle,
        )

    async def inspect_run(self, run_id: str) -> RunInspection:
        """只读检查同一运行的 Checkpoint，绝不恢复、推进或写入状态。."""
        settings = self._settings_for_run(run_id)
        budget_guard = self._budget_guard(run_id, settings=settings)
        config = self._runnable_config(run_id, budget_guard, settings=settings)
        async with self._checkpoint_store.open_saver() as saver:
            graph = self._build_graph(saver, budget_guard, settings=settings)
            snapshot = await graph.aget_state(config)
        values = _state_values(snapshot)
        if not values:
            raise PersistenceError(f"任务 {run_id} 没有可恢复的 Checkpoint")
        status = _state_status(values)
        scope = _state_scope(values)
        request = _state_request(values)
        has_confirmation_interrupt = bool(_state_interrupts(snapshot))
        pending_confirmation = (
            status is RunStatus.AWAITING_CONFIRMATION
            and has_confirmation_interrupt
        )
        terminal = status in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        handle = RunHandle(
            run_id=run_id,
            status=status,
            scope=scope,
            pending_confirmation=pending_confirmation,
            confirmation_kind=(
                _state_confirmation_kind(values)
                if pending_confirmation
                else None
            ),
            discovery_candidates=(
                _state_discovery_candidates(values)
                if pending_confirmation
                else ()
            ),
        )
        return RunInspection(
            handle=handle,
            request=request,
            has_confirmation_interrupt=has_confirmation_interrupt,
            recoverable=not terminal and not has_confirmation_interrupt,
        )

    async def project_confirmation(self, run_id: str) -> RunHandle:
        """兼容旧调用方的确认点只读投影。."""
        return (await self.inspect_run(run_id)).handle

    async def freeze_confirmed_scope(
        self,
        run_id: str,
        confirmation: ScopeConfirmation,
    ) -> RunHandle:
        """先耐久冻结用户确认的 Scope，再返回可展示的中间态句柄。."""
        lock = self._lock_for(run_id)
        async with lock:
            inspection = await self.inspect_run(run_id)
            handle = inspection.handle
            if (
                not inspection.has_confirmation_interrupt
                or not handle.pending_confirmation
                or handle.confirmation_kind
                is not ConfirmationKind.SCOPE_CONFIRMATION
            ):
                raise DataValidationError("当前任务不在可提交的范围确认点")
            if handle.scope is None or inspection.request is None:
                raise DataValidationError("范围确认的 Checkpoint 缺少 Scope 或请求")
            frozen = freeze_scope_spec(handle.scope, confirmation)
            frozen_request = inspection.request.model_copy(
                update={
                    "entities": frozen.entities,
                    "levels": frozen.levels,
                    "dimensions": dict(frozen.dimensions),
                    "exclusions": frozen.exclusions,
                }
            )
            await self._execution_ledger.initialize()
            settings = self._settings_for_run(run_id)
            budget_guard = self._budget_guard(run_id, settings=settings)
            config = self._runnable_config(
                run_id, budget_guard, settings=settings, scope=frozen
            )
            async with self._checkpoint_store.open_saver() as saver:
                graph = self._build_graph(saver, budget_guard, settings=settings)
                updated = await graph.aupdate_state(
                    config,
                    {
                        "run_id": run_id,
                        "request": frozen_request.model_dump(mode="json"),
                        "scope": frozen.model_dump(mode="json"),
                        "status": RunStatus.SCOPE_FROZEN.value,
                    },
                    as_node="freeze_scope",
                )
                durable_config = _merge_updated_graph_config(config, updated)
                snapshot = await graph.aget_state(durable_config)
            values = _state_values(snapshot)
            if not values:
                raise PersistenceError("冻结范围后无法读取 durable Checkpoint")
            durable_status = _state_status(values)
            durable_scope = _state_scope(values)
            if durable_status is not RunStatus.SCOPE_FROZEN or durable_scope != frozen:
                raise PersistenceError("冻结范围后的 Checkpoint 状态与确认内容不一致")
            return await self._finalize_state(run_id, values)

    async def resume_execution(self, run_id: str) -> RunHandle:
        """显式继续非确认点的 durable 研究执行。."""
        return await self.resume_run(run_id)

    async def purge_checkpoint(self, run_id: str) -> None:
        """删除该 run 的 durable Checkpoint thread（用户显式删除任务）。."""
        async with self._checkpoint_store.open_saver() as saver:
            await saver.adelete_thread(run_id)

    async def _load_or_rebuild_manifest(
        self,
        run_id: str,
        recovered: RunManifest | None = None,
    ) -> RunManifest:
        """优先读取索引；不可读时只允许依据同 thread Checkpoint 重建。."""
        try:
            return self._repository.load_manifest(run_id)
        except PersistenceError as manifest_error:
            await self._execution_ledger.initialize()
            try:
                if recovered is None:
                    recovered = await self._manifest_from_checkpoint(run_id)
                self._repository.rebuild_manifest_index(recovered)
            except Exception as exc:
                raise PersistenceError(
                    f"任务 {run_id} 的 Manifest 无法读取，且 Checkpoint 无法恢复"
                ) from exc
            self._append_event(
                run_id,
                "manifest_rebuilt_from_checkpoint",
                error_code=manifest_error.code,
            )
            return self._repository.load_manifest(run_id)

    async def _manifest_from_checkpoint(self, run_id: str) -> RunManifest:
        """读取 durable Graph state 并生成不信任业务文件的最小索引。."""
        settings = self._settings_for_run(run_id)
        budget_guard = self._budget_guard(run_id, settings=settings)
        config = self._runnable_config(run_id, budget_guard, settings=settings)
        async with self._checkpoint_store.open_saver() as saver:
            graph = self._build_graph(saver, budget_guard, settings=settings)
            snapshot = await graph.aget_state(config)
        values = _state_values(snapshot)
        if not values:
            raise PersistenceError(f"任务 {run_id} 没有可恢复的 Checkpoint")
        return await self._recovery_manifest_from_values(
            run_id,
            values,
            budget_guard,
        )

    async def _checkpoint_recovery_state(
        self,
        run_id: str,
    ) -> tuple[CheckpointSnapshot | None, RunManifest | None]:
        """读取一次 Checkpoint，并同时生成对账快照和恢复索引。."""
        database_path = getattr(self._checkpoint_store, "database_path", None)
        if database_path is not None and not Path(database_path).exists():
            return None, None
        settings = self._settings_for_run(run_id)
        budget_guard = self._budget_guard(run_id, settings=settings)
        config = self._runnable_config(run_id, budget_guard, settings=settings)
        async with self._checkpoint_store.open_saver() as saver:
            graph = self._build_graph(saver, budget_guard, settings=settings)
            snapshot = await graph.aget_state(config)
        values = _state_values(snapshot)
        if not values:
            return None, None
        recovered = await self._recovery_manifest_from_values(
            run_id,
            values,
            budget_guard,
        )
        checkpoint_status = _state_status(values)
        return (
            CheckpointSnapshot(
                run_id=run_id,
                scope_revision=recovered.scope_revision,
                artifact_revision=recovered.artifact_revision,
                status=checkpoint_status.value,
                recoverable=checkpoint_status not in {
                    RunStatus.COMPLETED,
                    RunStatus.PARTIAL,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                },
            ),
            recovered,
        )

    async def _recovery_manifest_from_values(
        self,
        run_id: str,
        values: Mapping[str, Any],
        budget_guard: BudgetGuard,
    ) -> RunManifest:
        """校验 Checkpoint 业务契约并生成非发布恢复索引。."""
        if values.get("run_id") != run_id:
            raise PersistenceError("Checkpoint 的 run_id 与恢复任务不一致")
        try:
            status = _state_status(values)
            scope = _state_scope(values)
            bundle = _state_bundle(values)
            request = _state_request(values)
        except DataValidationError as exc:
            raise PersistenceError("Checkpoint 业务状态无法通过数据契约校验") from exc

        scope_required = status not in {
            RunStatus.DRAFT,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }
        if scope_required and scope is None:
            raise PersistenceError("Checkpoint 状态缺少可恢复的 Scope")
        if status in _PUBLISHED_STATUSES and bundle is None:
            raise PersistenceError("终态 Checkpoint 缺少可恢复的 Bundle")
        if bundle is not None:
            if bundle.run_id != run_id or bundle.status is not status:
                raise PersistenceError("Checkpoint 的 Bundle 与任务状态不一致")
            if scope is None or bundle.scope.revision != scope.revision:
                raise PersistenceError("Checkpoint 的 Bundle 与 Scope revision 不一致")

        budget = bundle.budget if bundle is not None else await budget_guard.snapshot()
        recovered_status = (
            RunStatus.FAILED if status in _PUBLISHED_STATUSES else status
        )
        recovered_config: dict[str, Any] = {
            "recovered_from_checkpoint": True,
            "original_config_available": False,
            "resume_config": self._config_snapshot(self.settings),
        }
        if request is not None:
            recovered_config["task_input_sha256"] = hashlib.sha256(
                request.query.encode("utf-8")
            ).hexdigest()
        return RunManifest(
            run_id=run_id,
            status=recovered_status,
            scope_revision=scope.revision if scope is not None else 0,
            artifact_revision=(
                bundle.artifact_revision if bundle is not None else 0
            ),
            config_snapshot=recovered_config,
            budget_summary=budget,
        )

    def _replace_manifest_from_checkpoint(
        self,
        current: RunManifest,
        recovered: RunManifest,
    ) -> RunManifest:
        """受控重置可解析但落后或不一致的非发布索引。."""
        replacement = current.model_copy(
            update={
                "status": recovered.status,
                "scope_revision": recovered.scope_revision,
                "artifact_revision": recovered.artifact_revision,
                "budget_summary": recovered.budget_summary,
                "file_hashes": {},
                "updated_at": utc_now(),
            }
        )
        self._repository.update_manifest(replacement)
        return self._repository.load_manifest(current.run_id)

    async def list_runs(self) -> list[RunSummary]:
        """只从 Run Repository 列出历史任务。."""
        return self._repository.list_runs()

    async def load_run(self, run_id: str) -> RunBundle:
        """只返回通过 revision 和哈希校验的正式 Bundle。."""
        try:
            return self._repository.load_verified(run_id).bundle
        except PersistenceError as exc:
            manifest = self._repository.load_manifest(run_id)
            if manifest.status not in _PUBLISHED_STATUSES:
                raise DataValidationError(
                    f"任务 {run_id} 尚未生成可查看报告；当前状态为 "
                    f"{_STATUS_NAMES[manifest.status]}。任务已保存在本地，可稍后恢复。"
                ) from exc
            raise

    async def evaluate_dataset(
        self,
        request: EvaluationRequest,
    ) -> EvaluationBatchResult:
        """把离线评测请求交给 Evaluation Service。."""
        service = self._evaluation_service
        if service is None:
            from opencompete.evaluation.judge import LangChainReportJudge
            from opencompete.evaluation.service import EvaluationService

            judge = (
                LangChainReportJudge(
                    model=self.settings.judge_model,
                    max_tokens=self.settings.judge_model_max_tokens,
                    prompt_version=self.settings.judge_prompt_version,
                    request_timeout_seconds=(
                        self.settings.llm_request_timeout_seconds
                    ),
                    sdk_max_retries=self.settings.llm_sdk_max_retries,
                    disable_thinking=bool(
                        thinking_disable_options(self.settings.judge_model)
                    ),
                )
                if self.settings.judge_model is not None
                else None
            )
            service = cast(
                EvaluationServiceProtocol,
                EvaluationService(repository=self._repository, judge=judge),
            )
            self._evaluation_service = service
        try:
            return await service.evaluate(request)
        except (OSError, ValueError) as exc:
            raise DataValidationError(f"评测输入或结果无效：{exc}") from exc

    async def evaluate_competitive_dataset(
        self,
        request: CompetitiveEvaluationRequest,
    ) -> Any:
        """默认竞品评测走 v3（官方 RACE 四维）；不再写入新的 v2 五维主分。."""
        from opencompete.evaluation.competitive_v3_judge import (
            LangChainCompetitiveV3RaceJudge,
        )
        from opencompete.evaluation.competitive_v3_models import (
            CompetitiveEvaluationRequestV3,
        )
        from opencompete.evaluation.competitive_v3_service import (
            CompetitiveEvaluationServiceV3,
        )

        v3_request = CompetitiveEvaluationRequestV3(
            dataset=request.dataset,
            targets=request.targets,
            allow_authored=request.allow_authored,
        )
        judge = (
            LangChainCompetitiveV3RaceJudge(
                model=self.settings.judge_model,
                max_tokens=self.settings.judge_model_max_tokens,
                request_timeout_seconds=self.settings.llm_request_timeout_seconds,
                sdk_max_retries=self.settings.llm_sdk_max_retries,
                max_output_attempts=self.settings.max_structured_output_retries,
                disable_thinking=bool(
                    thinking_disable_options(self.settings.judge_model)
                ),
            )
            if self.settings.judge_model is not None
            else None
        )
        service = CompetitiveEvaluationServiceV3(
            repository=self._repository,
            judge=judge,
        )
        try:
            return await service.evaluate(v3_request)
        except (OSError, ValueError) as exc:
            raise DataValidationError(f"v3 评测输入或结果无效：{exc}") from exc

    async def calibrate_competitive_case(
        self,
        dataset_path: str | Path,
        case_id: str,
    ) -> CompetitiveCalibrationResult:
        """使用同一匿名 Judge 对固定 Reference 的六种缺陷变体做校准。."""
        from opencompete.evaluation.competitive_calibration import (
            evaluate_case_calibration,
            load_calibration_document,
            save_calibration_result,
        )
        from opencompete.evaluation.competitive_dataset import (
            load_competitive_dataset,
        )
        from opencompete.evaluation.competitive_judge import (
            LangChainCompetitiveReportJudge,
        )

        if self.settings.judge_model is None:
            raise DataValidationError("竞品校准必须配置 judge_model")
        try:
            dataset = load_competitive_dataset(dataset_path)
            case = dataset.get_case(case_id)
            document = load_calibration_document(dataset_path, case_id)
            judge = LangChainCompetitiveReportJudge(
                model=self.settings.judge_model,
                max_tokens=self.settings.judge_model_max_tokens,
                request_timeout_seconds=self.settings.llm_request_timeout_seconds,
                sdk_max_retries=self.settings.llm_sdk_max_retries,
                max_output_attempts=self.settings.max_structured_output_retries,
                disable_thinking=bool(
                    thinking_disable_options(self.settings.judge_model)
                ),
            )
            result = await evaluate_case_calibration(
                case,
                document,
                judge,
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.version,
            )
            save_calibration_result(result, data_root=self.settings.data_dir)
            return result
        except (OSError, ValueError, KeyError) as exc:
            raise DataValidationError(f"v2 Judge 校准失败：{exc}") from exc

    async def _resume_graph(
        self,
        run_id: str,
        user_input: CandidateSelection | ScopeConfirmation | None,
        *,
        scope: ScopeSpec | None = None,
        settings: OpenCompeteSettings | None = None,
        resumed_with_current_settings: bool = False,
    ) -> RunHandle:
        """继续 durable 图；模型路由取当前设置，预算契约取原任务快照。."""
        execution_id: str | None = None
        started: float | None = None
        settings = settings or self._settings_for_run(run_id)
        budget_settings = self._settings_for_run(run_id)
        connections = runtime_model_connections(settings)
        async with self._checkpoint_store.open_saver() as saver:
            if user_input is not None:
                execution_id = str(uuid4())
                budget_guard = self._budget_guard(
                    run_id,
                    settings=budget_settings,
                    execution_id=execution_id,
                    emit_attempt_events=True,
                )
                self._append_resume_runtime_event(
                    run_id,
                    execution_id,
                    settings,
                    current_settings=resumed_with_current_settings,
                )
                self._append_event(
                    run_id,
                    "graph_execution_started",
                    execution_id=execution_id,
                    mode="resume",
                )
                started = perf_counter()
                try:
                    config = self._runnable_config(
                        run_id,
                        budget_guard,
                        settings=settings,
                        budget_settings=budget_settings,
                        scope=scope,
                    )
                    graph = self._build_graph(saver, budget_guard, settings=settings)
                    with model_connection_context(connections):
                        result = await graph.ainvoke(
                            Command(resume=user_input.model_dump(mode="json")),
                            config,
                        )
                except BudgetExceededError as exc:
                    config = self._runnable_config(
                        run_id,
                        budget_guard,
                        settings=settings,
                        budget_settings=budget_settings,
                        scope=scope,
                    )
                    graph = self._build_graph(saver, budget_guard, settings=settings)
                    result = await self._budget_partial_state(
                        graph,
                        run_id,
                        budget_guard,
                        config,
                        exc,
                    )
            else:
                read_budget_guard = self._budget_guard(
                    run_id, settings=budget_settings
                )
                read_config = self._runnable_config(
                    run_id,
                    read_budget_guard,
                    settings=settings,
                    budget_settings=budget_settings,
                )
                read_graph = self._build_graph(
                    saver, read_budget_guard, settings=settings
                )
                snapshot = await read_graph.aget_state(read_config)
                values = _state_values(snapshot)
                if not values:
                    raise PersistenceError(f"任务 {run_id} 没有可恢复的 Checkpoint")
                state_status = _state_status(values)
                if (
                    state_status is RunStatus.AWAITING_CONFIRMATION
                    or _state_interrupts(snapshot)
                ):
                    result = values
                elif state_status in _PUBLISHED_STATUSES | {
                    RunStatus.CANCELLED,
                    RunStatus.FAILED,
                }:
                    result = values
                else:
                    execution_id = str(uuid4())
                    budget_guard = self._budget_guard(
                        run_id,
                        settings=budget_settings,
                        execution_id=execution_id,
                        emit_attempt_events=True,
                    )
                    self._append_resume_runtime_event(
                        run_id,
                        execution_id,
                        settings,
                        current_settings=resumed_with_current_settings,
                    )
                    self._append_event(
                        run_id,
                        "graph_execution_started",
                        execution_id=execution_id,
                        mode="resume",
                    )
                    started = perf_counter()
                    try:
                        config = self._runnable_config(
                            run_id,
                            budget_guard,
                            settings=settings,
                            budget_settings=budget_settings,
                            scope=scope,
                        )
                        graph = self._build_graph(
                            saver, budget_guard, settings=settings
                        )
                        with model_connection_context(connections):
                            result = await graph.ainvoke(None, config)
                    except BudgetExceededError as exc:
                        config = self._runnable_config(
                            run_id,
                            budget_guard,
                            settings=settings,
                            budget_settings=budget_settings,
                            scope=scope,
                        )
                        graph = self._build_graph(
                            saver, budget_guard, settings=settings
                        )
                        result = await self._budget_partial_state(
                            graph,
                            run_id,
                            budget_guard,
                            config,
                            exc,
                        )
        handle = await self._finalize_state(run_id, result)
        # Manifest 先落盘再广播完成事件，确保 UI 端收到停止帧时即可读到终态。
        if execution_id is not None and started is not None:
            self._append_event(
                run_id,
                "graph_resumed",
                execution_id=execution_id,
                duration_ms=int((perf_counter() - started) * 1000),
            )
        return handle

    async def _invoke_graph(
        self,
        run_id: str,
        value: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        execution_id = str(uuid4())
        settings = self._settings_for_run(run_id)
        budget_guard = self._budget_guard(
            run_id,
            settings=settings,
            execution_id=execution_id,
            emit_attempt_events=True,
        )
        async with self._checkpoint_store.open_saver() as saver:
            config = self._runnable_config(run_id, budget_guard, settings=settings)
            graph = self._build_graph(saver, budget_guard, settings=settings)
            self._append_event(
                run_id,
                "graph_execution_started",
                execution_id=execution_id,
                mode="invoke",
            )
            started = perf_counter()
            try:
                with model_connection_context(runtime_model_connections(settings)):
                    result = await graph.ainvoke(value, config)
            except BudgetExceededError as exc:
                result = await self._budget_partial_state(
                    graph,
                    run_id,
                    budget_guard,
                    config,
                    exc,
                )
        self._append_event(
            run_id,
            "graph_invoked",
            execution_id=execution_id,
            duration_ms=int((perf_counter() - started) * 1000),
        )
        return result

    async def _invoke_graph_from_fixed_scope(
        self,
        run_id: str,
        request: ResearchRequest,
        scope: ScopeSpec,
    ) -> Mapping[str, Any]:
        """用 LangGraph 状态更新能力进入正式 ``freeze_scope`` 后继边。."""
        execution_id = str(uuid4())
        settings = self._settings_for_run(run_id)
        budget_guard = self._budget_guard(
            run_id,
            settings=settings,
            execution_id=execution_id,
            emit_attempt_events=True,
        )
        async with self._checkpoint_store.open_saver() as saver:
            config = self._runnable_config(
                run_id, budget_guard, settings=settings, scope=scope
            )
            graph = self._build_graph(saver, budget_guard, settings=settings)
            self._append_event(
                run_id,
                "graph_execution_started",
                execution_id=execution_id,
                mode="fixed_scope",
            )
            started = perf_counter()
            updated_config = await graph.aupdate_state(
                config,
                {
                    "run_id": run_id,
                    "request": request.model_dump(mode="json"),
                    "scope": scope.model_dump(mode="json"),
                    "status": RunStatus.SCOPE_FROZEN.value,
                },
                as_node="freeze_scope",
            )
            resume_config = _merge_updated_graph_config(config, updated_config)
            try:
                with model_connection_context(runtime_model_connections(settings)):
                    result = await graph.ainvoke(None, resume_config)
            except BudgetExceededError as exc:
                result = await self._budget_partial_state(
                    graph,
                    run_id,
                    budget_guard,
                    resume_config,
                    exc,
                )
        self._append_event(
            run_id,
            "graph_invoked",
            execution_id=execution_id,
            mode="fixed_scope",
            duration_ms=int((perf_counter() - started) * 1000),
        )
        return result

    async def _budget_partial_state(
        self,
        graph: ApplicationGraph,
        run_id: str,
        budget_guard: BudgetGuard,
        config: RunnableConfig,
        exc: BudgetExceededError,
    ) -> Mapping[str, Any]:
        """预算拒绝后只用 durable 已有数据生成零外部调用的部分产物。."""
        snapshot = await graph.aget_state(config)
        values = _state_values(snapshot)
        scope = _state_scope(values)
        if scope is None or not scope.frozen:
            raise DataValidationError(
                "预算耗尽时没有可交付的冻结范围"
            ) from exc
        bundle = await materialize_bundle(
            run_id=run_id,
            scope=scope,
            upstream_state={
                **values,
                # 拒绝发生在预留前（durable ledger 无记录），此处用异常本身
                # 标记预算耗尽，确保部分产物标题开放给用户可读的说明。
                "budget_exhausted": True,
            },
            budget=await budget_guard.snapshot(),
            extractor=StaticArtifactExtractor(
                ArtifactExtraction(limitations=("预算已耗尽，已停止新增外部调用",))
            ),
        )
        self._append_event(
            run_id,
            "budget_exhausted",
            status=RunStatus.PARTIAL,
            error_code=exc.code,
        )
        return {
            **values,
            "run_id": run_id,
            "scope": scope,
            "status": RunStatus.PARTIAL,
            "bundle": bundle,
        }

    def _build_graph(
        self,
        saver: object,
        budget_guard: BudgetGuard,
        *,
        settings: OpenCompeteSettings,
    ) -> ApplicationGraph:
        extractor = self._extractor_for(settings)
        return self._graph_factory(
            saver,
            research_graph=self._research_graph,
            extractor=extractor,
            budget_guard=budget_guard,
            discovery_provider=(
                self._discovery_provider_override
                or LazyLlmDiscoveryProvider(settings)
            ),
            scope_planner=(
                self._scope_planner_override or LazyScopePlanner(settings)
            ),
            # 这是候选发现专用的单次 Tavily 请求页大小，绝不能再充当候选
            # 产品数量上限，也不能因上游 Researcher 的搜索配置而被静默收紧。
            discovery_search_results_per_query=(
                settings.discovery_search_results_per_query
            ),
        )

    def _extractor_for(self, settings: OpenCompeteSettings) -> ArtifactExtractor:
        """按运行快照模型路由创建提取器；测试注入始终优先。."""
        if self._extractor_override is not None:
            return self._extractor_override
        return LangChainArtifactExtractor(
            settings.final_report_model,
            max_tokens=settings.artifact_extraction_model_max_tokens,
            max_retries=settings.primary_model_attempts,
            request_timeout_seconds=settings.llm_request_timeout_seconds,
            sdk_max_retries=settings.llm_sdk_max_retries,
            fallback_model=settings.fallback_model,
            fallback_provider_name=settings.fallback_provider_name,
            primary_provider_name=settings.primary_provider_name,
            fallback_model_attempts=settings.fallback_model_attempts,
            retry_delays_seconds=settings.model_retry_delays_seconds,
        )

    async def _finalize_state(
        self,
        run_id: str,
        state: Mapping[str, Any],
    ) -> RunHandle:
        status = _state_status(state)
        scope = _state_scope(state)
        bundle = _state_bundle(state)
        budget = (
            bundle.budget
            if bundle is not None
            else await self._budget_guard(run_id).snapshot()
        )
        current = self._repository.load_manifest(run_id)

        if status in _PUBLISHED_STATUSES:
            if bundle is None:
                raise DataValidationError(
                    f"任务 {run_id} 已到终态但没有结构化 Bundle"
                )
            if bundle.run_id != run_id or bundle.status is not status:
                raise DataValidationError("Graph 终态与 Bundle 契约不一致")
            manifest = current.model_copy(
                update={
                    "status": status,
                    "scope_revision": bundle.scope.revision,
                    "artifact_revision": bundle.artifact_revision,
                    "budget_summary": bundle.budget,
                    "file_hashes": {},
                    "updated_at": utc_now(),
                }
            )
            published = self._repository.publish(bundle, manifest)
            self._append_event(run_id, "run_published", status=status)
            return RunHandle(
                run_id=run_id,
                status=status,
                scope=bundle.scope,
                bundle=published.bundle,
            )

        if status is RunStatus.AWAITING_CONFIRMATION:
            if scope is None:
                raise DataValidationError("确认点没有提供待确认 Scope")
            manifest = current.model_copy(
                update={
                    "status": status,
                    "scope_revision": scope.revision,
                    "budget_summary": budget,
                    "file_hashes": {},
                    "updated_at": utc_now(),
                }
            )
            self._repository.update_manifest(manifest)
            self._append_event(run_id, "awaiting_confirmation", status=status)
            confirmation_kind = _state_confirmation_kind(state)
            return RunHandle(
                run_id=run_id,
                status=status,
                scope=scope,
                pending_confirmation=True,
                confirmation_kind=confirmation_kind,
                discovery_candidates=_state_discovery_candidates(state),
            )

        manifest = current.model_copy(
            update={
                "status": status,
                "scope_revision": scope.revision if scope is not None else 0,
                "budget_summary": budget,
                "file_hashes": {},
                "updated_at": utc_now(),
            }
        )
        self._repository.update_manifest(manifest)
        self._append_event(run_id, "run_status_changed", status=status)
        return RunHandle(run_id=run_id, status=status, scope=scope, bundle=bundle)

    async def _record_failure(self, run_id: str, exc: Exception) -> None:
        """尽力把脱敏失败写入 Manifest，但不遮蔽原异常。."""
        try:
            current = self._repository.load_manifest(run_id)
            try:
                budget = await self._budget_guard(run_id).snapshot()
            except Exception:
                budget = current.budget_summary
            failed = current.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "budget_summary": budget,
                    "file_hashes": {},
                    "errors": (*current.errors, sanitized_error(exc)),
                    "updated_at": utc_now(),
                }
            )
            self._repository.update_manifest(failed)
            self._append_event(
                run_id,
                "run_failed",
                status=RunStatus.FAILED,
                error_code=failed.errors[-1].code,
                error=failed.errors[-1].message,
            )
        except Exception:
            logger.error("任务失败状态无法写入：%s", run_id)

    async def guard_cancellation(
        self,
        run_id: str,
        execution: Coroutine[Any, Any, RunHandle],
    ) -> RunHandle:
        """包装后台执行协程：用户取消时落盘 CANCELLED 终态再传播。.

        CancelledError 会绕过 ``except Exception``；不捕获落盘会让任务
        留在无法恢复的中间态 manifest。取消是终态，不保留恢复。
        """
        try:
            return await execution
        except asyncio.CancelledError:
            await self._record_cancellation(run_id)
            raise

    async def _record_cancellation(self, run_id: str) -> None:
        """尽力把用户取消写入 Manifest，但不遮蔽 CancelledError。."""
        try:
            current = self._repository.load_manifest(run_id)
            try:
                budget = await self._budget_guard(run_id).snapshot()
            except Exception:
                budget = current.budget_summary
            cancelled = current.model_copy(
                update={
                    "status": RunStatus.CANCELLED,
                    "budget_summary": budget,
                    "file_hashes": {},
                    "updated_at": utc_now(),
                }
            )
            self._repository.update_manifest(cancelled)
            self._append_event(
                run_id,
                "run_cancelled",
                status=RunStatus.CANCELLED,
            )
        except Exception:
            logger.error("任务取消状态无法写入：%s", run_id)

    def _budget_guard(
        self,
        run_id: str,
        *,
        settings: OpenCompeteSettings | None = None,
        execution_id: str | None = None,
        emit_attempt_events: bool = False,
    ) -> BudgetGuard:
        def append_attempt_event(event: dict[str, object]) -> None:
            self._repository.append_event(run_id, event)
            self._notify_progress(event)

        return BudgetGuard(
            self._execution_ledger,
            self._budget_limit(settings or self.settings),
            run_id,
            execution_id=execution_id,
            event_sink=(append_attempt_event if emit_attempt_events else None),
        )

    def _budget_limit(self, settings: OpenCompeteSettings) -> BudgetEstimate:
        return BudgetEstimate(
            input_tokens=settings.max_input_tokens,
            output_tokens=settings.max_output_tokens,
            tavily_calls=settings.max_tavily_calls,
            cost_usd=settings.max_cost_usd,
        )

    def _runnable_config(
        self,
        run_id: str,
        budget_guard: BudgetGuard,
        *,
        settings: OpenCompeteSettings | None = None,
        budget_settings: OpenCompeteSettings | None = None,
        scope: ScopeSpec | None = None,
    ) -> RunnableConfig:
        effective_settings = settings or self.settings
        effective_budget_settings = budget_settings or effective_settings
        config = cast(
            RunnableConfig,
            effective_settings.to_runnable_config(run_id),
        )
        effective_report_tokens = effective_settings.final_report_model_max_tokens
        if scope is not None and scope.frozen:
            # max_tokens 是输出上限而非预留；范围矩阵越大，完整叙事报告
            # 所需容量线性增长。配置值只做下限，避免单次事故驱动常数。
            effective_report_tokens = max(
                effective_report_tokens,
                estimated_report_output_tokens(scope),
            )
            configurable = dict(config.get("configurable") or {})
            configurable["final_report_model_max_tokens"] = effective_report_tokens
            config["configurable"] = configurable
        metadata = dict(config.get("metadata") or {})
        metadata["opencompete_execution_id"] = budget_guard.execution_id
        config["metadata"] = metadata
        config["callbacks"] = [
            BudgetCallback(
                budget_guard,
                default_max_output_tokens=max(
                    effective_settings.research_model_max_tokens,
                    effective_settings.summarization_model_max_tokens,
                    effective_settings.compression_model_max_tokens,
                    effective_report_tokens,
                    effective_settings.artifact_extraction_model_max_tokens,
                ),
                llm_input_cost_per_million_usd=(
                    effective_budget_settings.llm_input_cost_per_million_usd
                ),
                llm_output_cost_per_million_usd=(
                    effective_budget_settings.llm_output_cost_per_million_usd
                ),
                tavily_cost_per_call_usd=(
                    effective_budget_settings.tavily_cost_per_call_usd
                ),
                tavily_max_queries_per_call=(
                    effective_budget_settings.tavily_max_queries_per_call
                ),
            )
        ]
        return config

    def _resume_settings_for_run(self, run_id: str) -> OpenCompeteSettings:
        """合成恢复运行时设置：当前模型路由覆盖，原任务执行约束保留。."""
        task_settings = self._settings_for_run(run_id)
        current_settings = self.settings
        values = task_settings.model_dump(mode="python")
        values.update(
            {
                name: getattr(current_settings, name)
                for name in _RESUME_RUNTIME_FIELDS
            }
        )
        return OpenCompeteSettings.model_validate(values)

    def _settings_for_run(self, run_id: str) -> OpenCompeteSettings:
        """恢复运行时从 Manifest 读取创建时冻结的非秘密设置。."""
        try:
            manifest = self._repository.load_manifest(run_id)
        except PersistenceError:
            # Manifest 损坏时由后续 Checkpoint 对账重建索引；此窗口保留历史
            # 降级路径，避免为了读取快照阻断恢复。
            return self.settings
        snapshot = dict(manifest.config_snapshot)
        settings_payload = snapshot.get("settings")
        if not isinstance(settings_payload, Mapping):
            # 早期任务没有封装快照；保留既有降级语义并使用当前默认设置。
            return self.settings
        try:
            return OpenCompeteSettings.model_validate(dict(settings_payload))
        except ValidationError as exc:
            raise PersistenceError("任务设置快照无法通过当前配置契约校验") from exc

    def _append_resume_runtime_event(
        self,
        run_id: str,
        execution_id: str,
        settings: OpenCompeteSettings,
        *,
        current_settings: bool,
    ) -> None:
        """记录恢复时选择的非秘密模型路由，不覆盖创建快照。."""
        if not current_settings:
            return
        self._append_event(
            run_id,
            "resume_runtime_selected",
            execution_id=execution_id,
            settings_revision=self._default_settings_revision,
            primary_connection_role="primary",
            primary_model=settings.research_model,
            fallback_connection_role=(
                "fallback" if settings.fallback_model is not None else None
            ),
            fallback_model=settings.fallback_model,
        )

    def _config_snapshot(self, settings: OpenCompeteSettings) -> dict[str, Any]:
        """生成只含非秘密字段的版本化任务设置快照。."""
        serialized = settings.model_dump(mode="json")
        return {
            **serialized,
            "settings_schema_version": 1,
            "settings_revision": self._default_settings_revision,
            "settings": serialized,
            "budget_calculation_version": "v2-tavily-credits",
        }

    def _lock_for(self, run_id: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with _RUN_LOCKS_GUARD:
            locks = _RUN_LOCKS_BY_LOOP.setdefault(loop, {})
            return locks.setdefault(run_id, asyncio.Lock())

    def _append_event(self, run_id: str, event_type: str, **values: object) -> None:
        event = {
            "event": event_type,
            "run_id": run_id,
            "occurred_at": utc_now().isoformat(),
            **{
                key: value.value if isinstance(value, RunStatus) else value
                for key, value in values.items()
            },
        }
        try:
            self._repository.append_event(run_id, event)
            self._notify_progress(event)
        except PersistenceError:
            logger.warning("任务事件写入失败：%s/%s", run_id, event_type)

    def _notify_progress(self, event: dict[str, object]) -> None:
        """用户反馈失败不得改变研究和持久化语义。."""
        sink = self._progress_sink
        if sink is None:
            return
        try:
            sink(event)
        except Exception:
            logger.warning("任务进度反馈失败：%s", event.get("run_id"))


def _validate_fixed_scope_input(
    request: ResearchRequest,
    scope: ScopeSpec,
) -> None:
    """固定入口只允许与请求完全一致的已确认 Scope。."""
    if request.discover_candidates:
        raise DataValidationError("固定 Scope 运行不能启用候选发现")
    if not scope.frozen or scope.confirmed_at is None or scope.revision < 1:
        raise DataValidationError("固定 Scope 必须已经确认并冻结")
    if tuple(request.entities) != tuple(scope.entities):
        raise DataValidationError("固定 Scope 的研究对象与 ResearchRequest 不一致")
    if tuple(request.levels) != tuple(scope.levels):
        raise DataValidationError("固定 Scope 的分析层级与 ResearchRequest 不一致")
    if dict(request.dimensions) != dict(scope.dimensions):
        raise DataValidationError("固定 Scope 的分析维度与 ResearchRequest 不一致")
    if tuple(request.exclusions) != tuple(scope.exclusions):
        raise DataValidationError("固定 Scope 的排除项与 ResearchRequest 不一致")


def _merge_updated_graph_config(
    original: RunnableConfig,
    updated: RunnableConfig,
) -> RunnableConfig:
    """保留业务配置，同时采用 ``aupdate_state`` 返回的 checkpoint 定位。."""
    merged = cast(RunnableConfig, {**original, **updated})
    merged["configurable"] = {
        **dict(original.get("configurable") or {}),
        **dict(updated.get("configurable") or {}),
    }
    merged["metadata"] = {
        **dict(original.get("metadata") or {}),
        **dict(updated.get("metadata") or {}),
    }
    if original.get("callbacks") is not None:
        merged["callbacks"] = original["callbacks"]
    return merged


def _state_values(snapshot: object) -> Mapping[str, Any]:
    values = getattr(snapshot, "values", {})
    return values if isinstance(values, Mapping) else {}


def _state_interrupts(snapshot: object) -> tuple[object, ...]:
    direct = getattr(snapshot, "interrupts", ())
    if direct:
        return tuple(direct)
    interrupts: list[object] = []
    for task in getattr(snapshot, "tasks", ()) or ():
        interrupts.extend(getattr(task, "interrupts", ()) or ())
    return tuple(interrupts)


def _state_status(state: Mapping[str, Any]) -> RunStatus:
    value = state.get("status")
    try:
        return value if isinstance(value, RunStatus) else RunStatus(str(value))
    except ValueError as exc:
        raise DataValidationError(f"Graph 返回了无效任务状态：{value}") from exc


def _state_scope(state: Mapping[str, Any]) -> ScopeSpec | None:
    value = state.get("scope")
    if value is None:
        return None
    try:
        return ScopeSpec.model_validate(value)
    except Exception as exc:
        raise DataValidationError("Graph 返回的 Scope 无法校验") from exc


def _state_request(state: Mapping[str, Any]) -> ResearchRequest | None:
    value = state.get("request")
    if value is None:
        return None
    try:
        return ResearchRequest.model_validate(value)
    except Exception as exc:
        raise DataValidationError("Graph 返回的 ResearchRequest 无法校验") from exc


def _state_bundle(state: Mapping[str, Any]) -> RunBundle | None:
    value = state.get("bundle")
    if value is None:
        return None
    try:
        return RunBundle.model_validate(value)
    except Exception as exc:
        raise DataValidationError("Graph 返回的 Bundle 无法校验") from exc


def _state_confirmation_kind(state: Mapping[str, Any]) -> ConfirmationKind:
    value = state.get("confirmation_kind", ConfirmationKind.SCOPE_CONFIRMATION.value)
    try:
        return ConfirmationKind(str(value))
    except ValueError as exc:
        raise DataValidationError("Graph 返回了无效确认类型") from exc


def _state_discovery_candidates(
    state: Mapping[str, Any],
) -> tuple[DiscoveryCandidate, ...]:
    try:
        candidates = tuple(
            DiscoveryCandidate.model_validate(candidate)
            for candidate in state.get("discovery_candidates", ())
        )
        for candidate in candidates:
            validate_public_http_url(candidate.source_url)
        return candidates
    except Exception as exc:
        raise DataValidationError("Graph 返回的候选对象无法校验") from exc


__all__ = ["ApplicationRunner", "RunInspection"]
