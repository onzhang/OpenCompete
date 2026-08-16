"""OpenCompete 的领域数据契约。."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


def utc_now() -> datetime:
    """返回带时区的 UTC 时间。."""
    return datetime.now(UTC)


def normalize_text(value: str) -> str:
    """规范化用于稳定标识的文本。."""
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def make_stable_id(prefix: str, *parts: str) -> str:
    """根据规范化内容生成稳定且不暴露原文的标识。."""
    payload = "\x1f".join(normalize_text(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


class _ImmutableMapping(Mapping[_Key, _Value], Generic[_Key, _Value]):
    """保留字典读取接口但不暴露任何原地修改方法。."""

    def __init__(self, values: Mapping[_Key, _Value]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: _Key) -> _Value:
        return self._values[key]

    def __iter__(self) -> Iterator[_Key]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return repr(self._values)


class FrozenModel(BaseModel):
    """禁止额外字段和原地赋值的领域模型基类。."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class LenientModel(BaseModel):
    """模型结构化输出边界的传输模型基类：忽略未知字段。.

    业务契约一律使用 ``FrozenModel``（extra=forbid）；模型输出直接
    落地的候选/绑定层面对 Schema 之外的噪声字段应丢弃而非整单拒绝，
    再由确定性规范化边界转换为严格领域模型。
    """

    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
    )


class AnalysisLevel(str, Enum):
    """竞品分析支持的三个通用层级。."""

    PRODUCT_SERVICE = "product_service"
    COMPANY_ORGANIZATION = "company_organization"
    INDUSTRY_MARKET = "industry_market"


class RunStatus(str, Enum):
    """一次研究运行的生命周期状态。."""

    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    SCOPE_FROZEN = "scope_frozen"
    RESEARCHING = "researching"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConfirmationKind(str, Enum):
    """当前 durable interrupt 需要的用户决定类型。."""

    CANDIDATE_SELECTION = "candidate_selection"
    SCOPE_CONFIRMATION = "scope_confirmation"


class ClaimStatus(str, Enum):
    """结论的证据状态。."""

    SUPPORTED = "supported"
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"


class SourceType(str, Enum):
    """来源的受控类型。."""

    WEB = "web"
    USER_URL = "user_url"


class GateSeverity(str, Enum):
    """确定性门禁问题的严重程度。."""

    ERROR = "error"
    WARNING = "warning"


class ErrorCategory(str, Enum):
    """可持久化的脱敏错误类别。."""

    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    BUDGET = "budget"
    PROVIDER = "provider"
    VALIDATION = "validation"
    PERSISTENCE = "persistence"
    INTERNAL = "internal"


class ResearchEntity(FrozenModel):
    """用户输入或发现后待确认的研究对象。."""

    name: str = Field(min_length=1)
    entity_id: str = ""
    aliases: tuple[str, ...] = ()
    description: str | None = None
    user_provided: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """拒绝只包含空白的实体名称。."""
        if not value.strip():
            raise ValueError("研究对象名称不能为空")
        return value

    @model_validator(mode="after")
    def populate_entity_id(self) -> ResearchEntity:
        """在调用方未指定时生成稳定实体标识。."""
        if not self.entity_id:
            object.__setattr__(self, "entity_id", make_stable_id("ent", self.name))
        return self


class DiscoveryCandidate(FrozenModel):
    """带可核验发现来源、尚未进入研究范围的候选对象。."""

    entity: ResearchEntity
    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    summary: str | None = None
    category: str | None = None
    source_excerpt: str | None = None
    candidate_id: str = ""

    @model_validator(mode="after")
    def populate_candidate_id(self) -> DiscoveryCandidate:
        """用对象与来源共同生成稳定候选标识。."""
        if not self.candidate_id:
            object.__setattr__(
                self,
                "candidate_id",
                make_stable_id(
                    "cand",
                    f"{self.entity.entity_id}:{self.source_url}",
                ),
            )
        return self


class ResearchRequest(FrozenModel):
    """创建竞品研究任务所需的非秘密输入。."""

    query: str = Field(min_length=1)
    entities: tuple[ResearchEntity, ...] = Field(min_length=1)
    levels: tuple[AnalysisLevel, ...] = (
        AnalysisLevel.PRODUCT_SERVICE,
        AnalysisLevel.COMPANY_ORGANIZATION,
        AnalysisLevel.INDUSTRY_MARKET,
    )
    dimensions: Mapping[AnalysisLevel, tuple[str, ...]] = Field(default_factory=dict)
    exclusions: tuple[str, ...] = ()
    allowed_urls: tuple[str, ...] = ()
    discover_candidates: bool = False

    @field_validator("levels")
    @classmethod
    def validate_levels(
        cls, value: tuple[AnalysisLevel, ...]
    ) -> tuple[AnalysisLevel, ...]:
        """层级必须非空且不能重复。."""
        if not value:
            raise ValueError("至少选择一个分析层级")
        if len(set(value)) != len(value):
            raise ValueError("分析层级不能重复")
        return value

    @field_validator("dimensions")
    @classmethod
    def freeze_dimensions(
        cls, value: Mapping[AnalysisLevel, tuple[str, ...]]
    ) -> Mapping[AnalysisLevel, tuple[str, ...]]:
        """避免请求中的维度映射被旁路修改。."""
        return _ImmutableMapping(value)

    @field_serializer("dimensions")
    def serialize_dimensions(
        self, value: Mapping[AnalysisLevel, tuple[str, ...]]
    ) -> dict[AnalysisLevel, tuple[str, ...]]:
        """把只读映射序列化为普通 JSON 对象。."""
        return dict(value)


class ResearchCommission(FrozenModel):
    """Web 用户提交的原始研究委托，不预设层级或分析维度。."""

    query: str = Field(min_length=1)
    entities: tuple[ResearchEntity, ...] = Field(min_length=1)
    focus_points: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    allowed_urls: tuple[str, ...] = ()
    discover_candidates: bool = False


class ScopeProposal(FrozenModel):
    """ScopePlanner 为单次委托生成的动态中文范围草案。."""

    intent_summary: str = Field(min_length=1)
    levels: tuple[AnalysisLevel, ...] = Field(min_length=1)
    dimensions: Mapping[AnalysisLevel, tuple[str, ...]]

    @field_validator("dimensions")
    @classmethod
    def freeze_dimensions(
        cls, value: Mapping[AnalysisLevel, tuple[str, ...]]
    ) -> Mapping[AnalysisLevel, tuple[str, ...]]:
        """规范化本次 Planner 输出，但不套用任何固定维度目录。."""
        normalized = {
            level: tuple(
                dict.fromkeys(item.strip() for item in dimensions if item.strip())
            )
            for level, dimensions in value.items()
        }
        return _ImmutableMapping(normalized)

    @field_serializer("dimensions")
    def serialize_dimensions(
        self, value: Mapping[AnalysisLevel, tuple[str, ...]]
    ) -> dict[AnalysisLevel, tuple[str, ...]]:
        """把只读映射序列化为普通 JSON 对象。."""
        return dict(value)

    @model_validator(mode="after")
    def validate_dynamic_scope(self) -> ScopeProposal:
        """只校验形态与中文可读性，绝不根据词表重写规划结果。."""
        if len(set(self.levels)) != len(self.levels):
            raise ValueError("分析层级不能重复")
        if set(self.dimensions) != set(self.levels):
            raise ValueError("每个且仅有已选层级必须配置分析维度")
        for level in self.levels:
            dimensions = self.dimensions[level]
            if not dimensions:
                raise ValueError("每个分析层级至少需要一个维度")
            if any(not _contains_han_char(item) for item in dimensions):
                raise ValueError("动态分析维度必须使用包含中文的自然语言短语")
        if not _contains_han_char(self.intent_summary):
            raise ValueError("研究意图摘要必须使用中文自然语言")
        return self

    def to_research_request(self, commission: ResearchCommission) -> ResearchRequest:
        """把已规划的草案转成现有 Graph 使用的完整 ResearchRequest。."""
        return ResearchRequest(
            query=commission.query,
            entities=commission.entities,
            levels=self.levels,
            dimensions=self.dimensions,
            exclusions=commission.exclusions,
            allowed_urls=commission.allowed_urls,
            discover_candidates=commission.discover_candidates,
        )

    def to_draft_scope(self, commission: ResearchCommission) -> ScopeSpec:
        """把本次 Planner 输出直接变为 Web 可编辑草案，绝不套用 legacy 维度。."""
        return ScopeSpec(
            entities=commission.entities,
            levels=self.levels,
            dimensions=self.dimensions,
            exclusions=commission.exclusions,
            revision=0,
            frozen=False,
        )


def _contains_han_char(value: str) -> bool:
    """判断字符串是否包含至少一个汉字；API、Agent 等可与中文混用。."""
    return any("\u4e00" <= char <= "\u9fff" for char in value)


class DiscoveryRequest(FrozenModel):
    """发现候选对象的只读请求。."""

    query: str = Field(min_length=1)
    levels: tuple[AnalysisLevel, ...] = ()
    existing_entities: tuple[ResearchEntity, ...] = ()
    # Tavily 单次请求页大小；它不是候选产品数，也不能控制检索前沿长度。
    search_results_per_query: int = Field(default=5, ge=1, le=20)
    allowed_urls: tuple[str, ...] = ()


class ScopeConfirmation(FrozenModel):
    """用户对建议范围的显式确认或调整。."""

    accepted: bool
    expected_revision: int = Field(ge=0)
    entities: tuple[ResearchEntity, ...] | None = None
    levels: tuple[AnalysisLevel, ...] | None = None
    dimensions: Mapping[AnalysisLevel, tuple[str, ...]] | None = None
    exclusions: tuple[str, ...] | None = None
    confirmed_at: datetime | None = None

    @field_validator("dimensions")
    @classmethod
    def freeze_dimensions(
        cls, value: Mapping[AnalysisLevel, tuple[str, ...]] | None
    ) -> Mapping[AnalysisLevel, tuple[str, ...]] | None:
        """避免确认载荷被恢复流程原地修改。."""
        return None if value is None else _ImmutableMapping(value)

    @field_serializer("dimensions")
    def serialize_dimensions(
        self, value: Mapping[AnalysisLevel, tuple[str, ...]] | None
    ) -> dict[AnalysisLevel, tuple[str, ...]] | None:
        """把可选只读维度映射序列化为普通 JSON 对象。."""
        return None if value is None else dict(value)


class CandidateChoice(FrozenModel):
    """用户确认加入范围的候选及其最终对象名称。."""

    candidate_id: str = Field(min_length=1)
    entity_name: str = Field(min_length=1)


class CandidateSelection(FrozenModel):
    """用户对发现候选的显式添加/拒绝决定。."""

    expected_revision: int = Field(ge=0)
    selected_candidates: tuple[CandidateChoice, ...] = ()

    @field_validator("selected_candidates")
    @classmethod
    def reject_duplicate_ids(
        cls, value: tuple[CandidateChoice, ...]
    ) -> tuple[CandidateChoice, ...]:
        """同一候选不能被重复添加。."""
        ids = [choice.candidate_id for choice in value]
        if len(set(ids)) != len(ids):
            raise ValueError("候选对象不能重复选择")
        return value


class ScopeSpec(FrozenModel):
    """经确认后可冻结的研究对象、层级和维度范围。."""

    entities: tuple[ResearchEntity, ...] = Field(min_length=1)
    levels: tuple[AnalysisLevel, ...] = Field(min_length=1)
    dimensions: Mapping[AnalysisLevel, tuple[str, ...]]
    exclusions: tuple[str, ...] = ()
    confirmed_at: datetime | None = None
    revision: int = Field(default=0, ge=0)
    frozen: bool = False

    @field_validator("dimensions")
    @classmethod
    def freeze_dimensions(
        cls, value: Mapping[AnalysisLevel, tuple[str, ...]]
    ) -> Mapping[AnalysisLevel, tuple[str, ...]]:
        """冻结嵌套映射，阻止绕过模型赋值保护。."""
        normalized = {
            level: tuple(dict.fromkeys(item.strip() for item in dimensions if item.strip()))
            for level, dimensions in value.items()
        }
        return _ImmutableMapping(normalized)

    @field_serializer("dimensions")
    def serialize_dimensions(
        self, value: Mapping[AnalysisLevel, tuple[str, ...]]
    ) -> dict[AnalysisLevel, tuple[str, ...]]:
        """把冻结维度映射序列化为普通 JSON 对象。."""
        return dict(value)

    @model_validator(mode="after")
    def validate_scope(self) -> ScopeSpec:
        """确保范围层级与维度完全对应。."""
        if len(set(self.levels)) != len(self.levels):
            raise ValueError("分析层级不能重复")
        if len({entity.entity_id for entity in self.entities}) != len(self.entities):
            raise ValueError("研究对象标识不能重复")
        if set(self.dimensions) != set(self.levels):
            raise ValueError("每个且仅有已选层级必须配置分析维度")
        if any(not self.dimensions[level] for level in self.levels):
            raise ValueError("每个分析层级至少需要一个维度")
        if self.frozen and self.confirmed_at is None:
            raise ValueError("冻结范围必须记录确认时间")
        return self


class Source(FrozenModel):
    """可定位的网页来源。."""

    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    accessed_at: datetime
    source_id: str = ""
    publisher: str | None = None
    published_at: datetime | None = None
    source_type: SourceType = SourceType.WEB

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """来源必须是规范化的公网 HTTP/HTTPS URL。."""
        from opencompete.security import validate_public_http_url

        return validate_public_http_url(value)

    @model_validator(mode="after")
    def populate_source_id(self) -> Source:
        """根据 URL 生成稳定来源标识。."""
        if not self.source_id:
            object.__setattr__(self, "source_id", make_stable_id("src", self.url))
        return self


class Evidence(FrozenModel):
    """从单一来源捕获的原文或结构化值。."""

    source_id: str = Field(min_length=1)
    evidence_id: str = ""
    excerpt: str | None = None
    value: str | int | float | bool | None = None
    location: str | None = None
    observed_at: datetime | None = None
    quality_flags: tuple[str, ...] = ()
    query: str | None = Field(default=None, max_length=1000)
    request_id: str | None = Field(default=None, max_length=128)
    provider_usage: Mapping[str, int | float] = Field(default_factory=dict)

    @field_validator("provider_usage")
    @classmethod
    def freeze_provider_usage(
        cls,
        value: Mapping[str, int | float],
    ) -> Mapping[str, int | float]:
        """只保存 Extract 返回的非负数值用量，不接受任意响应正文。."""
        normalized: dict[str, int | float] = {}
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or isinstance(item, bool)
                or not isinstance(item, int | float)
                or item < 0
            ):
                raise ValueError("Evidence Provider usage 只能包含非负数值")
            normalized[key.strip()] = item
        return _ImmutableMapping(normalized)

    @field_serializer("provider_usage")
    def serialize_provider_usage(
        self,
        value: Mapping[str, int | float],
    ) -> dict[str, int | float]:
        """把冻结的用量映射序列化为 JSON 对象。."""
        return dict(value)

    @model_validator(mode="after")
    def validate_and_populate_id(self) -> Evidence:
        """证据必须有内容，并根据内容与来源生成稳定标识。."""
        if self.excerpt is None and self.value is None:
            raise ValueError("证据必须包含原文摘录或结构化值")
        if not self.evidence_id:
            content = self.excerpt if self.excerpt is not None else str(self.value)
            object.__setattr__(
                self,
                "evidence_id",
                make_stable_id(
                    "evd",
                    self.source_id,
                    content,
                    self.location or "",
                    self.query or "",
                ),
            )
        return self


class Claim(FrozenModel):
    """绑定到实体、层级、维度和证据的研究结论。."""

    text: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    level: AnalysisLevel
    dimension: str = Field(min_length=1)
    claim_id: str = ""
    evidence_ids: tuple[str, ...] = ()
    status: ClaimStatus = ClaimStatus.UNKNOWN
    uncertainty: str | None = None
    important: bool = True

    @model_validator(mode="after")
    def validate_and_populate_id(self) -> Claim:
        """已知或冲突结论必须显式引用证据。."""
        if self.status is ClaimStatus.UNKNOWN:
            if self.evidence_ids:
                raise ValueError("Unknown 结论不能引用证据")
            if not self.uncertainty or not self.uncertainty.strip():
                raise ValueError("Unknown 结论必须说明不确定原因")
        elif not self.evidence_ids:
            raise ValueError("非 Unknown 结论必须引用至少一条证据")
        if not self.claim_id:
            object.__setattr__(
                self,
                "claim_id",
                make_stable_id(
                    "clm",
                    self.entity_id,
                    self.level.value,
                    self.dimension,
                    self.text,
                ),
            )
        return self


class Comparison(FrozenModel):
    """同一层级和维度下多个对象的结构化比较。."""

    entity_ids: tuple[str, ...] = Field(min_length=1)
    level: AnalysisLevel
    dimension: str = Field(min_length=1)
    claim_ids: tuple[str, ...] = ()
    comparison_id: str = ""
    differences: tuple[str, ...] = ()
    commonalities: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()

    @model_validator(mode="after")
    def populate_comparison_id(self) -> Comparison:
        """根据范围与引用生成稳定比较标识。."""
        if not self.comparison_id:
            object.__setattr__(
                self,
                "comparison_id",
                make_stable_id(
                    "cmp",
                    self.level.value,
                    self.dimension,
                    *self.entity_ids,
                    *self.claim_ids,
                ),
            )
        return self


class ReportArtifact(FrozenModel):
    """用户可读 Markdown 报告与独立的结构化关系索引。."""

    markdown: str = Field(min_length=1)
    claim_ids: tuple[str, ...] = ()
    comparison_ids: tuple[str, ...] = ()
    cross_level_conclusions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()


class BudgetEstimate(FrozenModel):
    """一次调用或累计预算的四类可加总用量。."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tavily_calls: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)

    def __add__(self, other: BudgetEstimate) -> BudgetEstimate:
        """逐项合并预算用量。."""
        if not isinstance(other, BudgetEstimate):
            return NotImplemented
        return BudgetEstimate(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            tavily_calls=self.tavily_calls + other.tavily_calls,
            cost_usd=self.cost_usd + other.cost_usd,
        )

    def __sub__(self, other: BudgetEstimate) -> BudgetEstimate:
        """逐项扣减预算；透支会被字段校验拒绝。."""
        if not isinstance(other, BudgetEstimate):
            return NotImplemented
        return BudgetEstimate(
            input_tokens=self.input_tokens - other.input_tokens,
            output_tokens=self.output_tokens - other.output_tokens,
            tavily_calls=self.tavily_calls - other.tavily_calls,
            cost_usd=self.cost_usd - other.cost_usd,
        )


class BudgetSnapshot(FrozenModel):
    """某个时点的预算上限、已用、预留和剩余值。."""

    limit: BudgetEstimate = Field(default_factory=BudgetEstimate)
    spent: BudgetEstimate = Field(default_factory=BudgetEstimate)
    reserved: BudgetEstimate = Field(default_factory=BudgetEstimate)
    remaining: BudgetEstimate = Field(default_factory=BudgetEstimate)


class GateViolation(FrozenModel):
    """确定性校验发现的一项可定位问题。."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: tuple[str | int, ...] = ()
    severity: GateSeverity = GateSeverity.ERROR


class ErrorRecord(FrozenModel):
    """可写入 Manifest 的脱敏错误摘要。."""

    code: str = Field(min_length=1)
    category: ErrorCategory
    message: str = Field(min_length=1)
    detail: str | None = Field(default=None, max_length=500)
    exception_type: str | None = Field(default=None, max_length=200)
    cause_type: str | None = Field(default=None, max_length=200)
    transport_reason: str | None = Field(default=None, max_length=100)
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_error_code: str | None = Field(default=None, max_length=200)
    request_id: str | None = Field(default=None, max_length=300)
    retryable: bool = False
    occurred_at: datetime = Field(default_factory=utc_now)


class RunBundle(FrozenModel):
    """同一 artifact revision 下完整的结构化业务产物。."""

    run_id: str = Field(min_length=1)
    scope: ScopeSpec
    report: ReportArtifact
    budget: BudgetSnapshot
    status: RunStatus
    sources: tuple[Source, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    claims: tuple[Claim, ...] = ()
    comparisons: tuple[Comparison, ...] = ()
    artifact_revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)

    def traceability_violations(self) -> list[GateViolation]:
        """运行不访问网络的证据链硬门禁。."""
        from opencompete.analysis import validate_traceability

        return validate_traceability(self)


class RunManifest(FrozenModel):
    """最后发布的运行状态与文件索引。."""

    run_id: str = Field(min_length=1)
    status: RunStatus
    scope_revision: int = Field(ge=0)
    artifact_revision: int = Field(ge=0)
    config_snapshot: Mapping[str, Any] = Field(default_factory=dict)
    budget_summary: BudgetSnapshot = Field(default_factory=BudgetSnapshot)
    file_hashes: Mapping[str, str] = Field(default_factory=dict)
    errors: tuple[ErrorRecord, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("config_snapshot", "file_hashes")
    @classmethod
    def freeze_mappings(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        """防止发布后的 Manifest 内容被原地改写。."""
        return _ImmutableMapping(value)

    @field_validator("config_snapshot")
    @classmethod
    def reject_secret_keys(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        """拒绝把常见密钥字段写入 Manifest。."""
        for key in value:
            normalized = key.casefold().replace("-", "_")
            if (
                normalized in {
                    "api_key",
                    "apikey",
                    "authorization",
                    "access_token",
                    "refresh_token",
                    "secret",
                    "token",
                }
                or normalized.endswith("_api_key")
                or normalized.endswith("_secret")
                or normalized.endswith("_token")
            ):
                raise ValueError("配置快照不能包含密钥字段")
        return value

    @field_serializer("config_snapshot", "file_hashes")
    def serialize_mappings(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """把 Manifest 只读映射序列化为普通 JSON 对象。."""
        return dict(value)


class RunSummary(FrozenModel):
    """列表命令需要的最小运行摘要。."""

    run_id: str = Field(min_length=1)
    status: RunStatus
    scope_revision: int = Field(ge=0)
    artifact_revision: int = Field(ge=0)
    updated_at: datetime


class RunHandle(FrozenModel):
    """Application Runner 返回给 CLI 的运行句柄。."""

    run_id: str = Field(min_length=1)
    status: RunStatus
    scope: ScopeSpec | None = None
    bundle: RunBundle | None = None
    pending_confirmation: bool = False
    confirmation_kind: ConfirmationKind | None = None
    discovery_candidates: tuple[DiscoveryCandidate, ...] = ()
