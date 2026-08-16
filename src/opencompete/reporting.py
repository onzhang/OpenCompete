"""最终报告生成失败时，从已验证结构化关系渲染可读保底报告。."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from urllib.parse import quote

from opencompete.models import (
    Claim,
    ClaimStatus,
    Comparison,
    Evidence,
    ScopeSpec,
    Source,
)

_LEVEL_LABELS = {
    "product_service": "产品与服务",
    "company_organization": "公司与组织",
    "industry_market": "产业与市场",
}
_DIMENSION_LABELS = {
    "capabilities": "核心能力",
    "pricing": "定价",
    "user_experience": "用户体验",
    "technology_integrations": "技术集成",
    "service_support": "服务与支持",
    "positioning_strategy": "定位与战略",
    "business_model": "商业模式",
    "go_to_market": "市场进入策略",
    "operations_capabilities": "运营能力",
    "partnerships_ecosystem": "合作与生态",
    "market_size_growth": "市场规模与增长",
    "customer_segments_demand": "客户细分与需求",
    "competitive_landscape": "竞争格局",
    "regulation_standards": "监管与标准",
    "trends_risks": "趋势与风险",
}
_CLAIM_STATUS_LABELS = {
    ClaimStatus.SUPPORTED: "已关联证据，未经独立 FACT 核验",
    ClaimStatus.CONFLICTED: "证据存在冲突，未经独立 FACT 核验",
    ClaimStatus.UNKNOWN: "待核实命题",
}
_MARKDOWN_PUNCTUATION = re.compile(r"([\\`*_[\]{}()#+\-.!|])")


def render_validated_report(
    *,
    scope: ScopeSpec,
    claims: tuple[Claim, ...],
    comparisons: tuple[Comparison, ...],
    sources: tuple[Source, ...],
    evidence: tuple[Evidence, ...],
    limitations: tuple[str, ...],
) -> str:
    """只用已校验结构化关系渲染不暴露内部标识的保底报告。."""
    entity_names = {
        entity.entity_id: _markdown_text(entity.name) for entity in scope.entities
    }
    claims_by_id = {claim.claim_id: claim for claim in claims}
    evidence_by_id = {item.evidence_id: item for item in evidence}
    sources_by_id = {source.source_id: source for source in sources}
    title = " 与 ".join(entity_names.values()) or "OpenCompete"
    lines = [
        f"# {title} 竞品分析",
        "",
        "## 结论摘要",
        "",
        f"- 建议：基于当前已校验结构化证据，在 {title} 之间按下文条件选择；"
        "证据不足处请按「待核实」说明自行核验。",
        "",
        "## 研究范围",
        "",
    ]
    lines.append(
        f"- 对象：{'、'.join(entity_names.values()) or '（对象未命名）'}"
    )
    lines.append(
        "- 层级："
        + "、".join(
            _LEVEL_LABELS.get(level.value, level.value) for level in scope.levels
        )
    )
    lines.extend(["", "## 结构化比较", ""])

    referenced_claims: set[str] = set()
    for comparison in comparisons:
        level = _LEVEL_LABELS.get(comparison.level.value, comparison.level.value)
        dimension_label = _DIMENSION_LABELS.get(
            comparison.dimension,
            comparison.dimension,
        )
        lines.append(f"### {level} / {_markdown_text(dimension_label)}")
        lines.append("")
        lines.append(
            "比较对象："
            + "、".join(
                entity_names.get(entity_id, entity_id)
                for entity_id in comparison.entity_ids
            )
        )
        lines.append("")
        for claim_id in comparison.claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                continue
            referenced_claims.add(claim_id)
            lines.extend(
                _render_claim(
                    claim,
                    entity_names=entity_names,
                    evidence_by_id=evidence_by_id,
                    sources_by_id=sources_by_id,
                )
            )
        if not comparison.claim_ids:
            lines.append("- 待核实：当前维度没有可发布的结构化结论。")
        lines.append("")

    remaining = [claim for claim in claims if claim.claim_id not in referenced_claims]
    if remaining:
        lines.extend(["## 其他结构化结论", ""])
        for claim in remaining:
            lines.extend(
                _render_claim(
                    claim,
                    entity_names=entity_names,
                    evidence_by_id=evidence_by_id,
                    sources_by_id=sources_by_id,
                )
            )
        lines.append("")

    lines.extend(["## 已知限制", ""])
    if limitations:
        lines.extend(f"- {_markdown_text(item)}" for item in limitations)
    else:
        lines.append("- 无额外限制。")

    lines.extend(["", "## 来源", ""])
    if sources:
        for source in sources:
            lines.append(
                f"- {_markdown_text(source.title)} — "
                f"<{_markdown_url(source.url)}>"
            )
    else:
        lines.append("- 待核实：没有捕获到可定位来源。")
    return "\n".join(lines).strip()


def _render_claim(
    claim: Claim,
    *,
    entity_names: Mapping[str, str],
    evidence_by_id: Mapping[str, Evidence],
    sources_by_id: Mapping[str, Source],
) -> list[str]:
    entity = entity_names.get(claim.entity_id, _markdown_text(claim.entity_id))
    status = _CLAIM_STATUS_LABELS[claim.status]
    text = claim.text
    # 内部维度枚举（capabilities 等）不得泄漏进用户正文；渲染时统一换成人读标签。
    dimension_label = _DIMENSION_LABELS.get(claim.dimension)
    if dimension_label is not None and claim.dimension in text:
        text = text.replace(claim.dimension, dimension_label)
    lines = [f"- **{entity}**（{status}）：{_markdown_text(text)}"]
    if claim.evidence_ids:
        citations: list[str] = []
        seen_urls: set[str] = set()
        for evidence_id in claim.evidence_ids:
            item = evidence_by_id.get(evidence_id)
            source = sources_by_id.get(item.source_id) if item is not None else None
            if source is None or source.url in seen_urls:
                continue
            seen_urls.add(source.url)
            citations.append(
                f"{_markdown_text(source.title)}：<{_markdown_url(source.url)}>"
            )
        if citations:
            lines.append(f"  - 来源：{'；'.join(citations)}")
    elif claim.uncertainty:
        # Unknown 空缺是研究结论：用与 claim 绑定的可读说明，而不是全篇复用
        # 同一句模板，避免发布门禁把大量重复句判为 excessive_repetition。
        lines.append(f"  - 说明：{_markdown_text(claim.uncertainty)}")
    return lines


def _single_line(value: str) -> str:
    return " ".join(str(value).split())


def _markdown_text(value: str) -> str:
    escaped_html = html.escape(_single_line(value), quote=False)
    return _MARKDOWN_PUNCTUATION.sub(r"\\\1", escaped_html)


def _markdown_url(value: str) -> str:
    return quote(
        value,
        safe=":/?#[]@!$&'()*+,;=%-._~",
    )
