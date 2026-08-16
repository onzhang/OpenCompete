"""研究内部契约与用户报告 brief 的分离构造。."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from opencompete.models import FrozenModel, ResearchRequest, ScopeSpec


class ResearchContract(FrozenModel):
    """仅供 Extractor / hard gates /（可选）Supervisor system 使用。."""

    frozen_scope: dict[str, Any]
    coverage_units: tuple[str, ...]
    comparison_units: tuple[str, ...]
    allowed_urls: tuple[str, ...]
    exclusions: tuple[str, ...]
    structured_unknown_requirements: str
    internal_dimension_ids: tuple[str, ...]


class UserReportBrief(FrozenModel):
    """允许进入上游根 messages / 最终报告上下文的用户可读任务说明。."""

    audience: str = Field(min_length=1)
    decision_to_resolve: str = Field(min_length=1)
    natural_language_question: str = Field(min_length=1)
    confirmed_scope: tuple[str, ...] = ()
    geography_and_horizon: str = ""
    user_scenarios: tuple[str, ...] = ()
    output_language: str = "zh-CN"
    readability_structure_requirements: tuple[str, ...] = ()


_INTERNAL_ENUM_MARKERS = (
    "product_service",
    "company_organization",
    "industry_market",
    "capabilities",
    "user_experience",
    "technology_integrations",
    "service_support",
    "ClaimStatus",
    "src_",
    "evd_",
    "clm_",
    "cmp_",
)

_LEVEL_LABELS = {
    "product_service": "产品与服务",
    "company_organization": "公司与组织",
    "industry_market": "产业与市场",
}


def build_research_contract(
    request: ResearchRequest,
    scope: ScopeSpec,
) -> ResearchContract:
    """从冻结 Scope 构造内部研究契约。."""
    coverage = tuple(
        f"{entity.name} | {level.value} | {dimension}"
        for entity in scope.entities
        for level in scope.levels
        for dimension in scope.dimensions[level]
    )
    comparisons = tuple(
        f"{level.value} | {dimension}"
        for level in scope.levels
        for dimension in scope.dimensions[level]
    )
    internal_ids = tuple(
        sorted(
            {
                level.value
                for level in scope.levels
            }
            | {
                dimension
                for level in scope.levels
                for dimension in scope.dimensions[level]
                if dimension.isascii() and "_" in dimension
            }
        )
    )
    return ResearchContract(
        frozen_scope=scope.model_dump(mode="json"),
        coverage_units=coverage,
        comparison_units=comparisons,
        allowed_urls=tuple(request.allowed_urls),
        exclusions=tuple(scope.exclusions),
        structured_unknown_requirements=(
            "结构化产物中证据不足的单元必须保留为 Unknown 状态，"
            "不得伪造支持关系；Unknown 仅进入 Bundle，不得逐格写入用户正文。"
        ),
        internal_dimension_ids=internal_ids,
    )


def build_user_report_brief(
    request: ResearchRequest,
    scope: ScopeSpec,
    *,
    audience: str | None = None,
    decision: str | None = None,
    output_language: str = "zh-CN",
) -> UserReportBrief:
    """构造自然语言用户 brief，禁止夹带内部 schema。."""
    entity_names = "、".join(entity.name for entity in scope.entities) or "比较对象"
    resolved_audience = audience or "需要做选择的普通用户"
    resolved_decision = decision or (
        f"在 {entity_names} 之间做出可执行的选择，并说明适用条件"
    )
    scope_summary = _render_confirmed_scope(scope)
    return UserReportBrief(
        audience=resolved_audience,
        decision_to_resolve=resolved_decision,
        natural_language_question=request.query.strip(),
        confirmed_scope=scope_summary,
        geography_and_horizon="",
        user_scenarios=(),
        output_language=output_language,
        readability_structure_requirements=(
            "结论前置：开篇直接回答什么人适合选择谁",
            "使用目标用户能理解的语言，解释必要术语的用户意义",
            "价格与成本使用同口径表格，单位明确",
            "关键价格、资格与迁移规则提供可点击官方链接",
            "不要输出系统内部字段名、内部 ID 或审计清单",
            "不要把研究限制写成淹没结论的主体",
        ),
    )


def render_user_report_brief_message(
    brief: UserReportBrief,
    *,
    allowed_urls: tuple[str, ...] = (),
) -> str:
    """渲染进入上游 messages 的唯一用户任务正文。."""
    lines = [
        "请完成一份面向真实用户的竞品分析报告。",
        "",
        f"目标读者：{brief.audience}",
        f"要解决的决策：{brief.decision_to_resolve}",
        f"用户问题：{brief.natural_language_question}",
        f"输出语言：{brief.output_language}",
    ]
    if brief.geography_and_horizon.strip():
        lines.append(f"时间与地区：{brief.geography_and_horizon.strip()}")
    if brief.user_scenarios:
        lines.append("用户关心的场景：")
        lines.extend(f"- {item}" for item in brief.user_scenarios)
    if brief.confirmed_scope:
        lines.append("本次已确认的研究范围：")
        lines.extend(f"- {item}" for item in brief.confirmed_scope)
    if allowed_urls:
        lines.append("用户确认可参考的公开网址：")
        lines.extend(f"- {url}" for url in allowed_urls)
    lines.append("报告结构要求：")
    lines.extend(f"- {item}" for item in brief.readability_structure_requirements)
    lines.extend(
        [
            "",
            "写作约束：",
            "- 只写用户能理解并用于决策的内容。",
            "- 不要粘贴或复述任何内部 JSON、枚举字段名或逐格覆盖审计表。",
            "- 证据不足时，用自然语言说明“尚未确认什么、用户应如何核实”，"
            "不要输出裸 Unknown 标记或内部状态枚举。",
        ]
    )
    return "\n".join(lines)


def _render_confirmed_scope(scope: ScopeSpec) -> tuple[str, ...]:
    """把冻结 Scope 转为用户可读约束，不引入动态维度词表或 JSON。."""
    entity_names = "、".join(entity.name for entity in scope.entities)
    lines = [f"研究对象：{entity_names}"]
    for level in scope.levels:
        # 历史固定 slug 仍可能存在于旧 CLI/Benchmark 路径；它们是内部契约，
        # 不应为了兼容而泄漏到上游用户消息。新 Web Planner 输出中文短语。
        visible_dimensions = tuple(
            dimension
            for dimension in scope.dimensions[level]
            if not dimension.isascii()
        )
        if visible_dimensions:
            lines.append(
                f"{_LEVEL_LABELS[level.value]}：{'、'.join(visible_dimensions)}"
            )
    if scope.exclusions:
        lines.append(f"明确不纳入研究：{'、'.join(scope.exclusions)}")
    return tuple(lines)


def build_prepare_research_message_content(
    request: ResearchRequest,
    scope: ScopeSpec,
    *,
    audience: str | None = None,
    decision: str | None = None,
    output_language: str = "zh-CN",
) -> str:
    """Graph ``prepare_research_input`` 写入 messages 的唯一内容路径。."""
    if not scope.frozen:
        raise ValueError("研究开始前必须冻结范围")
    brief = build_user_report_brief(
        request,
        scope,
        audience=audience,
        decision=decision,
        output_language=output_language,
    )
    return render_user_report_brief_message(
        brief,
        allowed_urls=tuple(request.allowed_urls),
    )


def message_contains_internal_contract_leak(text: str) -> list[str]:
    """检测用户消息路径是否泄漏内部契约（测试与门禁共用启发式）。."""
    hits: list[str] = []
    if '"entities"' in text and '"levels"' in text and '"dimensions"' in text:
        hits.append("scope_json_dump")
    if "冻结范围：" in text or "冻结范围覆盖清单" in text:
        hits.append("coverage_audit_boilerplate")
    if "必须逐项完成以下" in text:
        hits.append("cell_by_cell_audit")
    if "显式 Unknown" in text or "写 Unknown" in text:
        hits.append("unknown_contract_language")
    for marker in _INTERNAL_ENUM_MARKERS:
        if marker in text:
            hits.append(f"internal_marker:{marker}")
            break
    # 完整 Scope JSON dump
    try:
        if text.strip().startswith("{") and "frozen" in json.loads(text):
            hits.append("raw_json_scope")
    except json.JSONDecodeError:
        pass
    return hits
