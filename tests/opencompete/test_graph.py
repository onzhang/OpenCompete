"""外层范围 Graph 的确认、冻结和上游配置契约测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command

from opencompete.analysis import build_draft_scope
from opencompete.budget import BudgetGuard, InMemoryBudgetLedger
from opencompete.errors import DataValidationError
from opencompete.extractors import ArtifactExtraction, StaticArtifactExtractor
from opencompete.graph import build_graph
from opencompete.models import (
    AnalysisLevel,
    BudgetEstimate,
    DiscoveryCandidate,
    DiscoveryRequest,
    ResearchCommission,
    ResearchEntity,
    ResearchRequest,
    RunBundle,
    RunStatus,
    ScopeProposal,
    ScopeSpec,
)


class _BudgetChargingExtractor:
    """模拟 Artifact Extractor 在物化期间完成一次模型预算结算。"""

    def __init__(self, guard: BudgetGuard) -> None:
        self._guard = guard

    async def extract(self, **kwargs: Any) -> ArtifactExtraction:
        del kwargs
        reservation = await self._guard.reserve(
            "artifact-extractor-call",
            BudgetEstimate(input_tokens=10, output_tokens=12),
        )
        await self._guard.settle(
            reservation.reservation_id,
            BudgetEstimate(input_tokens=7, output_tokens=9),
        )
        return ArtifactExtraction()


class _ResearchState(MessagesState):
    raw_notes: list[str]
    final_report: str
    supervisor_termination_reason: str
    unexecuted_research_calls: int


def _request() -> ResearchRequest:
    return ResearchRequest(
        query="比较 Alpha 与 Beta",
        entities=(ResearchEntity(name="Alpha"), ResearchEntity(name="Beta")),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
    )


def _request_with_url(url: str) -> ResearchRequest:
    return _request().model_copy(update={"allowed_urls": (url,)})


class _DiscoveryProvider:
    async def discover(
        self,
        request: DiscoveryRequest,
        budget: BudgetGuard,
        *,
        config: object | None = None,
    ) -> list[DiscoveryCandidate]:
        del request, budget, config
        return [
            DiscoveryCandidate(
                entity=ResearchEntity(name="候选页面标题", user_provided=False),
                source_url="https://example.com/candidate",
                source_title="候选来源页",
                summary="来源摘要",
            )
        ]


class _ScopePlanner:
    """离线 Planner：只返回测试明确给定的动态中文范围。"""

    def __init__(self, proposal: ScopeProposal) -> None:
        self.proposal = proposal
        self.commissions: list[ResearchCommission] = []

    async def plan(
        self,
        commission: ResearchCommission,
        *,
        config: RunnableConfig | None = None,
    ) -> ScopeProposal:
        del config
        self.commissions.append(commission)
        return self.proposal


def _scope(state: dict[str, Any]) -> ScopeSpec:
    return ScopeSpec.model_validate(state["scope"])


def _bundle(state: dict[str, Any]) -> RunBundle:
    return RunBundle.model_validate(state["bundle"])


def _config(thread_id: str = "run-graph-1") -> RunnableConfig:
    return {
        "configurable": {
            "thread_id": thread_id,
            "allow_clarification": False,
            "search_api": "tavily",
            "mcp_config": None,
        }
    }


def _fake_research_graph(
    calls: list[dict[str, Any]],
    *,
    supervisor_termination_reason: str = "research_complete",
    unexecuted_research_calls: int = 0,
) -> StateGraph[_ResearchState, None, _ResearchState, _ResearchState]:
    async def research(
        state: _ResearchState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        calls.append(
            {
                "configurable": dict(config.get("configurable", {})),
                "messages": list(state["messages"]),
            }
        )
        return {
            "messages": [AIMessage(content="研究完成")],
            "raw_notes": [],
            "final_report": "# Alpha 与 Beta 竞品分析",
            "supervisor_termination_reason": supervisor_termination_reason,
            "unexecuted_research_calls": unexecuted_research_calls,
        }

    builder = StateGraph(_ResearchState)
    builder.add_node("research", research)
    builder.add_edge(START, "research")
    builder.add_edge("research", END)
    return builder


@pytest.mark.asyncio
async def test_confirmation_is_durable_interrupt_and_research_waits() -> None:
    calls: list[dict[str, Any]] = []
    saver = InMemorySaver()
    graph = build_graph(
        saver,
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    config = _config()

    interrupted = await graph.ainvoke(
        {"run_id": "run-graph-1", "request": _request().model_dump(mode="json")},
        config=config,
    )

    assert calls == []
    assert _scope(interrupted).frozen is False
    assert interrupted["run_id"] == "run-graph-1"
    assert interrupted["status"] == "awaiting_confirmation"
    assert interrupted["__interrupt__"][0].value["type"] == "scope_confirmation"
    assert graph.get_state(config).next == ("confirm_scope",)


@pytest.mark.asyncio
async def test_web_commission_uses_planner_scope_without_default_dimension_injection() -> None:
    calls: list[dict[str, Any]] = []
    planner = _ScopePlanner(
        ScopeProposal(
            intent_summary="分析 Trae 与同类 AI IDE 的产品取舍",
            levels=(AnalysisLevel.PRODUCT_SERVICE,),
            dimensions={
                AnalysisLevel.PRODUCT_SERVICE: (
                    "代码库上下文连续性",
                    "Agent 编程工作流",
                )
            },
        )
    )
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
        scope_planner=planner,
    )
    commission = ResearchCommission(
        query="调研 Trae 的 AI IDE 竞品",
        entities=(ResearchEntity(name="Trae"),),
        focus_points=("代码库上下文连续性",),
    )

    interrupted = await graph.ainvoke(
        {
            "run_id": "run-commission",
            "commission": commission.model_dump(mode="json"),
        },
        config=_config("run-commission"),
    )

    scope = _scope(interrupted)
    assert planner.commissions == [commission]
    assert scope.dimensions == {
        AnalysisLevel.PRODUCT_SERVICE: (
            "代码库上下文连续性",
            "Agent 编程工作流",
        )
    }
    assert scope.dimensions[AnalysisLevel.PRODUCT_SERVICE] != (
        "核心能力",
        "定价与套餐",
        "用户体验",
        "技术与集成",
        "服务与支持",
    )
    assert interrupted["status"] == RunStatus.AWAITING_CONFIRMATION.value
    assert calls == []


@pytest.mark.asyncio
async def test_resume_freezes_scope_and_passes_safe_upstream_config() -> None:
    calls: list[dict[str, Any]] = []
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    config = _config()
    first = await graph.ainvoke(
        {"request": _request().model_dump(mode="json")}, config=config
    )
    revision = _scope(first).revision

    completed = await graph.ainvoke(
        Command(resume={"accepted": True, "expected_revision": revision}),
        config=config,
    )

    assert _scope(completed).frozen is True
    assert _scope(completed).revision == revision + 1
    assert len(calls) == 1
    upstream = calls[0]
    assert upstream["configurable"]["thread_id"] == "run-graph-1"
    assert upstream["configurable"]["allow_clarification"] is False
    assert upstream["configurable"]["search_api"] == "tavily"
    assert upstream["configurable"]["mcp_config"] is None
    prompt = str(upstream["messages"][-1].content)
    assert "面向真实用户的竞品分析报告" in prompt
    assert "Alpha" in prompt and "Beta" in prompt
    assert "product_service" not in prompt
    assert "冻结范围：" not in prompt
    assert _bundle(completed).run_id == "run-graph-1"
    assert completed["status"] == _bundle(completed).status.value


@pytest.mark.asyncio
async def test_fixed_scope_state_update_uses_formal_post_freeze_path() -> None:
    """固定案例从 freeze_scope 后继续，但仍经过正式研究和发布节点。."""
    calls: list[dict[str, Any]] = []
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    config = _config("run-fixed-scope")
    request = _request()
    draft = build_draft_scope(request)
    request = request.model_copy(update={"dimensions": dict(draft.dimensions)})
    frozen = draft.model_copy(
        update={
            "confirmed_at": datetime.now(UTC),
            "revision": 1,
            "frozen": True,
        }
    )

    updated_config = await graph.aupdate_state(
        config,
        {
            "run_id": "run-fixed-scope",
            "request": request.model_dump(mode="json"),
            "scope": frozen.model_dump(mode="json"),
            "status": RunStatus.SCOPE_FROZEN.value,
        },
        as_node="freeze_scope",
    )
    updated_config["configurable"] = {
        **dict(config["configurable"]),
        **dict(updated_config.get("configurable") or {}),
    }
    completed = await graph.ainvoke(None, updated_config)

    assert len(calls) == 1
    prompt = str(calls[0]["messages"][-1].content)
    assert "面向真实用户的竞品分析报告" in prompt
    assert "冻结范围：" not in prompt
    assert _scope(completed).frozen is True
    assert _bundle(completed).run_id == "run-fixed-scope"
    assert "__interrupt__" not in completed


@pytest.mark.asyncio
async def test_discovery_requires_candidate_selection_then_scope_confirmation() -> None:
    calls: list[dict[str, Any]] = []
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=3, cost_usd=5),
        run_id="run-graph-1",
    )
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
        budget_guard=guard,
        discovery_provider=_DiscoveryProvider(),
    )
    request = _request().model_copy(update={"discover_candidates": True})
    config = _config()

    discovered = await graph.ainvoke(
        {"run_id": "run-graph-1", "request": request.model_dump(mode="json")},
        config=config,
    )

    candidate = DiscoveryCandidate.model_validate(discovered["discovery_candidates"][0])
    assert discovered["confirmation_kind"] == "candidate_selection"
    assert discovered["__interrupt__"][0].value["type"] == "candidate_selection"
    assert candidate.source_url == "https://example.com/candidate"
    assert calls == []

    scope_pending = await graph.ainvoke(
        Command(
            resume={
                "expected_revision": _scope(discovered).revision,
                "selected_candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "entity_name": "用户确认后的竞品名",
                    }
                ],
            }
        ),
        config=config,
    )

    assert scope_pending["confirmation_kind"] == "scope_confirmation"
    assert scope_pending["__interrupt__"][0].value["type"] == "scope_confirmation"
    assert [entity.name for entity in _scope(scope_pending).entities] == [
        "Alpha",
        "Beta",
        "用户确认后的竞品名",
    ]
    assert _scope(scope_pending).levels == _scope(discovered).levels
    assert _scope(scope_pending).dimensions == _scope(discovered).dimensions
    assert _scope(scope_pending).exclusions == _scope(discovered).exclusions
    assert _scope(scope_pending).revision == _scope(discovered).revision
    assert calls == []

    completed = await graph.ainvoke(
        Command(
            resume={
                "accepted": True,
                "expected_revision": _scope(scope_pending).revision,
            }
        ),
        config=config,
    )

    assert _scope(completed).frozen is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_user_confirmed_public_url_is_kept_in_the_frozen_research_prompt() -> None:
    calls: list[dict[str, Any]] = []
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    config = _config()
    first = await graph.ainvoke(
        {"run_id": "run-graph-1", "request": _request_with_url("https://example.com")},
        config=config,
    )

    await graph.ainvoke(
        Command(
            resume={
                "accepted": True,
                "expected_revision": _scope(first).revision,
            }
        ),
        config=config,
    )

    assert "https://example.com" in str(calls[0]["messages"][-1].content)


@pytest.mark.asyncio
async def test_private_user_url_is_rejected_before_confirmation_or_research() -> None:
    calls: list[dict[str, Any]] = []
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )

    with pytest.raises(DataValidationError, match="公网安全边界"):
        await graph.ainvoke(
            {
                "run_id": "run-graph-1",
                "request": _request_with_url("http://127.0.0.1/admin"),
            },
            config=_config(),
        )

    assert calls == []


@pytest.mark.asyncio
async def test_rejected_confirmation_cancels_without_research() -> None:
    calls: list[dict[str, Any]] = []
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    config = _config()
    await graph.ainvoke(
        {
            "run_id": "run-graph-1",
            "request": _request().model_dump(mode="json"),
        },
        config=config,
    )

    cancelled = await graph.ainvoke(
        Command(resume={"accepted": False, "expected_revision": 0}),
        config=config,
    )

    assert cancelled["status"] == RunStatus.CANCELLED.value
    assert _scope(cancelled).frozen is False
    assert "bundle" not in cancelled
    assert calls == []


@pytest.mark.asyncio
async def test_resume_requires_the_same_thread_id() -> None:
    calls: list[dict[str, Any]] = []
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    await graph.ainvoke(
        {
            "run_id": "run-original",
            "request": _request().model_dump(mode="json"),
        },
        config=_config("run-original"),
    )

    # 另一个 thread_id 没有原任务的 Checkpoint，不能借 Command 恢复其状态。
    with pytest.raises(DataValidationError, match="ResearchRequest"):
        await graph.ainvoke(
            Command(resume={"accepted": True, "expected_revision": 0}),
            config=_config("run-other"),
        )

    assert calls == []


@pytest.mark.asyncio
async def test_unsafe_upstream_config_is_rejected_before_research() -> None:
    calls: list[dict[str, Any]] = []
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    config = _config()
    await graph.ainvoke(
        {
            "run_id": "run-graph-1",
            "request": _request().model_dump(mode="json"),
        },
        config=config,
    )
    unsafe_config = _config()
    unsafe_config["configurable"]["search_api"] = "openai"

    with pytest.raises(DataValidationError, match="Tavily"):
        await graph.ainvoke(
            Command(resume={"accepted": True, "expected_revision": 0}),
            config=unsafe_config,
        )

    assert calls == []


@pytest.mark.asyncio
async def test_materialize_reads_the_runtime_budget_snapshot() -> None:
    calls: list[dict[str, Any]] = []
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=3, cost_usd=5),
        run_id="run-graph-1",
    )
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
        budget_guard=guard,
    )
    config = _config()
    await graph.ainvoke(
        {
            "run_id": "run-graph-1",
            "request": _request().model_dump(mode="json"),
        },
        config=config,
    )

    completed = await graph.ainvoke(
        Command(resume={"accepted": True, "expected_revision": 0}),
        config=config,
    )

    assert _bundle(completed).budget.limit.tavily_calls == 3
    assert _bundle(completed).budget.remaining.cost_usd == 5


@pytest.mark.asyncio
async def test_materialize_refreshes_budget_after_artifact_extractor() -> None:
    calls: list[dict[str, Any]] = []
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(input_tokens=100, output_tokens=100),
        run_id="run-graph-1",
    )
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls),
        extractor=_BudgetChargingExtractor(guard),
        budget_guard=guard,
    )
    config = _config()
    await graph.ainvoke(
        {
            "run_id": "run-graph-1",
            "request": _request().model_dump(mode="json"),
        },
        config=config,
    )

    completed = await graph.ainvoke(
        Command(resume={"accepted": True, "expected_revision": 0}),
        config=config,
    )

    budget = _bundle(completed).budget
    assert budget.spent.input_tokens == 7
    assert budget.spent.output_tokens == 9
    assert budget.remaining.input_tokens == 93
    assert budget.remaining.output_tokens == 91


@pytest.mark.asyncio
async def test_graph_propagates_unexecuted_supervisor_delegation_to_publication() -> None:
    calls: list[dict[str, Any]] = []
    graph = build_graph(
        InMemorySaver(),
        _fake_research_graph(calls, unexecuted_research_calls=1),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )
    config = _config()
    await graph.ainvoke(
        {
            "run_id": "run-graph-1",
            "request": _request().model_dump(mode="json"),
        },
        config=config,
    )

    completed = await graph.ainvoke(
        Command(resume={"accepted": True, "expected_revision": 0}),
        config=config,
    )

    bundle = _bundle(completed)
    assert bundle.status is RunStatus.PARTIAL
    assert any(
        "1 个 Supervisor 研究委托未执行" in item
        for item in bundle.report.limitations
    )


def test_build_graph_requires_non_empty_thread_id_at_runtime() -> None:
    calls: list[dict[str, Any]] = []
    graph = build_graph(InMemorySaver(), _fake_research_graph(calls))

    with pytest.raises(ValueError, match="thread_id"):
        graph.invoke(
            {
                "run_id": "run-graph-1",
                "request": _request().model_dump(mode="json"),
            },
            config={"configurable": {}},
        )
