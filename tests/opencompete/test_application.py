from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import Generation, LLMResult
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

import opencompete.application as application_module
from opencompete.analysis import build_draft_scope, freeze_scope
from opencompete.application import ApplicationRunner
from opencompete.callbacks import BudgetCallback
from opencompete.errors import BudgetExceededError, PersistenceError
from opencompete.extractors import ArtifactExtraction, StaticArtifactExtractor
from opencompete.models import (
    AnalysisLevel,
    BudgetSnapshot,
    ReportArtifact,
    ResearchCommission,
    ResearchEntity,
    ResearchRequest,
    RunBundle,
    RunManifest,
    RunStatus,
    ScopeConfirmation,
    ScopeProposal,
    ScopeSpec,
)
from opencompete.persistence import ReconcileAction, RunRepository, reconcile
from opencompete.settings import OpenCompeteSettings


def _checkpoint_payloads(data_root: Path) -> bytes:
    return b"".join(
        path.read_bytes()
        for path in data_root.glob("checkpoints.sqlite3*")
        if path.is_file()
    )


class RecordingCheckpointStore:
    def __init__(self) -> None:
        self.active = False
        self.entries = 0

    @asynccontextmanager
    async def open_saver(self):
        self.active = True
        self.entries += 1
        try:
            yield object()
        finally:
            self.active = False


class FakeExecutionLedger:
    async def initialize(self) -> None:
        return None

    async def snapshot(self, run_id, limit):
        del run_id
        return BudgetSnapshot(limit=limit, remaining=limit)

    async def try_reserve(self, reservation, limit):
        del limit
        return reservation

    async def settle(self, reservation_id, actual):
        raise AssertionError((reservation_id, actual))

    async def cancel(self, reservation_id):
        raise AssertionError(reservation_id)


class RecordingGraphRuntime:
    def __init__(
        self,
        checkpoint_store: RecordingCheckpointStore,
        *,
        terminal_status: RunStatus = RunStatus.COMPLETED,
        resume_delay: float = 0,
    ) -> None:
        self.checkpoint_store = checkpoint_store
        self.terminal_status = terminal_status
        self.resume_delay = resume_delay
        self.states: dict[str, dict[str, Any]] = {}
        self.resume_calls = 0
        self.active_resumes = 0
        self.max_active_resumes = 0
        self.received_callbacks = False
        self.budget_guard = None
        self.last_configurable: dict[str, Any] | None = None
        self.build_kwargs: dict[str, Any] | None = None

    def build(self, checkpointer, research_graph=None, **kwargs):
        del checkpointer, research_graph
        assert self.checkpoint_store.active
        assert "extractor" in kwargs
        assert "budget_guard" in kwargs
        self.build_kwargs = dict(kwargs)
        self.budget_guard = kwargs["budget_guard"]
        return self

    async def ainvoke(self, value, config):
        assert self.checkpoint_store.active
        run_id = config["configurable"]["thread_id"]
        self.last_configurable = dict(config["configurable"])
        self.received_callbacks = bool(config.get("callbacks"))
        if isinstance(value, dict):
            request = ResearchRequest.model_validate(value["request"])
            scope = build_draft_scope(request)
            result = {
                "run_id": run_id,
                "scope": scope,
                "status": RunStatus.AWAITING_CONFIRMATION,
                "__interrupt__": (object(),),
            }
            self.states[run_id] = result
            return result

        assert isinstance(value, Command)
        self.resume_calls += 1
        self.active_resumes += 1
        self.max_active_resumes = max(
            self.max_active_resumes,
            self.active_resumes,
        )
        try:
            if self.resume_delay:
                await asyncio.sleep(self.resume_delay)
            scope = self.states[run_id]["scope"]
            confirmation = ScopeConfirmation.model_validate(value.resume)
            if not confirmation.accepted:
                result = {
                    "run_id": run_id,
                    "scope": scope,
                    "status": RunStatus.CANCELLED,
                }
                self.states[run_id] = result
                return result
            frozen = freeze_scope(scope, confirmation)
            assert self.budget_guard is not None
            budget = await self.budget_guard.snapshot()
            bundle = RunBundle(
                run_id=run_id,
                scope=frozen,
                report=ReportArtifact(markdown="# 竞品分析\n\n测试报告"),
                budget=budget,
                status=self.terminal_status,
            )
            result = {
                "run_id": run_id,
                "scope": frozen,
                "status": self.terminal_status,
                "bundle": bundle,
            }
            self.states[run_id] = result
            return result
        finally:
            self.active_resumes -= 1

    async def aget_state(self, config):
        assert self.checkpoint_store.active
        run_id = config["configurable"]["thread_id"]
        state = self.states.get(run_id, {})
        interrupts = state.get("__interrupt__", ())
        return SimpleNamespace(values=state, interrupts=interrupts)


class AttemptGraphRuntime(RecordingGraphRuntime):
    def __init__(self, checkpoint_store: RecordingCheckpointStore) -> None:
        super().__init__(checkpoint_store)
        self.execution_ids: list[str] = []

    async def ainvoke(self, value, config):
        callbacks = config.get("callbacks") or []
        callback = callbacks[0]
        assert isinstance(callback, BudgetCallback)
        execution_id = config.get("metadata", {}).get(
            "opencompete_execution_id"
        )
        assert isinstance(execution_id, str) and execution_id
        self.execution_ids.append(execution_id)
        if isinstance(value, dict):
            await self._attempt(callback, succeeded=True, retry_number=1)
        else:
            await self._attempt(callback, succeeded=False, retry_number=1)
            await self._attempt(callback, succeeded=True, retry_number=2)
        return await super().ainvoke(value, config)

    async def _attempt(
        self,
        callback: BudgetCallback,
        *,
        succeeded: bool,
        retry_number: int,
    ) -> None:
        research_run = uuid4()
        node_run = uuid4()
        model_run = uuid4()
        topic = "真实研究主题不能进入持久化"
        await callback.on_chain_start(
            {},
            {"research_topic": topic},
            run_id=research_run,
        )
        await callback.on_chain_start(
            {},
            {"messages": []},
            run_id=node_run,
            parent_run_id=research_run,
        )
        tags = [] if retry_number == 1 else [f"retry:attempt:{retry_number}"]
        await callback.on_llm_start(
            {},
            ["same model input"],
            run_id=model_run,
            parent_run_id=node_run,
            tags=tags,
            metadata={"langgraph_node": "researcher", "langgraph_step": 1},
            invocation_params={"max_tokens": 10},
        )
        if succeeded:
            await callback.on_llm_end(
                LLMResult(
                    generations=[[Generation(text="ok")]],
                    llm_output={
                        "token_usage": {
                            "prompt_tokens": 2,
                            "completion_tokens": 1,
                        }
                    },
                ),
                run_id=model_run,
            )
        else:
            await callback.on_llm_error(
                RuntimeError("供应商异常正文不能进入持久化"),
                run_id=model_run,
            )
        await callback.on_chain_end({}, run_id=node_run)
        await callback.on_chain_end({}, run_id=research_run)


class FixedScopeGraphRuntime(RecordingGraphRuntime):
    """记录固定 Scope 注入并模拟正式后继节点完成发布。"""

    def __init__(self, checkpoint_store: RecordingCheckpointStore) -> None:
        super().__init__(checkpoint_store)
        self.updated_as_node: str | None = None
        self.update_values: dict[str, Any] | None = None

    async def aupdate_state(self, config, values, *, as_node):
        assert self.checkpoint_store.active
        run_id = config["configurable"]["thread_id"]
        self.updated_as_node = as_node
        self.update_values = dict(values)
        self.states[run_id] = dict(values)
        return config

    async def ainvoke(self, value, config):
        if value is not None:
            return await super().ainvoke(value, config)
        assert self.checkpoint_store.active
        self.last_configurable = dict(config["configurable"])
        self.received_callbacks = bool(config.get("callbacks"))
        run_id = config["configurable"]["thread_id"]
        state = self.states[run_id]
        scope = ScopeSpec.model_validate(state["scope"])
        assert scope.frozen
        assert self.budget_guard is not None
        bundle = RunBundle(
            run_id=run_id,
            scope=scope,
            report=ReportArtifact(markdown="# 固定 Scope 竞品分析\n\n测试报告"),
            budget=await self.budget_guard.snapshot(),
            status=RunStatus.COMPLETED,
        )
        result = {
            **state,
            "status": RunStatus.COMPLETED,
            "bundle": bundle,
        }
        self.states[run_id] = result
        return result


class FailingPublishRepository(RunRepository):
    def publish(self, bundle, manifest):
        del bundle, manifest
        raise PersistenceError("模拟最终发布失败")


class FailOncePublishRepository(RunRepository):
    def __init__(self, data_root: Path) -> None:
        super().__init__(data_root)
        self.publish_attempts = 0

    def publish(self, bundle, manifest):
        self.publish_attempts += 1
        if self.publish_attempts == 1:
            raise PersistenceError("模拟发布崩溃窗口")
        return super().publish(bundle, manifest)


class FailingAttemptEventRepository(RunRepository):
    def __init__(self, data_root: Path) -> None:
        super().__init__(data_root)
        self.attempt_event_failures = 0

    def append_event(self, run_id, event, *, secrets=()):
        if str(event.get("event", "")).startswith("call_attempt_"):
            self.attempt_event_failures += 1
            raise PersistenceError("模拟 attempt 事件写入失败")
        return super().append_event(run_id, event, secrets=secrets)


def _request() -> ResearchRequest:
    return ResearchRequest(
        query="比较 Alpha 与 Beta",
        entities=(ResearchEntity(name="Alpha"), ResearchEntity(name="Beta")),
    )


def _offline_research_graph(
    calls: list[str] | None = None,
) -> StateGraph[Any, Any, Any, Any]:
    class ResearchState(MessagesState):
        raw_notes: list[str]
        final_report: str

    async def research(state: ResearchState) -> dict[str, Any]:
        del state
        if calls is not None:
            calls.append("research")
        return {
            "messages": [AIMessage(content="研究完成")],
            "raw_notes": [],
            "final_report": "# Alpha 与 Beta\n\n离线研究已完成。",
        }

    builder = StateGraph(ResearchState)
    builder.add_node("research", research)
    builder.add_edge(START, "research")
    builder.add_edge("research", END)
    return builder


def _sqlite_runner(
    data_root: Path,
    *,
    research_graph: StateGraph[Any, Any, Any, Any] | None = None,
) -> ApplicationRunner:
    return ApplicationRunner(
        OpenCompeteSettings(data_dir=data_root),
        research_graph=research_graph or _offline_research_graph(),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )


def _damage_manifest(data_root: Path, run_id: str, damage: str) -> Path:
    manifest_path = data_root / "runs" / run_id / "manifest.json"
    if damage == "missing":
        manifest_path.unlink()
    else:
        manifest_path.write_text(
            '{"api_key":"SECRET-MANIFEST-RECOVERY-SENTINEL"',
            encoding="utf-8",
        )
    return manifest_path


def _capture_reconcile_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> list[ReconcileAction]:
    actions: list[ReconcileAction] = []

    def recording_reconcile(checkpoint: Any, artifacts: Any) -> Any:
        decision = reconcile(checkpoint, artifacts)
        actions.append(decision.action)
        return decision

    monkeypatch.setattr(
        application_module,
        "reconcile",
        recording_reconcile,
        raising=False,
    )
    return actions


def _runner(
    tmp_path: Path,
    *,
    runtime: RecordingGraphRuntime | None = None,
    repository: RunRepository | None = None,
) -> tuple[ApplicationRunner, RecordingGraphRuntime, RunRepository]:
    settings = OpenCompeteSettings(data_dir=tmp_path)
    checkpoint_store = (
        runtime.checkpoint_store if runtime is not None else RecordingCheckpointStore()
    )
    graph_runtime = runtime or RecordingGraphRuntime(checkpoint_store)
    run_repository = repository or RunRepository(tmp_path)
    runner = ApplicationRunner(
        settings,
        repository=run_repository,
        checkpoint_store=checkpoint_store,
        execution_ledger=FakeExecutionLedger(),
        graph_factory=graph_runtime.build,
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    return runner, graph_runtime, run_repository


def _attempt_runner(
    tmp_path: Path,
    *,
    repository: RunRepository | None = None,
) -> tuple[ApplicationRunner, AttemptGraphRuntime, RunRepository]:
    checkpoint_store = RecordingCheckpointStore()
    runtime = AttemptGraphRuntime(checkpoint_store)
    run_repository = repository or RunRepository(tmp_path)
    runner = ApplicationRunner(
        OpenCompeteSettings(data_dir=tmp_path),
        repository=run_repository,
        checkpoint_store=checkpoint_store,
        graph_factory=runtime.build,
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    return runner, runtime, run_repository


@pytest.mark.asyncio
async def test_create_run_publishes_draft_then_waits_for_confirmation(
    tmp_path: Path,
) -> None:
    runner, runtime, repository = _runner(tmp_path)

    handle = await runner.create_run(_request())

    assert handle.status is RunStatus.AWAITING_CONFIRMATION
    assert handle.pending_confirmation is True
    assert handle.scope is not None and handle.scope.frozen is False
    assert repository.load_manifest(handle.run_id).status is RunStatus.AWAITING_CONFIRMATION
    assert runtime.received_callbacks is True
    assert runtime.checkpoint_store.entries == 1
    assert runtime.checkpoint_store.active is False


@pytest.mark.asyncio
async def test_discovery_page_size_is_independent_from_upstream_search_config(
    tmp_path: Path,
) -> None:
    checkpoint_store = RecordingCheckpointStore()
    runtime = RecordingGraphRuntime(checkpoint_store)
    settings = OpenCompeteSettings(
        data_dir=tmp_path,
        tavily_max_results_per_query=5,
        discovery_search_results_per_query=8,
    )
    runner = ApplicationRunner(
        settings,
        repository=RunRepository(tmp_path),
        checkpoint_store=checkpoint_store,
        execution_ledger=FakeExecutionLedger(),
        graph_factory=runtime.build,
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )

    await runner.create_run(_request())

    assert runtime.build_kwargs is not None
    assert runtime.build_kwargs["discovery_search_results_per_query"] == 8
    assert runtime.last_configurable is not None
    assert runtime.last_configurable["tavily_max_results_per_query"] == 5


@pytest.mark.asyncio
async def test_create_commission_run_uses_injected_dynamic_planner(
    tmp_path: Path,
) -> None:
    class FakeScopePlanner:
        def __init__(self) -> None:
            self.calls: list[ResearchCommission] = []

        async def plan(
            self,
            commission: ResearchCommission,
            *,
            config: object | None = None,
        ) -> ScopeProposal:
            del config
            self.calls.append(commission)
            return ScopeProposal(
                intent_summary="比较 Trae 与同类 AI IDE 的产品取舍",
                levels=(AnalysisLevel.PRODUCT_SERVICE,),
                dimensions={
                    AnalysisLevel.PRODUCT_SERVICE: (
                        "代码库上下文连续性",
                        "Agent 编程工作流",
                    )
                },
            )

    planner = FakeScopePlanner()
    runner = ApplicationRunner(
        OpenCompeteSettings(data_dir=tmp_path),
        research_graph=_offline_research_graph(),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
        scope_planner=planner,
    )
    commission = ResearchCommission(
        query="调研 Trae 的 AI IDE 竞品",
        entities=(ResearchEntity(name="Trae"),),
        focus_points=("代码库上下文连续性",),
    )

    handle = await runner.create_commission_run(commission)

    assert planner.calls == [commission]
    assert handle.status is RunStatus.AWAITING_CONFIRMATION
    assert handle.scope is not None
    assert handle.scope.dimensions == {
        AnalysisLevel.PRODUCT_SERVICE: (
            "代码库上下文连续性",
            "Agent 编程工作流",
        )
    }


@pytest.mark.asyncio
async def test_inspect_run_reads_checkpoint_without_resuming_or_writing_manifest(
    tmp_path: Path,
) -> None:
    runner, runtime, repository = _runner(tmp_path)
    run_id = "inspection-only"
    scope = build_draft_scope(_request())
    runtime.states[run_id] = {
        "run_id": run_id,
        "request": _request().model_dump(mode="json"),
        "scope": scope.model_dump(mode="json"),
        "status": RunStatus.AWAITING_CONFIRMATION.value,
        "confirmation_kind": "scope_confirmation",
        "__interrupt__": (object(),),
    }
    repository.initialize(
        RunManifest(
            run_id=run_id,
            status=RunStatus.AWAITING_CONFIRMATION,
            scope_revision=0,
            artifact_revision=0,
        )
    )
    manifest_path = tmp_path / "runs" / run_id / "manifest.json"
    before = manifest_path.read_bytes()

    inspection = await runner.inspect_run(run_id)

    assert inspection.handle.pending_confirmation is True
    assert inspection.handle.confirmation_kind is not None
    assert inspection.handle.scope == scope
    assert inspection.recoverable is False
    assert runtime.resume_calls == 0
    assert manifest_path.read_bytes() == before


@pytest.mark.asyncio
async def test_freeze_confirmed_scope_persists_scope_frozen_before_execution(
    tmp_path: Path,
) -> None:
    runner = _sqlite_runner(tmp_path)
    request = ResearchRequest(
        query="调研 Trae 的 AI IDE 竞品",
        entities=(ResearchEntity(name="Trae"),),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={
            AnalysisLevel.PRODUCT_SERVICE: ("代码库上下文连续性", "Agent 编程工作流")
        },
    )
    pending = await runner.create_run(request)
    assert pending.scope is not None

    frozen = await runner.freeze_confirmed_scope(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
            dimensions={
                AnalysisLevel.PRODUCT_SERVICE: ("个人版配额与使用成本",)
            },
        ),
    )
    inspection = await runner.inspect_run(pending.run_id)
    manifest = RunRepository(tmp_path).load_manifest(pending.run_id)

    assert frozen.status is RunStatus.SCOPE_FROZEN
    assert frozen.scope is not None and frozen.scope.frozen is True
    assert frozen.scope.dimensions[AnalysisLevel.PRODUCT_SERVICE] == (
        "个人版配额与使用成本",
    )
    assert inspection.handle.status is RunStatus.SCOPE_FROZEN
    assert inspection.handle.scope == frozen.scope
    assert manifest.status is RunStatus.SCOPE_FROZEN
    assert manifest.scope_revision == frozen.scope.revision


@pytest.mark.asyncio
async def test_explicit_resume_continues_frozen_checkpoint_once_after_read_only_inspection(
    tmp_path: Path,
) -> None:
    """读取冻结 Checkpoint 不执行；只有显式恢复才继续一次研究。"""
    research_calls: list[str] = []
    research_graph = _offline_research_graph(research_calls)
    first_runner = _sqlite_runner(tmp_path, research_graph=research_graph)
    request = ResearchRequest(
        query="比较 Alpha 与 Beta 的产品能力",
        entities=(ResearchEntity(name="Alpha"), ResearchEntity(name="Beta")),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
    )
    pending = await first_runner.create_run(request)
    assert pending.scope is not None

    frozen = await first_runner.freeze_confirmed_scope(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    manifest_path = tmp_path / "runs" / pending.run_id / "manifest.json"
    before_inspection = manifest_path.read_bytes()

    # 新 Runner 模拟 API 进程重启；检查只能读取 Checkpoint，不能触发上游节点。
    reopened_runner = _sqlite_runner(tmp_path, research_graph=research_graph)
    inspection = await reopened_runner.inspect_run(pending.run_id)

    assert frozen.status is RunStatus.SCOPE_FROZEN
    assert inspection.handle.status is RunStatus.SCOPE_FROZEN
    assert inspection.recoverable is True
    assert research_calls == []
    assert manifest_path.read_bytes() == before_inspection

    resumed = await reopened_runner.resume_execution(pending.run_id)
    repeated = await reopened_runner.resume_execution(pending.run_id)

    assert resumed.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}
    assert repeated.status is resumed.status
    assert research_calls == ["research"]


@pytest.mark.asyncio
async def test_create_fixed_scope_run_publishes_through_same_repository(
    tmp_path: Path,
) -> None:
    """固定入口只注入 freeze_scope 状态，终态仍由正式 Runner 发布。"""
    checkpoint_store = RecordingCheckpointStore()
    runtime = FixedScopeGraphRuntime(checkpoint_store)
    runner, _, repository = _runner(tmp_path, runtime=runtime)
    base_request = ResearchRequest(
        query="比较甲与乙",
        entities=(ResearchEntity(name="甲"), ResearchEntity(name="乙")),
    )
    draft = build_draft_scope(base_request)
    request = base_request.model_copy(update={"dimensions": dict(draft.dimensions)})
    scope = draft.model_copy(
        update={
            "confirmed_at": application_module.utc_now(),
            "revision": 1,
            "frozen": True,
        }
    )

    handle = await runner.create_fixed_scope_run(
        request,
        scope,
        benchmark_case_id="ocb-fixed-test",
    )

    assert runtime.updated_as_node == "freeze_scope"
    assert runtime.update_values is not None
    assert runtime.update_values["status"] == RunStatus.SCOPE_FROZEN.value
    assert handle.status is RunStatus.COMPLETED
    published = repository.load_verified(handle.run_id)
    assert published.bundle.scope == scope
    assert published.manifest.config_snapshot["benchmark_case_id"] == "ocb-fixed-test"
    assert published.manifest.config_snapshot["task_input_sha256"] == hashlib.sha256(
        request.query.encode("utf-8")
    ).hexdigest()
    assert runtime.last_configurable is not None
    for field_name in (
        "max_researcher_iterations",
        "max_supervisor_model_turns",
        "max_react_tool_calls",
        "max_research_tool_calls",
    ):
        assert published.manifest.config_snapshot[field_name] == (
            runtime.last_configurable[field_name]
        )
    assert runtime.received_callbacks is True
    assert runtime.checkpoint_store.entries == 1
    assert runtime.checkpoint_store.active is False


@pytest.mark.asyncio
async def test_same_run_resume_is_serialized_and_returns_published_result(
    tmp_path: Path,
) -> None:
    store = RecordingCheckpointStore()
    runtime = RecordingGraphRuntime(store, resume_delay=0.05)
    runner, _, repository = _runner(tmp_path, runtime=runtime)
    pending = await runner.create_run(_request())
    assert pending.scope is not None
    confirmation = ScopeConfirmation(
        accepted=True,
        expected_revision=pending.scope.revision,
    )

    first, second = await asyncio.gather(
        runner.resume_run(pending.run_id, confirmation),
        runner.resume_run(pending.run_id, confirmation),
    )

    assert first.status is RunStatus.COMPLETED
    assert second.status is RunStatus.COMPLETED
    assert runtime.max_active_resumes == 1
    assert runtime.resume_calls == 1
    assert repository.load_verified(pending.run_id).bundle.status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_same_process_lock_is_shared_by_two_runner_instances(
    tmp_path: Path,
) -> None:
    store = RecordingCheckpointStore()
    runtime = RecordingGraphRuntime(store, resume_delay=0.05)
    repository = RunRepository(tmp_path)
    first, _, _ = _runner(
        tmp_path,
        runtime=runtime,
        repository=repository,
    )
    second, _, _ = _runner(
        tmp_path,
        runtime=runtime,
        repository=repository,
    )
    pending = await first.create_run(_request())
    assert pending.scope is not None
    confirmation = ScopeConfirmation(
        accepted=True,
        expected_revision=pending.scope.revision,
    )

    results = await asyncio.gather(
        first.resume_run(pending.run_id, confirmation),
        second.resume_run(pending.run_id, confirmation),
    )

    assert [result.status for result in results] == [
        RunStatus.COMPLETED,
        RunStatus.COMPLETED,
    ]
    assert runtime.max_active_resumes == 1
    assert runtime.resume_calls == 1


@pytest.mark.asyncio
async def test_resume_without_input_reports_the_durable_confirmation_point(
    tmp_path: Path,
) -> None:
    runner, runtime, _ = _runner(tmp_path)
    pending = await runner.create_run(_request())

    restored = await runner.resume_run(pending.run_id)

    assert restored.pending_confirmation is True
    assert restored.scope == pending.scope
    assert runtime.resume_calls == 0


@pytest.mark.parametrize("damage", ["missing", "invalid_json"])
@pytest.mark.asyncio
async def test_resume_recovers_awaiting_manifest_from_sqlite_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    secret = "SECRET-MANIFEST-RECOVERY-SENTINEL"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    pending = await _sqlite_runner(tmp_path).create_run(_request())
    assert pending.scope is not None
    manifest_path = _damage_manifest(tmp_path, pending.run_id, damage)

    restored = await _sqlite_runner(tmp_path).resume_run(pending.run_id)

    manifest = RunRepository(tmp_path).load_manifest(pending.run_id)
    assert restored.pending_confirmation is True
    assert restored.scope == pending.scope
    assert manifest.status is RunStatus.AWAITING_CONFIRMATION
    assert manifest.scope_revision == pending.scope.revision
    assert manifest.artifact_revision == 0
    assert manifest.config_snapshot["recovered_from_checkpoint"] is True
    assert manifest.config_snapshot["original_config_available"] is False
    assert "resume_config" in manifest.config_snapshot
    assert manifest.config_snapshot["task_input_sha256"] == hashlib.sha256(
        _request().query.encode("utf-8")
    ).hexdigest()
    assert secret.encode() not in manifest_path.read_bytes()
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


@pytest.mark.parametrize("damage", ["missing", "invalid_json"])
@pytest.mark.asyncio
async def test_resume_recovers_researching_manifest_from_sqlite_checkpoint(
    tmp_path: Path,
    damage: str,
) -> None:
    class ResearchState(MessagesState):
        raw_notes: list[str]
        final_report: str

    visits: list[str] = []

    def research(state: ResearchState) -> dict[str, Any]:
        del state
        visits.append("research")
        interrupt({"type": "research_wait"})
        return {}

    research_graph = StateGraph(ResearchState)
    research_graph.add_node("research", research)
    research_graph.add_edge(START, "research")
    research_graph.add_edge("research", END)
    first_process = _sqlite_runner(tmp_path, research_graph=research_graph)
    pending = await first_process.create_run(_request())
    assert pending.scope is not None
    researching = await first_process.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    assert researching.status is RunStatus.RESEARCHING
    assert researching.scope is not None and researching.scope.revision == 1
    _damage_manifest(tmp_path, pending.run_id, damage)

    restored = await _sqlite_runner(
        tmp_path,
        research_graph=research_graph,
    ).resume_run(pending.run_id)

    manifest = RunRepository(tmp_path).load_manifest(pending.run_id)
    assert restored.status is RunStatus.RESEARCHING
    assert restored.scope == researching.scope
    assert manifest.status is RunStatus.RESEARCHING
    assert manifest.scope_revision == researching.scope.revision
    assert manifest.artifact_revision == 0
    assert visits == ["research"]


@pytest.mark.asyncio
async def test_resume_does_not_rebuild_manifest_without_checkpoint(
    tmp_path: Path,
) -> None:
    run_id = "missing-checkpoint-run"

    with pytest.raises(PersistenceError, match="Checkpoint"):
        await _sqlite_runner(tmp_path).resume_run(run_id)

    assert not (tmp_path / "runs" / run_id / "manifest.json").exists()


@pytest.mark.asyncio
async def test_awaiting_checkpoint_overrides_cancelled_manifest(
    tmp_path: Path,
) -> None:
    first_process = _sqlite_runner(tmp_path)
    pending = await first_process.create_run(_request())
    repository = RunRepository(tmp_path)
    current = repository.load_manifest(pending.run_id)
    repository.update_manifest(
        current.model_copy(update={"status": RunStatus.CANCELLED})
    )

    restored = await _sqlite_runner(tmp_path).resume_run(pending.run_id)

    assert restored.status is RunStatus.AWAITING_CONFIRMATION
    assert restored.pending_confirmation is True
    assert restored.scope == pending.scope
    assert repository.load_manifest(pending.run_id).status is (
        RunStatus.AWAITING_CONFIRMATION
    )


@pytest.mark.asyncio
async def test_awaiting_checkpoint_rejects_self_consistent_published_artifacts(
    tmp_path: Path,
) -> None:
    first_process = _sqlite_runner(tmp_path)
    pending = await first_process.create_run(_request())
    assert pending.scope is not None
    repository = RunRepository(tmp_path)
    current = repository.load_manifest(pending.run_id)
    frozen = freeze_scope(
        pending.scope,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    forged_bundle = RunBundle(
        run_id=pending.run_id,
        scope=frozen,
        report=ReportArtifact(markdown="# 伪发布报告\n\n不来自 Checkpoint。"),
        budget=current.budget_summary,
        status=RunStatus.COMPLETED,
    )
    repository.publish(
        forged_bundle,
        current.model_copy(
            update={
                "status": RunStatus.COMPLETED,
                "scope_revision": frozen.revision,
                "artifact_revision": forged_bundle.artifact_revision,
                "budget_summary": forged_bundle.budget,
            }
        ),
    )
    published_before = repository.load_verified(pending.run_id)

    with pytest.raises(PersistenceError, match="对账冲突"):
        await _sqlite_runner(tmp_path).resume_run(pending.run_id)

    assert repository.load_verified(pending.run_id) == published_before
    checkpoint_index = await first_process._manifest_from_checkpoint(
        pending.run_id
    )
    assert checkpoint_index.status is RunStatus.AWAITING_CONFIRMATION
    assert checkpoint_index.scope_revision == pending.scope.revision


@pytest.mark.asyncio
async def test_manifest_revision_ahead_is_conflict_without_manifest_rewrite(
    tmp_path: Path,
) -> None:
    first_process = _sqlite_runner(tmp_path)
    pending = await first_process.create_run(_request())
    repository = RunRepository(tmp_path)
    current = repository.load_manifest(pending.run_id)
    repository.update_manifest(
        current.model_copy(
            update={
                "status": RunStatus.RESEARCHING,
                "scope_revision": 99,
                "artifact_revision": 99,
            }
        )
    )
    conflicting = repository.load_manifest(pending.run_id)

    with pytest.raises(PersistenceError, match="对账冲突"):
        await _sqlite_runner(tmp_path).resume_run(pending.run_id)

    assert repository.load_manifest(pending.run_id) == conflicting
    checkpoint_index = await first_process._manifest_from_checkpoint(
        pending.run_id
    )
    assert checkpoint_index.status is RunStatus.AWAITING_CONFIRMATION
    assert checkpoint_index.scope_revision == 0
    assert checkpoint_index.artifact_revision == 0


@pytest.mark.asyncio
async def test_terminal_checkpoint_rebuilds_cancelled_manifest_same_revision(
    tmp_path: Path,
) -> None:
    first_process = _sqlite_runner(tmp_path)
    pending = await first_process.create_run(_request())
    assert pending.scope is not None
    original = await first_process.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    assert original.bundle is not None
    assert original.status is RunStatus.PARTIAL
    repository = RunRepository(tmp_path)
    published_before = repository.load_verified(pending.run_id)
    repository.update_manifest(
        published_before.manifest.model_copy(
            update={"status": RunStatus.CANCELLED, "file_hashes": {}}
        )
    )

    rebuilt = await _sqlite_runner(tmp_path).resume_run(pending.run_id)

    assert rebuilt.status is RunStatus.PARTIAL
    assert rebuilt.bundle == original.bundle
    assert rebuilt.bundle.artifact_revision == original.bundle.artifact_revision
    published_after = repository.load_verified(pending.run_id)
    assert published_after.bundle == published_before.bundle
    assert (
        published_after.manifest.artifact_revision
        == published_before.manifest.artifact_revision
    )
    assert published_after.manifest.file_hashes == published_before.manifest.file_hashes


@pytest.mark.asyncio
async def test_reconcile_noop_and_history_only_return_verified_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_process = _sqlite_runner(tmp_path)
    pending = await first_process.create_run(_request())
    assert pending.scope is not None
    original = await first_process.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    assert original.bundle is not None
    actions = _capture_reconcile_actions(monkeypatch)

    noop = await _sqlite_runner(tmp_path).resume_run(pending.run_id)

    assert noop.bundle == original.bundle
    assert actions == [ReconcileAction.NOOP]
    history_root = tmp_path / "history-only"
    shutil.copytree(
        tmp_path / "runs" / pending.run_id,
        history_root / "runs" / pending.run_id,
    )

    history_only = await _sqlite_runner(history_root).resume_run(pending.run_id)

    assert history_only.bundle == original.bundle
    assert actions == [ReconcileAction.NOOP, ReconcileAction.HISTORY_ONLY]
    assert not (history_root / "checkpoints.sqlite3").exists()


@pytest.mark.parametrize("damage", ["missing", "invalid_json"])
@pytest.mark.asyncio
async def test_terminal_checkpoint_republishes_damaged_manifest_without_new_revision(
    tmp_path: Path,
    damage: str,
) -> None:
    first_process = _sqlite_runner(tmp_path)
    pending = await first_process.create_run(_request())
    assert pending.scope is not None
    original = await first_process.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    assert original.bundle is not None
    assert original.status is RunStatus.PARTIAL
    repository = RunRepository(tmp_path)
    original_published = repository.load_verified(pending.run_id)
    run_dir = tmp_path / "runs" / pending.run_id
    bundle_path = run_dir / "bundle.json"
    report_path = run_dir / "report.md"
    original_bundle_bytes = bundle_path.read_bytes()
    report_path.write_text("# 被篡改的旧报告", encoding="utf-8")
    _damage_manifest(tmp_path, pending.run_id, damage)

    rebuilt = await _sqlite_runner(tmp_path).resume_run(pending.run_id)

    assert rebuilt.status is RunStatus.PARTIAL
    assert rebuilt.bundle == original.bundle
    assert rebuilt.bundle.artifact_revision == original.bundle.artifact_revision
    published = repository.load_verified(pending.run_id)
    assert published.manifest.status is RunStatus.PARTIAL
    assert published.manifest.scope_revision == original_published.manifest.scope_revision
    assert (
        published.manifest.artifact_revision
        == original_published.manifest.artifact_revision
        == original.bundle.artifact_revision
    )
    assert published.manifest.file_hashes == original_published.manifest.file_hashes
    assert published.bundle == original.bundle
    assert published.report == original.bundle.report.markdown
    assert bundle_path.read_bytes() == original_bundle_bytes
    assert report_path.read_text(encoding="utf-8") == original.bundle.report.markdown
    for checkpoint_file in tmp_path.glob("checkpoints.sqlite3*"):
        checkpoint_file.unlink()

    history_only = await _sqlite_runner(tmp_path).resume_run(pending.run_id)

    assert history_only.bundle == original.bundle
    assert not (tmp_path / "checkpoints.sqlite3").exists()


@pytest.mark.asyncio
async def test_manifest_recovery_is_serialized_across_runner_instances(
    tmp_path: Path,
) -> None:
    first_process = _sqlite_runner(tmp_path)
    second_process = _sqlite_runner(tmp_path)
    pending = await first_process.create_run(_request())
    (tmp_path / "runs" / pending.run_id / "manifest.json").unlink()

    first, second = await asyncio.gather(
        first_process.resume_run(pending.run_id),
        second_process.resume_run(pending.run_id),
    )

    assert first.pending_confirmation is True
    assert second.pending_confirmation is True
    assert RunRepository(tmp_path).load_manifest(pending.run_id).status is (
        RunStatus.AWAITING_CONFIRMATION
    )


@pytest.mark.asyncio
async def test_partial_graph_result_is_published_as_partial(tmp_path: Path) -> None:
    store = RecordingCheckpointStore()
    runtime = RecordingGraphRuntime(store, terminal_status=RunStatus.PARTIAL)
    runner, _, repository = _runner(tmp_path, runtime=runtime)
    pending = await runner.create_run(_request())
    assert pending.scope is not None

    result = await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )

    assert result.status is RunStatus.PARTIAL
    assert repository.load_verified(pending.run_id).manifest.status is RunStatus.PARTIAL


@pytest.mark.asyncio
async def test_publish_failure_is_recorded_as_failed_never_completed(
    tmp_path: Path,
) -> None:
    repository = FailingPublishRepository(tmp_path)
    runner, _, _ = _runner(tmp_path, repository=repository)
    pending = await runner.create_run(_request())
    assert pending.scope is not None

    with pytest.raises(PersistenceError, match="模拟最终发布失败"):
        await runner.resume_run(
            pending.run_id,
            ScopeConfirmation(
                accepted=True,
                expected_revision=pending.scope.revision,
            ),
        )

    manifest = repository.load_manifest(pending.run_id)
    assert manifest.status is RunStatus.FAILED
    assert manifest.file_hashes == {}
    assert manifest.errors[-1].code == "persistence_error"


@pytest.mark.asyncio
async def test_resume_rebuilds_terminal_artifacts_after_publish_failure(
    tmp_path: Path,
) -> None:
    repository = FailOncePublishRepository(tmp_path)
    runner, runtime, _ = _runner(tmp_path, repository=repository)
    pending = await runner.create_run(_request())
    assert pending.scope is not None
    confirmation = ScopeConfirmation(
        accepted=True,
        expected_revision=pending.scope.revision,
    )
    with pytest.raises(PersistenceError, match="发布崩溃窗口"):
        await runner.resume_run(pending.run_id, confirmation)

    rebuilt = await runner.resume_run(pending.run_id)

    assert rebuilt.status is RunStatus.COMPLETED
    assert rebuilt.bundle is not None
    assert repository.publish_attempts == 2
    assert runtime.resume_calls == 1
    assert repository.load_verified(pending.run_id).manifest.status is (
        RunStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_resume_repairs_damaged_report_from_trusted_bundle(
    tmp_path: Path,
) -> None:
    runner, runtime, repository = _runner(tmp_path)
    pending = await runner.create_run(_request())
    assert pending.scope is not None
    await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    report_path = tmp_path / "runs" / pending.run_id / "report.md"
    report_path.write_text("已损坏", encoding="utf-8")

    repaired = await runner.resume_run(pending.run_id)

    assert repaired.status is RunStatus.COMPLETED
    assert repaired.bundle is not None
    assert report_path.read_text(encoding="utf-8") == repaired.bundle.report.markdown
    assert runtime.resume_calls == 1
    assert repository.load_verified(pending.run_id).bundle == repaired.bundle


@pytest.mark.asyncio
async def test_resume_rebuilds_damaged_bundle_from_terminal_checkpoint(
    tmp_path: Path,
) -> None:
    runner, runtime, repository = _runner(tmp_path)
    pending = await runner.create_run(_request())
    assert pending.scope is not None
    original = await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    bundle_path = tmp_path / "runs" / pending.run_id / "bundle.json"
    bundle_path.write_text("不是合法 JSON", encoding="utf-8")

    rebuilt = await runner.resume_run(pending.run_id)

    assert rebuilt.status is RunStatus.COMPLETED
    assert rebuilt.bundle == original.bundle
    assert runtime.resume_calls == 1
    assert repository.load_verified(pending.run_id).bundle == original.bundle


@pytest.mark.asyncio
async def test_cancelled_confirmation_updates_manifest_without_research_bundle(
    tmp_path: Path,
) -> None:
    runner, _, repository = _runner(tmp_path)
    pending = await runner.create_run(_request())
    assert pending.scope is not None

    cancelled = await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=False,
            expected_revision=pending.scope.revision,
        ),
    )

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.bundle is None
    assert repository.load_manifest(pending.run_id).status is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_list_and_load_use_only_the_run_repository(tmp_path: Path) -> None:
    runner, _, repository = _runner(tmp_path)
    pending = await runner.create_run(_request())
    assert pending.scope is not None
    await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )

    summaries = await runner.list_runs()
    bundle = await runner.load_run(pending.run_id)

    assert [item.run_id for item in summaries] == [pending.run_id]
    assert bundle == repository.load_verified(pending.run_id).bundle


@pytest.mark.asyncio
async def test_real_graph_and_sqlite_resume_after_saver_is_reopened(
    tmp_path: Path,
) -> None:
    class ResearchState(MessagesState):
        raw_notes: list[str]
        final_report: str

    async def research(state: ResearchState):
        del state
        return {
            "messages": [AIMessage(content="研究完成")],
            "raw_notes": [],
            "final_report": "# Alpha 与 Beta\n\n离线研究已完成。",
        }

    research_graph = StateGraph(ResearchState)
    research_graph.add_node("research", research)
    research_graph.add_edge(START, "research")
    research_graph.add_edge("research", END)
    settings = OpenCompeteSettings(data_dir=tmp_path)
    extractor = StaticArtifactExtractor(ArtifactExtraction())

    first_process = ApplicationRunner(
        settings,
        research_graph=research_graph,
        extractor=extractor,
    )
    pending = await first_process.create_run(_request())
    assert pending.scope is not None
    assert b"opencompete.models" not in _checkpoint_payloads(tmp_path)

    second_process = ApplicationRunner(
        settings,
        research_graph=research_graph,
        extractor=extractor,
    )
    restored = await second_process.resume_run(pending.run_id)
    completed = await second_process.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=restored.scope.revision,
        ),
    )

    assert restored.pending_confirmation is True
    assert completed.status is RunStatus.PARTIAL
    assert completed.bundle is not None
    assert b"opencompete.models" not in _checkpoint_payloads(tmp_path)
    assert (await second_process.load_run(pending.run_id)).report.markdown.startswith(
        "# Alpha"
    )


@pytest.mark.asyncio
async def test_budget_rejection_materializes_partial_without_another_provider_call(
    tmp_path: Path,
) -> None:
    class ResearchState(MessagesState):
        raw_notes: list[str]
        final_report: str

    calls = 0

    async def research(state: ResearchState):
        nonlocal calls
        del state
        calls += 1
        raise BudgetExceededError("测试预算不足")

    research_graph = StateGraph(ResearchState)
    research_graph.add_node("research", research)
    research_graph.add_edge(START, "research")
    research_graph.add_edge("research", END)
    settings = OpenCompeteSettings(data_dir=tmp_path)
    runner = ApplicationRunner(
        settings,
        research_graph=research_graph,
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    pending = await runner.create_run(_request())
    assert pending.scope is not None

    partial = await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )

    assert calls == 1
    assert partial.status is RunStatus.PARTIAL
    assert partial.bundle is not None
    assert any(
        "预算已耗尽" in limitation
        for limitation in partial.bundle.report.limitations
    )
    assert RunRepository(tmp_path).load_verified(pending.run_id).manifest.status is (
        RunStatus.PARTIAL
    )


@pytest.mark.asyncio
async def test_application_persists_redacted_attempt_events_across_executions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = "sk-application-attempt-secret-sentinel"
    forbidden_values = (
        api_key,
        "真实研究主题不能进入持久化",
        "供应商异常正文不能进入持久化",
    )
    monkeypatch.setenv("OPENAI_API_KEY", api_key)
    runner, runtime, _ = _attempt_runner(tmp_path)

    pending = await runner.create_run(_request())
    assert pending.scope is not None
    completed = await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )

    assert completed.status is RunStatus.COMPLETED
    assert len(runtime.execution_ids) == 2
    assert runtime.execution_ids[0] != runtime.execution_ids[1]
    event_path = tmp_path / "runs" / pending.run_id / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    started = [event for event in events if event["event"] == "call_attempt_started"]
    succeeded = [
        event for event in events if event["event"] == "call_attempt_succeeded"
    ]
    failed = [event for event in events if event["event"] == "call_attempt_failed"]
    graph_started = [
        event for event in events if event["event"] == "graph_execution_started"
    ]

    assert [event["execution_id"] for event in graph_started] == runtime.execution_ids
    assert [event["mode"] for event in graph_started] == ["invoke", "resume"]
    assert [event["execution_id"] for event in started] == [
        runtime.execution_ids[0],
        runtime.execution_ids[1],
        runtime.execution_ids[1],
    ]
    assert [event["attempt_number"] for event in started] == [1, 2, 3]
    assert [event["retry_number"] for event in started] == [1, 1, 2]
    assert [event["replayed"] for event in started] == [False, True, False]
    assert all(event["node"] == "researcher" for event in started)
    assert len({event["unit_id"] for event in started}) == 1
    assert len(str(started[0]["unit_id"])) == 64
    assert len(succeeded) == 2
    assert len(failed) == 1
    assert failed[0]["error_code"] == "unexpected_error"
    assert all(isinstance(event["duration_ms"], int) for event in [*succeeded, *failed])
    lifecycle = [
        event
        for event in events
        if event["event"] in {"graph_invoked", "graph_resumed"}
    ]
    assert [event["execution_id"] for event in lifecycle] == runtime.execution_ids

    for path in tmp_path.rglob("*"):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        if any(value.encode() in payload for value in forbidden_values):
            pytest.fail(f"检测到禁止持久化的内容：{path}", pytrace=False)


@pytest.mark.asyncio
async def test_checkpoint_only_resume_does_not_emit_graph_execution_events(
    tmp_path: Path,
) -> None:
    runner, runtime, _ = _attempt_runner(tmp_path)
    pending = await runner.create_run(_request())
    event_path = tmp_path / "runs" / pending.run_id / "events.jsonl"
    before = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]

    restored = await runner.resume_run(pending.run_id)

    after = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    assert restored.pending_confirmation is True
    assert runtime.execution_ids == [runtime.execution_ids[0]]
    assert sum(event["event"] == "graph_execution_started" for event in after) == sum(
        event["event"] == "graph_execution_started" for event in before
    )
    assert sum(event["event"] == "graph_resumed" for event in after) == sum(
        event["event"] == "graph_resumed" for event in before
    )
    assert sum(event["event"] == "call_attempt_started" for event in after) == sum(
        event["event"] == "call_attempt_started" for event in before
    )


@pytest.mark.asyncio
async def test_attempt_event_sink_failure_does_not_fail_graph_or_ledger(
    tmp_path: Path,
) -> None:
    repository = FailingAttemptEventRepository(tmp_path)
    runner, _, _ = _attempt_runner(tmp_path, repository=repository)

    pending = await runner.create_run(_request())
    assert pending.scope is not None
    completed = await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )

    assert completed.status is RunStatus.COMPLETED
    assert repository.attempt_event_failures == 6
    assert completed.bundle is not None
    # 失败调用已经发送但缺少实际 usage，账本按预估值保守结算。
    assert completed.bundle.budget.spent.input_tokens == 10
    assert completed.bundle.budget.spent.output_tokens == 12
