"""动态 Web ScopePlanner 的离线契约测试。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from opencompete.models import (
    AnalysisLevel,
    ResearchCommission,
    ResearchEntity,
    ScopeProposal,
)
from opencompete.scope_planning import LlmScopePlanner, _ScopePlannerResponse
from opencompete.settings import OpenCompeteSettings


class _StructuredModel:
    """不联网的结构化模型替身，记录 Planner 传入的内容与配置。"""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[list[Any], object | None]] = []

    async def ainvoke(self, messages: list[Any], *, config: object | None = None) -> Any:
        self.calls.append((messages, config))
        return self.response


def _commission() -> ResearchCommission:
    return ResearchCommission(
        query="调研 Trae 面向初入团队产品经理的 AI IDE 竞品与差异",
        entities=(ResearchEntity(name="Trae"),),
        focus_points=("Agent 编程工作流", "代码库上下文连续性", "个人版配额"),
        exclusions=("未公开路线图",),
    )


@pytest.mark.asyncio
async def test_planner_returns_exact_task_specific_chinese_scope() -> None:
    model = _StructuredModel(
        {
            "intent_summary": "比较 Trae 与同类 AI IDE 的产品能力和适用场景",
            "levels": ["product_service", "company_organization"],
            "dimensions": [
                {
                    "level": "product_service",
                    "dimensions": [
                    "Agent 编程工作流",
                    "代码库上下文连续性",
                    "个人版配额与使用成本",
                    ],
                },
                {
                    "level": "company_organization",
                    "dimensions": ["目标开发者定位", "产品生态与集成策略"],
                },
            ],
        }
    )
    planner = LlmScopePlanner(OpenCompeteSettings(), model=model)

    proposal = await planner.plan(_commission(), config={"callbacks": [object()]})

    assert proposal.levels == (
        AnalysisLevel.PRODUCT_SERVICE,
        AnalysisLevel.COMPANY_ORGANIZATION,
    )
    assert proposal.dimensions[AnalysisLevel.PRODUCT_SERVICE] == (
        "Agent 编程工作流",
        "代码库上下文连续性",
        "个人版配额与使用成本",
    )
    request = proposal.to_research_request(_commission())
    assert request.dimensions == proposal.dimensions
    assert request.exclusions == ("未公开路线图",)
    assert len(model.calls) == 1
    message_text = str(model.calls[0][0][-1].content)
    assert "<untrusted_user_commission>" in message_text
    assert "Agent 编程工作流" in message_text


def test_planner_transport_schema_uses_array_not_dynamic_object_keys() -> None:
    """Provider Schema 不包含 OpenAI-compatible 端点拒绝的 propertyNames。."""
    schema = _ScopePlannerResponse.model_json_schema()

    def has_property_names(value: object) -> bool:
        """递归检查 JSON Schema 的结构键而非说明文本。."""
        if isinstance(value, dict):
            return "propertyNames" in value or any(
                has_property_names(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(has_property_names(item) for item in value)
        return False

    assert not has_property_names(schema)
    assert schema["properties"]["dimensions"]["type"] == "array"


def test_planner_output_never_accepts_english_slug_as_dynamic_dimension() -> None:
    with pytest.raises(ValidationError, match="中文"):
        ScopeProposal(
            intent_summary="比较两个音乐服务的订阅差异",
            levels=(AnalysisLevel.PRODUCT_SERVICE,),
            dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing",)},
        )


def test_two_commissions_can_have_entirely_different_dynamic_dimensions() -> None:
    trae = ScopeProposal(
        intent_summary="分析 AI IDE 的产品取舍",
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={
            AnalysisLevel.PRODUCT_SERVICE: ("代码库上下文连续性", "智能体任务完成度")
        },
    )
    coffee = ScopeProposal(
        intent_summary="分析连锁咖啡品牌的选址竞争",
        levels=(AnalysisLevel.INDUSTRY_MARKET,),
        dimensions={
            AnalysisLevel.INDUSTRY_MARKET: ("商圈覆盖密度", "门店模型与单店盈利")
        },
    )

    assert trae.dimensions != coffee.dimensions
