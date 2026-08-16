"""FastAPI 运行状态投影与显式恢复的离线回归测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from opencompete.api.errors import register_exception_handlers
from opencompete.api.meta import RunMetaStore
from opencompete.api.routes import router
from opencompete.api.tasks import ResearchTaskRegistry
from opencompete.application import CommissionRunStartup, RunInspection
from opencompete.models import (
    AnalysisLevel,
    CandidateSelection,
    ConfirmationKind,
    DiscoveryCandidate,
    ResearchCommission,
    ResearchEntity,
    RunHandle,
    RunManifest,
    RunStatus,
    ScopeConfirmation,
    ScopeSpec,
)
from opencompete.persistence import RunRepository
from opencompete.settings import OpenCompeteSettings


def _scope(*, frozen: bool = False, revision: int = 0) -> ScopeSpec:
    return ScopeSpec(
        entities=(ResearchEntity(name="Trae"),),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={
            AnalysisLevel.PRODUCT_SERVICE: (
                "代码库上下文连续性",
                "Agent 编程工作流",
            )
        },
        exclusions=("未公开路线图",),
        confirmed_at=None if not frozen else datetime.now(UTC),
        revision=revision,
        frozen=frozen,
    )


def _manifest(run_id: str, status: RunStatus, revision: int = 0) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        status=status,
        scope_revision=revision,
        artifact_revision=0,
    )


class _FakeRunner:
    """不触发 Graph、模型或预算的 API 边界替身。"""

    def __init__(self, repository: RunRepository) -> None:
        self.repository = repository
        self.inspections: dict[str, RunInspection] = {}
        self.inspect_calls = 0
        self.resume_calls: list[tuple[str, object | None]] = []
        self.freeze_calls: list[tuple[str, ScopeConfirmation]] = []
        self.resume_execution_calls: list[str] = []
        self.commissions: list[ResearchCommission] = []

    async def inspect_run(self, run_id: str) -> RunInspection:
        self.inspect_calls += 1
        return self.inspections[run_id]

    async def initialize_commission_run(
        self, commission: ResearchCommission
    ) -> CommissionRunStartup:
        self.commissions.append(commission)
        run_id = f"commission-{len(self.commissions)}"
        self.repository.initialize(_manifest(run_id, RunStatus.DRAFT))
        handle = RunHandle(run_id=run_id, status=RunStatus.DRAFT)

        async def execute() -> RunHandle:
            # 模拟后台范围规划：推进 manifest 与检查投影到确认点。
            scope = _scope()
            self.repository.update_manifest(
                _manifest(run_id, RunStatus.AWAITING_CONFIRMATION)
            )
            done = RunHandle(
                run_id=run_id,
                status=RunStatus.AWAITING_CONFIRMATION,
                scope=scope,
                pending_confirmation=True,
                confirmation_kind=ConfirmationKind.SCOPE_CONFIRMATION,
            )
            self.inspections[run_id] = RunInspection(
                handle=done,
                request=None,
                has_confirmation_interrupt=True,
                recoverable=False,
            )
            return done

        return CommissionRunStartup(handle=handle, execution=execute())

    async def resume_run(
        self,
        run_id: str,
        user_input: CandidateSelection | ScopeConfirmation | None = None,
    ) -> RunHandle:
        self.resume_calls.append((run_id, user_input))
        inspection = self.inspections[run_id]
        if isinstance(user_input, CandidateSelection):
            handle = inspection.handle.model_copy(
                update={
                    "confirmation_kind": ConfirmationKind.SCOPE_CONFIRMATION,
                    "discovery_candidates": (),
                }
            )
            self.inspections[run_id] = inspection.__class__(
                handle=handle,
                request=inspection.request,
                has_confirmation_interrupt=True,
                recoverable=False,
            )
            return handle
        if isinstance(user_input, ScopeConfirmation) and not user_input.accepted:
            self.repository.update_manifest(_manifest(run_id, RunStatus.CANCELLED))
            handle = RunHandle(run_id=run_id, status=RunStatus.CANCELLED)
            self.inspections[run_id] = inspection.__class__(
                handle=handle,
                request=inspection.request,
                has_confirmation_interrupt=False,
                recoverable=False,
            )
            return handle
        return inspection.handle

    async def freeze_confirmed_scope(
        self, run_id: str, confirmation: ScopeConfirmation
    ) -> RunHandle:
        self.freeze_calls.append((run_id, confirmation))
        frozen = _scope(frozen=True, revision=confirmation.expected_revision + 1)
        self.repository.update_manifest(
            _manifest(run_id, RunStatus.SCOPE_FROZEN, frozen.revision)
        )
        handle = RunHandle(
            run_id=run_id,
            status=RunStatus.SCOPE_FROZEN,
            scope=frozen,
        )
        self.inspections[run_id] = RunInspection(
            handle=handle,
            request=None,
            has_confirmation_interrupt=False,
            recoverable=True,
        )
        return handle

    async def resume_execution(self, run_id: str) -> RunHandle:
        self.resume_execution_calls.append(run_id)
        return self.inspections[run_id].handle

    async def list_runs(self) -> list[object]:
        return []


def _client(
    tmp_path: Path,
    run_id: str,
    inspection: RunInspection,
) -> tuple[TestClient, _FakeRunner, RunRepository]:
    repository = RunRepository(tmp_path)
    repository.initialize(
        _manifest(
            run_id,
            inspection.handle.status,
            inspection.handle.scope.revision if inspection.handle.scope else 0,
        )
    )
    fake_runner = _FakeRunner(repository)
    fake_runner.inspections[run_id] = inspection

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.state.settings = OpenCompeteSettings(data_dir=tmp_path)
    app.state.repository = repository
    app.state.runner = fake_runner
    app.state.registry = ResearchTaskRegistry(capacity=1)
    app.state.meta_store = RunMetaStore(tmp_path)
    app.state.confirmation_cache = {}
    app.state.hub = SimpleNamespace()
    return TestClient(app), fake_runner, repository


def test_get_projects_durable_candidate_confirmation_without_resuming(
    tmp_path: Path,
) -> None:
    run_id = "candidate-after-restart"
    scope = _scope()
    candidate = DiscoveryCandidate(
        entity=ResearchEntity(name="Cursor", user_provided=False),
        source_url="https://example.com/cursor",
        source_title="Cursor 对比来源",
        summary="用于验证候选确认投影。",
    )
    inspection = RunInspection(
        handle=RunHandle(
            run_id=run_id,
            status=RunStatus.AWAITING_CONFIRMATION,
            scope=scope,
            pending_confirmation=True,
            confirmation_kind=ConfirmationKind.CANDIDATE_SELECTION,
            discovery_candidates=(candidate,),
        ),
        request=None,
        has_confirmation_interrupt=True,
        recoverable=False,
    )
    client, runner, repository = _client(tmp_path, run_id, inspection)
    manifest_path = tmp_path / "runs" / run_id / "manifest.json"
    before = manifest_path.read_bytes()

    response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pending_confirmation"] is True
    assert payload["confirmation_kind"] == "candidate_selection"
    assert payload["expected_revision"] == 0
    assert [item["entity"]["name"] for item in payload["candidates"]] == ["Cursor"]
    assert runner.inspect_calls == 1
    assert runner.resume_calls == []
    assert runner.freeze_calls == []
    assert manifest_path.read_bytes() == before
    assert repository.load_manifest(run_id).status is RunStatus.AWAITING_CONFIRMATION


def _wait_for_confirmation(
    client: TestClient,
    run_id: str,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """轮询详情直到后台创建任务到达确认点（TestClient 请求驱动事件循环）。."""
    deadline = perf_counter() + timeout_seconds
    last: dict[str, Any] = {}
    while perf_counter() < deadline:
        last = client.get(f"/api/runs/{run_id}").json()
        if last.get("pending_confirmation") is True:
            return last
    raise AssertionError(f"后台创建未在限期内到达确认点：{last}")


def test_create_uses_commission_and_rejects_fixed_web_scope_fields(
    tmp_path: Path,
) -> None:
    run_id = "seed"
    inspection = RunInspection(
        handle=RunHandle(run_id=run_id, status=RunStatus.CANCELLED),
        request=None,
        has_confirmation_interrupt=False,
        recoverable=False,
    )
    client, runner, _repository = _client(tmp_path, run_id, inspection)

    created = client.post(
        "/api/runs",
        json={
            "query": "调研 Trae 的 AI IDE 竞品",
            "entities": ["Trae"],
            "focus_points": ["代码库上下文连续性"],
            "exclusions": ["未公开路线图"],
            "discover_candidates": False,
        },
    )
    rejected = client.post(
        "/api/runs",
        json={
            "query": "不应接收固定范围",
            "entities": ["Trae"],
            "levels": ["product_service"],
        },
    )

    # 创建立即返回 202：draft 投影 + 后台执行登记，不等待模型规划。
    assert created.status_code == 202
    draft = created.json()
    assert draft["status"] == "draft"
    assert draft["pending_confirmation"] is False
    assert draft["active"] is True
    assert runner.commissions == [
        ResearchCommission(
            query="调研 Trae 的 AI IDE 竞品",
            entities=(ResearchEntity(name="Trae"),),
            focus_points=("代码库上下文连续性",),
            exclusions=("未公开路线图",),
        )
    ]
    assert rejected.status_code == 422
    assert len(runner.commissions) == 1
    # 后台范围规划完成后，详情投影到达范围确认点。
    detail = _wait_for_confirmation(client, draft["run_id"])
    assert detail["confirmation_kind"] == "scope_confirmation"
    assert detail["expected_revision"] == 0


def test_scope_acceptance_is_frozen_before_202_execution_projection(
    tmp_path: Path,
) -> None:
    run_id = "scope-confirmation"
    scope = _scope()
    inspection = RunInspection(
        handle=RunHandle(
            run_id=run_id,
            status=RunStatus.AWAITING_CONFIRMATION,
            scope=scope,
            pending_confirmation=True,
            confirmation_kind=ConfirmationKind.SCOPE_CONFIRMATION,
        ),
        request=None,
        has_confirmation_interrupt=True,
        recoverable=False,
    )
    client, runner, repository = _client(tmp_path, run_id, inspection)

    response = client.post(
        f"/api/runs/{run_id}/scope-confirmation",
        json={
            "accepted": True,
            "expected_revision": 0,
            "entities": [item.model_dump(mode="json") for item in scope.entities],
            "levels": [item.value for item in scope.levels],
            "dimensions": {
                item.value: list(scope.dimensions[item]) for item in scope.levels
            },
            "exclusions": list(scope.exclusions),
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "scope_frozen"
    assert payload["scope_revision"] == 1
    assert payload["pending_confirmation"] is False
    assert payload["active"] is True
    assert len(runner.freeze_calls) == 1
    assert repository.load_manifest(run_id).status is RunStatus.SCOPE_FROZEN
    assert repository.load_manifest(run_id).scope_revision == 1


def test_wrong_confirmation_kind_returns_conflict_without_resuming(
    tmp_path: Path,
) -> None:
    run_id = "wrong-confirmation-kind"
    inspection = RunInspection(
        handle=RunHandle(
            run_id=run_id,
            status=RunStatus.AWAITING_CONFIRMATION,
            scope=_scope(),
            pending_confirmation=True,
            confirmation_kind=ConfirmationKind.CANDIDATE_SELECTION,
        ),
        request=None,
        has_confirmation_interrupt=True,
        recoverable=False,
    )
    client, runner, repository = _client(tmp_path, run_id, inspection)

    response = client.post(
        f"/api/runs/{run_id}/scope-confirmation",
        json={"accepted": True, "expected_revision": 0},
    )

    assert response.status_code == 409
    assert runner.resume_calls == []
    assert runner.freeze_calls == []
    assert repository.load_manifest(run_id).status is RunStatus.AWAITING_CONFIRMATION


def test_unknown_candidate_is_rejected_before_graph_resume(tmp_path: Path) -> None:
    run_id = "unknown-candidate"
    candidate = DiscoveryCandidate(
        entity=ResearchEntity(name="Cursor", user_provided=False),
        source_url="https://example.com/cursor",
        source_title="Cursor 对比来源",
    )
    inspection = RunInspection(
        handle=RunHandle(
            run_id=run_id,
            status=RunStatus.AWAITING_CONFIRMATION,
            scope=_scope(),
            pending_confirmation=True,
            confirmation_kind=ConfirmationKind.CANDIDATE_SELECTION,
            discovery_candidates=(candidate,),
        ),
        request=None,
        has_confirmation_interrupt=True,
        recoverable=False,
    )
    client, runner, repository = _client(tmp_path, run_id, inspection)

    response = client.post(
        f"/api/runs/{run_id}/candidate-selection",
        json={
            "expected_revision": 0,
            "selected_candidates": [
                {"candidate_id": "not-in-current-checkpoint", "entity_name": "不存在"}
            ],
        },
    )

    assert response.status_code == 409
    assert runner.resume_calls == []
    assert repository.load_manifest(run_id).status is RunStatus.AWAITING_CONFIRMATION
