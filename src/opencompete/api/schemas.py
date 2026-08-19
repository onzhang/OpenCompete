"""HTTP 请求体模型与响应构造函数。.

输入侧用 Pydantic 模型做边界校验，再转换为领域模型；
输出侧统一用显式字段构造 ``dict``，作为防泄漏边界——
``config_snapshot``、完整异常文本等敏感内容不在任何构造函数的输出里。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from opencompete.models import (
    AnalysisLevel,
    CandidateChoice,
    CandidateSelection,
    DiscoveryCandidate,
    ErrorRecord,
    ResearchCommission,
    ResearchEntity,
    RunManifest,
    ScopeConfirmation,
    ScopeSpec,
)


class ApiModel(BaseModel):
    """API 请求体基类：拒绝未知字段，避免前端拼写错误被静默吞掉。."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SettingsUpdateBody(ApiModel):
    """页面保存的非秘密运行设置与乐观锁版本。."""

    expected_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    values: dict[str, Any] = Field(min_length=1)


class CredentialUpdateBody(ApiModel):
    """单个本地受管凭据的设置或清除操作。."""

    expected_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    name: str = Field(min_length=1)
    operation: Literal["set", "clear"]
    value: str | None = None


class EntityBody(ApiModel):
    """范围确认请求中的单个研究对象。."""

    name: str = Field(min_length=1)
    entity_id: str = ""
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    user_provided: bool = True

    def to_domain(self) -> ResearchEntity:
        """转换为领域研究对象模型。."""
        return ResearchEntity(
            name=self.name,
            entity_id=self.entity_id,
            aliases=tuple(self.aliases),
            description=self.description,
            user_provided=self.user_provided,
        )


class CreateRunBody(ApiModel):
    """Web 原始研究委托；层级与维度由后端 Planner 动态生成。."""

    query: str = Field(min_length=1)
    entities: list[str] = Field(min_length=1)
    focus_points: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    allowed_urls: list[str] = Field(default_factory=list)
    discover_candidates: bool = False

    def to_domain(self) -> ResearchCommission:
        """转换为不含预设范围的领域研究委托。."""
        return ResearchCommission(
            query=self.query,
            entities=tuple(ResearchEntity(name=name) for name in self.entities),
            focus_points=tuple(self.focus_points),
            exclusions=tuple(self.exclusions),
            allowed_urls=tuple(self.allowed_urls),
            discover_candidates=self.discover_candidates,
        )


class CandidateChoiceBody(ApiModel):
    """用户确认纳入范围的单个候选。."""

    candidate_id: str = Field(min_length=1)
    entity_name: str = Field(min_length=1)


class CandidateSelectionBody(ApiModel):
    """候选选择确认点的请求体。."""

    expected_revision: int = Field(ge=0)
    selected_candidates: list[CandidateChoiceBody] = Field(default_factory=list)

    def to_domain(self) -> CandidateSelection:
        """转换为领域候选选择决定。."""
        return CandidateSelection(
            expected_revision=self.expected_revision,
            selected_candidates=tuple(
                CandidateChoice(
                    candidate_id=choice.candidate_id,
                    entity_name=choice.entity_name,
                )
                for choice in self.selected_candidates
            ),
        )


class ScopeConfirmationBody(ApiModel):
    """范围确认确认点的请求体。."""

    accepted: bool
    expected_revision: int = Field(ge=0)
    entities: list[EntityBody] | None = None
    levels: list[AnalysisLevel] | None = None
    dimensions: dict[AnalysisLevel, list[str]] | None = None
    exclusions: list[str] | None = None

    def to_domain(self) -> ScopeConfirmation:
        """转换为领域范围确认决定。."""
        return ScopeConfirmation(
            accepted=self.accepted,
            expected_revision=self.expected_revision,
            entities=(
                None
                if self.entities is None
                else tuple(entity.to_domain() for entity in self.entities)
            ),
            levels=None if self.levels is None else tuple(self.levels),
            dimensions=(
                None
                if self.dimensions is None
                else {
                    level: tuple(dims) for level, dims in self.dimensions.items()
                }
            ),
            exclusions=(
                None if self.exclusions is None else tuple(self.exclusions)
            ),
        )


def error_view(record: ErrorRecord) -> dict[str, Any]:
    """错误记录的白名单视图：只暴露四个安全字段。."""
    return {
        "code": record.code,
        "category": record.category.value,
        "message": record.message,
        "retryable": record.retryable,
    }


def scope_view(scope: ScopeSpec) -> dict[str, Any]:
    """研究范围的完整 JSON 视图（范围本身不含敏感信息）。."""
    return scope.model_dump(mode="json")


def candidate_view(candidate: DiscoveryCandidate) -> dict[str, Any]:
    """发现候选的 JSON 视图。."""
    return candidate.model_dump(mode="json")


def run_list_item(
    manifest: RunManifest,
    meta: dict[str, Any],
    *,
    active: bool,
) -> dict[str, Any]:
    """列表页的单条运行摘要（显式字段，绝不 dump 整个 manifest）。."""
    status = manifest.status.value
    last_error = manifest.errors[-1] if manifest.errors else None
    return {
        "run_id": manifest.run_id,
        "status": status,
        "scope_revision": manifest.scope_revision,
        "artifact_revision": manifest.artifact_revision,
        "updated_at": manifest.updated_at.isoformat(),
        "query": meta.get("query"),
        "entities": list(meta.get("entities") or []),
        "confirmation_kind": (
            meta.get("confirmation_kind")
            if status == "awaiting_confirmation"
            else None
        ),
        "budget_spent_usd": round(manifest.budget_summary.spent.cost_usd, 2),
        "budget_limit_usd": manifest.budget_summary.limit.cost_usd,
        "partial_reason": (
            manifest.partial_reason
            if status == "partial"
            else None
        ) or (
            "研究部分完成，已发布可用成果"
            if status == "partial"
            else None
        ),
        "error": (
            error_view(last_error) if status == "failed" and last_error else None
        ),
        # 评测字段属于评估流水线，本版本不接入。
        "race_overall": None,
        "fact_citation_accuracy": None,
        "active": active,
    }


def run_detail(
    manifest: RunManifest,
    meta: dict[str, Any],
    *,
    active: bool,
    pending_scope: ScopeSpec | None = None,
    confirmation_kind: str | None = None,
    candidates: tuple[DiscoveryCandidate, ...] = (),
    recoverable: bool = False,
) -> dict[str, Any]:
    """详情页视图：列表字段 + 预算快照 + 待确认载荷。."""
    view = run_list_item(manifest, meta, active=active)
    view.update(
        {
            "budget": manifest.budget_summary.model_dump(mode="json"),
            "pending_confirmation": pending_scope is not None,
            "confirmation_kind": confirmation_kind or view["confirmation_kind"],
            "pending_scope": (
                None if pending_scope is None else scope_view(pending_scope)
            ),
            "candidates": [candidate_view(item) for item in candidates],
            "expected_revision": (
                pending_scope.revision if pending_scope is not None else None
            ),
            # 只能由 Checkpoint inspection 标记可恢复；不能由 Manifest 猜测。
            "recoverable": recoverable,
            "stale": recoverable and not active,
        }
    )
    return view


_SOURCE_LINE = re.compile(
    r"^\s*[-*]\s*\[(\d+)\]\s.*?(https?://\S+?)\s*$"
)


def _normalize_url(url: str) -> str:
    """宽松比较用的 URL 归一：统一 https、小写主机、去尾斜杠。."""
    normalized = url.strip().strip("<>").rstrip("/")
    if normalized.startswith("http://"):
        normalized = "https://" + normalized[len("http://") :]
    scheme, _, rest = normalized.partition("://")
    host, _, path = rest.partition("/")
    return f"{scheme}://{host.lower()}/{path}" if path else f"{scheme}://{host.lower()}"


def _refs_from_report(markdown: str) -> dict[str, int]:
    """从报告文末 Sources 章节解析「URL → 正文引用编号」映射。.

    正文内联引用 ``[N]`` 的权威定义是报告自带的
    ``- [N] 标题: URL`` 列表；``report.source_ids`` 的顺序
    并不保证与正文编号一致，不能作为编号依据。
    """
    refs: dict[str, int] = {}
    for line in markdown.splitlines():
        match = _SOURCE_LINE.match(line)
        if match:
            refs.setdefault(_normalize_url(match.group(2)), int(match.group(1)))
    return refs


def bundle_view(bundle_dump: dict[str, Any]) -> dict[str, Any]:
    """给 Bundle 的来源注入报告引用编号 ``ref`` 并按引用顺序排序。.

    优先按报告文末 Sources 章节的编号经 URL 匹配赋值；报告没有
    可解析的来源列表时退回 ``report.source_ids`` 顺序。未被正文
    引用的来源 ``ref`` 为 ``null``，排在列表尾部。
    """
    report = bundle_dump.get("report", {})
    sources = bundle_dump.get("sources", [])
    refs_by_url = _refs_from_report(report.get("markdown", ""))
    if refs_by_url:
        for source in sources:
            source["ref"] = refs_by_url.get(_normalize_url(source["url"]))
    else:
        source_order = {
            source_id: index + 1
            for index, source_id in enumerate(report.get("source_ids", []))
        }
        for source in sources:
            source["ref"] = source_order.get(source["source_id"])
    bundle_dump["sources"] = sorted(
        sources,
        key=lambda item: (item["ref"] is None, item["ref"] or 0),
    )
    return bundle_dump
