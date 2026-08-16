"""真实 Runner、SQLite Checkpoint 与生产 FastAPI 装配的离线端到端测试。"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph

import opencompete.application as application_module
from opencompete.api.server import create_app
from opencompete.application import ApplicationRunner
from opencompete.extractors import ArtifactExtraction, StaticArtifactExtractor
from opencompete.models import (
    AnalysisLevel,
    ResearchCommission,
    ResearchEntity,
    ScopeConfirmation,
    ScopeProposal,
)
from opencompete.persistence import RunRepository
from opencompete.settings import OpenCompeteSettings


class _ResearchState(MessagesState):
    """离线研究子图需要保留的最小字段。"""

    raw_notes: list[str]
    final_report: str


class _ScopePlanner:
    """返回固定测试输入的动态中文 Scope，不连接模型。"""

    async def plan(
        self,
        commission: ResearchCommission,
        *,
        config: object | None = None,
    ) -> ScopeProposal:
        """按本次委托生成可编辑的中文维度。"""
        del commission, config
        return ScopeProposal(
            intent_summary="比较 Trae 与同类 AI IDE 的适用取舍",
            levels=(AnalysisLevel.PRODUCT_SERVICE,),
            dimensions={
                AnalysisLevel.PRODUCT_SERVICE: (
                    "代码库上下文连续性",
                    "Agent 编程工作流",
                )
            },
        )


def _research_graph(
    started: threading.Event,
    release: threading.Event,
    finished: threading.Event,
    calls: list[str],
) -> StateGraph[Any, Any, Any, Any]:
    """构造可控暂停的离线研究子图，保留后台任务可观测窗口。"""

    async def research(state: _ResearchState) -> dict[str, Any]:
        del state
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.001)
        calls.append("research")
        finished.set()
        return {
            "messages": [AIMessage(content="离线研究完成")],
            "raw_notes": [
                "--- SOURCE 1: Trae 官方资料 ---\n"
                "URL: https://www.trae.ai/\n"
                "SUMMARY: Trae 是面向开发者的 AI IDE。\n"
                "------------------------------"
            ],
            "final_report": (
                "# Trae 竞品分析\n\n"
                "## 结论摘要\n\n"
                "建议：先结合团队的代码库规模与 Agent 工作流要求选择工具。\n\n"
                "参考资料：<https://www.trae.ai/>"
            ),
        }

    builder = StateGraph(_ResearchState)
    builder.add_node("research", research)
    builder.add_edge(START, "research")
    builder.add_edge("research", END)
    return builder


def _production_app(
    monkeypatch: pytest.MonkeyPatch,
    settings: OpenCompeteSettings,
    research_graph: StateGraph[Any, Any, Any, Any],
    runners: list[ApplicationRunner],
) -> Any:
    """用生产 lifespan 装配实际 Runner，仅替换会访问外网的依赖。"""
    real_runner = ApplicationRunner

    def build_offline_runner(
        server_settings: OpenCompeteSettings,
        *,
        repository: RunRepository,
        progress_sink: Any,
    ) -> ApplicationRunner:
        runner = real_runner(
            server_settings,
            repository=repository,
            progress_sink=progress_sink,
            research_graph=research_graph,
            scope_planner=_ScopePlanner(),
            extractor=StaticArtifactExtractor(ArtifactExtraction()),
        )
        runners.append(runner)
        return runner

    monkeypatch.setattr(application_module, "ApplicationRunner", build_offline_runner)
    return create_app(settings)


async def _create_and_freeze(runner: ApplicationRunner) -> str:
    """创建 Web 委托并只冻结 Checkpoint，模拟后台执行前的进程中断。"""
    pending = await runner.create_commission_run(
        ResearchCommission(
            query="调研 Trae 的 AI IDE 竞品",
            entities=(ResearchEntity(name="Trae"),),
        )
    )
    assert pending.scope is not None
    frozen = await runner.freeze_confirmed_scope(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    assert frozen.scope is not None and frozen.scope.frozen
    return pending.run_id


def _wait_for_background_task(
    client: TestClient,
    app: Any,
    run_id: str,
) -> None:
    """在 TestClient 的所属事件循环中等待已登记的后台研究收尾。"""

    async def wait() -> None:
        task = app.state.registry._tasks.get(run_id)
        assert task is not None
        await task

    client.portal.call(wait)


def test_scope_confirmation_202_durably_freezes_actual_sqlite_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 确认返回 202 前，用户编辑后的 Scope 已耐久冻结到 SQLite。"""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    research_calls: list[str] = []
    settings = OpenCompeteSettings(data_dir=tmp_path)
    runners: list[ApplicationRunner] = []
    app = _production_app(
        monkeypatch,
        settings,
        _research_graph(started, release, finished, research_calls),
        runners,
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/runs",
            json={"query": "调研 Trae 的 AI IDE 竞品", "entities": ["Trae"]},
        )
        assert created.status_code == 200
        draft = created.json()
        assert draft["pending_confirmation"] is True
        assert draft["expected_revision"] == 0

        confirmed = client.post(
            f"/api/runs/{draft['run_id']}/scope-confirmation",
            json={
                "accepted": True,
                "expected_revision": draft["expected_revision"],
                "dimensions": {
                    "product_service": ["团队代码库迁移成本"]
                },
            },
        )

        assert confirmed.status_code == 202
        projection = confirmed.json()
        assert projection["status"] == "scope_frozen"
        assert projection["scope_revision"] == 1
        assert projection["active"] is True
        assert projection["pending_confirmation"] is False
        assert started.wait(timeout=2)

        runner = runners[-1]
        inspection = client.portal.call(runner.inspect_run, draft["run_id"])
        assert inspection.handle.scope is not None
        assert inspection.handle.scope.frozen is True
        assert inspection.handle.scope.revision == 1
        assert inspection.handle.scope.dimensions == {
            AnalysisLevel.PRODUCT_SERVICE: ("团队代码库迁移成本",)
        }
        manifest = RunRepository(tmp_path).load_manifest(draft["run_id"])
        assert manifest.status.value == "scope_frozen"
        assert manifest.scope_revision == 1
        assert (tmp_path / "checkpoints.sqlite3").exists()
        assert not (tmp_path / "runs" / draft["run_id"] / "bundle.json").exists()

        release.set()
        assert finished.wait(timeout=2)
        _wait_for_background_task(client, app, draft["run_id"])

    assert research_calls == ["research"]


def test_reopened_api_get_is_read_only_and_post_resume_executes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新进程读取中断任务无副作用，只有显式恢复才恰好执行一次。"""
    initial_started = threading.Event()
    initial_release = threading.Event()
    initial_finished = threading.Event()
    first_runner = ApplicationRunner(
        OpenCompeteSettings(data_dir=tmp_path),
        repository=RunRepository(tmp_path),
        research_graph=_research_graph(
            initial_started, initial_release, initial_finished, []
        ),
        scope_planner=_ScopePlanner(),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    run_id = asyncio.run(_create_and_freeze(first_runner))
    manifest_path = tmp_path / "runs" / run_id / "manifest.json"
    events_path = tmp_path / "runs" / run_id / "events.jsonl"
    manifest_before = manifest_path.read_bytes()
    events_before = events_path.read_bytes()

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    research_calls: list[str] = []
    settings = OpenCompeteSettings(data_dir=tmp_path)
    reopened_runners: list[ApplicationRunner] = []
    app = _production_app(
        monkeypatch,
        settings,
        _research_graph(started, release, finished, research_calls),
        reopened_runners,
    )

    with TestClient(app) as client:
        assert len(reopened_runners) == 1
        assert reopened_runners[0] is not first_runner

        read = client.get(f"/api/runs/{run_id}")
        assert read.status_code == 200
        assert read.json()["status"] == "scope_frozen"
        assert read.json()["recoverable"] is True
        assert read.json()["active"] is False
        assert not started.is_set()
        assert manifest_path.read_bytes() == manifest_before
        assert events_path.read_bytes() == events_before

        resumed = client.post(f"/api/runs/{run_id}/resume")
        assert resumed.status_code == 202
        assert resumed.json()["active"] is True
        assert started.wait(timeout=2)

        duplicate = client.post(f"/api/runs/{run_id}/resume")
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "run_already_active"
        assert research_calls == []

        release.set()
        assert finished.wait(timeout=2)
        _wait_for_background_task(client, app, run_id)
        terminal = client.get(f"/api/runs/{run_id}")

    assert terminal.status_code == 200
    assert terminal.json()["status"] in {"completed", "partial"}
    assert research_calls == ["research"]
