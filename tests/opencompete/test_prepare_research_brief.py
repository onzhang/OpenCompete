"""研究入口 messages 仅含用户 brief，不含内部研究契约。."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from opencompete.analysis import build_draft_scope
from opencompete.graph import build_graph
from opencompete.models import (
    AnalysisLevel,
    ResearchEntity,
    ResearchRequest,
)
from opencompete.research_contracts import (
    build_prepare_research_message_content,
    build_research_contract,
    message_contains_internal_contract_leak,
)


def _frozen_request_and_scope():
    request = ResearchRequest(
        query="比较 Spotify 与 Apple Music 谁更适合美国个人用户两年订阅",
        entities=(
            ResearchEntity(name="Spotify"),
            ResearchEntity(name="Apple Music"),
        ),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={
            AnalysisLevel.PRODUCT_SERVICE: (
                "capabilities",
                "pricing",
                "user_experience",
            ),
        },
        exclusions=("跨地区价格比较",),
        allowed_urls=("https://www.spotify.com/us/premium/",),
        discover_candidates=False,
    )
    draft = build_draft_scope(request)
    frozen = draft.model_copy(
        update={
            "frozen": True,
            "revision": 1,
            "confirmed_at": datetime(2026, 7, 18, tzinfo=timezone.utc),
        }
    )
    return request, frozen


def test_build_prepare_research_message_is_brief_only() -> None:
    request, scope = _frozen_request_and_scope()
    content = build_prepare_research_message_content(request, scope)
    leaks = message_contains_internal_contract_leak(content)
    assert leaks == [], leaks
    assert "目标读者" in content
    assert "capabilities" not in content
    assert "product_service" not in content
    assert "冻结范围：" not in content
    assert "显式 Unknown" not in content
    contract = build_research_contract(request, scope)
    assert contract.coverage_units
    assert any("capabilities" in unit for unit in contract.coverage_units)


def test_dynamic_confirmed_scope_is_passed_to_user_brief_and_contract() -> None:
    request = ResearchRequest(
        query="调研 Trae 与 Cursor 对新入职产品团队的 AI IDE 取舍",
        entities=(ResearchEntity(name="Trae"), ResearchEntity(name="Cursor")),
        levels=(
            AnalysisLevel.PRODUCT_SERVICE,
            AnalysisLevel.COMPANY_ORGANIZATION,
        ),
        dimensions={
            AnalysisLevel.PRODUCT_SERVICE: (
                "代码库上下文连续性",
                "Agent 编程工作流",
                "个人版配额与使用成本",
            ),
            AnalysisLevel.COMPANY_ORGANIZATION: ("面向团队的协作与生态策略",),
        },
        exclusions=("未公开路线图",),
        allowed_urls=("https://www.trae.ai/",),
    )
    scope = build_draft_scope(request).model_copy(
        update={
            "frozen": True,
            "revision": 1,
            "confirmed_at": datetime(2026, 8, 15, tzinfo=timezone.utc),
        }
    )

    content = build_prepare_research_message_content(request, scope)
    contract = build_research_contract(request, scope)

    assert "本次已确认的研究范围" in content
    assert "Trae、Cursor" in content
    assert "产品与服务：代码库上下文连续性、Agent 编程工作流、个人版配额与使用成本" in content
    assert "公司与组织：面向团队的协作与生态策略" in content
    assert "明确不纳入研究：未公开路线图" in content
    assert "https://www.trae.ai/" in content
    assert "product_service" not in content
    assert message_contains_internal_contract_leak(content) == []
    assert contract.exclusions == ("未公开路线图",)


def test_graph_prepare_research_input_real_node() -> None:
    """驱动编译 Graph 上真实的 prepare_research_input 节点。."""
    request, scope = _frozen_request_and_scope()
    graph = build_graph(checkpointer=None)
    node = graph.nodes["prepare_research_input"]
    state = {
        "request": request.model_dump(mode="json"),
        "scope": scope.model_dump(mode="json"),
        "run_id": "run-brief-test",
        "messages": [],
    }
    config = {
        "configurable": {
            "thread_id": "run-brief-test",
            "allow_clarification": False,
            "search_api": "tavily",
            "mcp_config": None,
        }
    }
    out = node.invoke(state, config)
    message_text = out["messages"][0].content
    assert isinstance(message_text, str)
    assert message_contains_internal_contract_leak(message_text) == []
    assert "research_contract" in out
    assert out["research_contract"]["coverage_units"]
    assert "capabilities" in str(out["research_contract"]["coverage_units"])
    assert "capabilities" not in message_text
    source = Path("src/opencompete/graph.py").read_text(encoding="utf-8")
    assert "build_prepare_research_message_content" in source


def test_report_output_budget_scales_with_frozen_scope() -> None:
    from opencompete.research_contracts import estimated_report_output_tokens

    _request, small = _frozen_request_and_scope()
    large_request = ResearchRequest(
        query="比较多款 AI IDE",
        entities=tuple(ResearchEntity(name=f"工具 {index}") for index in range(8)),
        levels=(
            AnalysisLevel.PRODUCT_SERVICE,
            AnalysisLevel.COMPANY_ORGANIZATION,
        ),
        dimensions={
            AnalysisLevel.PRODUCT_SERVICE: ("a", "b", "c", "d", "e"),
            AnalysisLevel.COMPANY_ORGANIZATION: ("x", "y", "z", "w"),
        },
        discover_candidates=False,
    )
    large = build_draft_scope(large_request).model_copy(
        update={
            "frozen": True,
            "revision": 1,
            "confirmed_at": datetime(2026, 7, 18, tzinfo=timezone.utc),
        }
    )

    def expected(scope):
        groups = sum(len(scope.dimensions[level]) for level in scope.levels)
        return 1_500 + len(scope.entities) * groups * 220 + groups * 300

    assert estimated_report_output_tokens(small) == expected(small)
    assert estimated_report_output_tokens(large) == expected(large)
    assert estimated_report_output_tokens(large) > estimated_report_output_tokens(small) * 5
