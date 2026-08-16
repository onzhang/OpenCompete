"""OpenCompete Bench v2 的竞品分析专用数据与结果契约。."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Literal, Mapping, Self
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from opencompete.evaluation.models import (
    CostMetrics,
    EvaluationTarget,
    FactSupport,
    HardGateEvaluation,
    MetricStatus,
)
from opencompete.models import FrozenModel, ResearchRequest, ScopeSpec, utc_now
from opencompete.security import validate_public_http_url

COMPETITIVE_EVALUATION_CONTRACT_VERSION = "opencompete-competitive-evaluation-v2.2.3"
COMPETITIVE_JUDGE_PROMPT_VERSION = "opencompete-competitive-judge-v2.2.3"

COMPETITIVE_DIMENSION_WEIGHTS: Mapping[CompetitiveDimension, float]


def normalize_competitive_task_text(value: str) -> str:
    """规范固定任务文本，同时保留大小写和正文内部语义。."""
    normalized = unicodedata.normalize("NFC", value)
    return normalized.replace("\r\n", "\n").replace("\r", "\n").strip()


class CompetitiveReviewStatus(str, Enum):
    """Reference 的真实审核来源，禁止把 AI 复核冒充人工审核。."""

    DRAFT = "draft"
    EXPERT_AI_AUTHORED = "expert_ai_authored"
    EXPERT_AI_DUAL_REVIEWED = "expert_ai_dual_reviewed"
    HUMAN_APPROVED = "human_approved"


class CompetitiveDimension(str, Enum):
    """竞品研究特有的五个互斥质量维度。."""

    DECISION_RELEVANCE = "decision_relevance"
    COMPARISON_RIGOR = "comparison_rigor"
    CAUSAL_SYNTHESIS = "causal_synthesis"
    EVIDENCE_GROUNDING = "evidence_grounding"
    RECOMMENDATION_QUALITY = "recommendation_quality"


COMPETITIVE_DIMENSION_WEIGHTS = {
    CompetitiveDimension.DECISION_RELEVANCE: 0.15,
    CompetitiveDimension.COMPARISON_RIGOR: 0.25,
    CompetitiveDimension.CAUSAL_SYNTHESIS: 0.20,
    CompetitiveDimension.EVIDENCE_GROUNDING: 0.25,
    CompetitiveDimension.RECOMMENDATION_QUALITY: 0.15,
}


class CompetitiveReferenceMode(str, Enum):
    """Reference 在固定数据集中的校准角色。."""

    POSITIVE = "positive"
    MIXED = "mixed"
    EVIDENCE_SCARCITY_CHALLENGE = "evidence_scarcity_challenge"


class CompetitiveCalibrationKind(str, Enum):
    """单缺陷和纯文风校准变体类型。."""

    DECISION_SCOPE_VIOLATION = "decision_scope_violation"
    COMPARISON_MISMATCH = "comparison_mismatch"
    CAUSAL_OVERCLAIM = "causal_overclaim"
    EVIDENCE_OVERCLAIM = "evidence_overclaim"
    UNCONDITIONAL_RECOMMENDATION = "unconditional_recommendation"
    STYLE_ONLY = "style_only"


class ReferenceClaimType(str, Enum):
    """Reference 原子结论类型。."""

    FACT = "fact"
    ANALYSIS = "analysis"
    RECOMMENDATION = "recommendation"
    UNKNOWN = "unknown"


class ClaimMateriality(str, Enum):
    """结论对最终决策的重要程度。."""

    CRITICAL = "critical"
    MAJOR = "major"
    SUPPORTING = "supporting"


class ClaimSupportType(str, Enum):
    """固定来源对 Reference Claim 的支持角色。."""

    DIRECT = "direct"
    CORROBORATING = "corroborating"
    CONTEXT = "context"


class SourceRetrievalStatus(str, Enum):
    """Benchmark 构建时的来源访问状态。."""

    LIVE_VERIFIED = "live_verified"
    BLOCKED = "blocked"
    NOT_SNAPSHOTTED = "not_snapshotted"
    ARCHIVED = "archived"


class CompetitiveEvaluationStatus(str, Enum):
    """竞品评测的最终资格与失败语义。."""

    COMPLETED = "completed"
    INELIGIBLE = "ineligible"
    JUDGE_FAILED = "judge_failed"
    JUDGE_UNSTABLE = "judge_unstable"
    REFERENCE_INVALID = "reference_invalid"
    PARTIAL_DIAGNOSTIC = "partial_diagnostic"


class StabilityStatus(str, Enum):
    """双顺序 Judge 的稳定性状态。."""

    STABLE = "stable"
    UNSTABLE = "unstable"
    NOT_RUN = "not_run"


class ReferenceCoverageStatus(str, Enum):
    """Target 对一个 Reference Claim 的覆盖状态。."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    HONEST_UNKNOWN = "honest_unknown"
    OMITTED = "omitted"
    NOT_APPLICABLE = "not_applicable"


class TargetClaimSupportStatus(str, Enum):
    """Judge 对一个 Target Claim 整体证据状态的判断。."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    NONFACTUAL_ANALYSIS = "nonfactual_analysis"
    HONEST_UNKNOWN = "honest_unknown"


class DecisionContext(FrozenModel):
    """案例要支持的具体业务决策。."""

    audience: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    geography: str | tuple[str, ...]
    assumptions: tuple[str, ...] = Field(min_length=1)


class CriterionAnchors(FrozenModel):
    """案例级 0/4/6/8/10 评分锚点。."""

    values: Mapping[str, str]

    @field_validator("values")
    @classmethod
    def validate_anchor_values(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        """要求每项标准使用同一组离散校准锚点。."""
        expected = {"0", "4", "6", "8", "10"}
        if set(value) != expected or any(not item.strip() for item in value.values()):
            raise ValueError("评分锚点必须完整包含 0、4、6、8、10")
        return dict(value)


class CompetitiveCriterion(FrozenModel):
    """一个可定位、带权重和案例锚点的竞品质量标准。."""

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    weight: float = Field(gt=0.0, le=1.0)
    critical: bool = False
    anchors: Mapping[str, str]

    @field_validator("anchors")
    @classmethod
    def validate_anchors(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        """复用统一锚点校验并冻结映射。."""
        return CriterionAnchors(values=value).values


class CompetitiveDimensionCriteria(FrozenModel):
    """单个主维度的定义与维内 Criteria。."""

    definition: str = Field(min_length=1)
    criteria: tuple[CompetitiveCriterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_criteria(self) -> Self:
        """校验维内标识唯一且权重可确定性复算。."""
        identifiers = [item.criterion_id for item in self.criteria]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("同一维度的 criterion_id 不能重复")
        if abs(sum(item.weight for item in self.criteria) - 1.0) > 1e-9:
            raise ValueError("每个维度内的 Criterion 权重之和必须为 1")
        return self


class CompetitiveFactCriterion(FrozenModel):
    """Reference Claim 覆盖与事实核验标准。."""

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    reference_claim_ids: tuple[str, ...] = Field(min_length=1)
    critical: bool = False


class CompetitiveCriteriaDocument(FrozenModel):
    """案例级五维权重、Criteria 与 FACT 要求。."""

    schema_version: int = Field(default=2, ge=2)
    case_id: str = Field(min_length=1)
    dimension_weights: Mapping[CompetitiveDimension, float]
    dimensions: Mapping[CompetitiveDimension, CompetitiveDimensionCriteria]
    fact: tuple[CompetitiveFactCriterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        """校验五维完整、权重边界和全局 Criterion 标识。."""
        expected = set(CompetitiveDimension)
        if set(self.dimension_weights) != expected or set(self.dimensions) != expected:
            raise ValueError("竞品质量必须且只能包含五个固定主维度")
        if any(
            abs(self.dimension_weights[dimension] - expected_weight) > 1e-9
            for dimension, expected_weight in COMPETITIVE_DIMENSION_WEIGHTS.items()
        ):
            raise ValueError("Bench v2.2 的五个主维度必须使用统一冻结权重")
        total_criteria = sum(
            len(dimension.criteria) for dimension in self.dimensions.values()
        )
        if not 10 <= total_criteria <= 15:
            raise ValueError("每个案例必须包含 10 到 15 条质量 Criterion")
        if any(
            not 2 <= len(dimension.criteria) <= 3
            for dimension in self.dimensions.values()
        ):
            raise ValueError("每个质量维度必须包含 2 到 3 条 Criterion")
        critical_count = sum(
            item.critical
            for dimension in self.dimensions.values()
            for item in dimension.criteria
        )
        if not 2 <= critical_count <= 4:
            raise ValueError("每个案例必须且只能包含 2 到 4 条关键 Criterion")
        identifiers = [
            item.criterion_id
            for dimension in self.dimensions.values()
            for item in dimension.criteria
        ]
        identifiers.extend(item.criterion_id for item in self.fact)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("质量和 FACT 的 criterion_id 必须全局唯一")
        return self


class CompetitiveBenchmarkSource(FrozenModel):
    """具有时间、地区、快照和方法信息的固定来源。."""

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    url: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    as_of: date
    published_at: datetime | None = None
    retrieved_at: date | datetime | None = None
    valid_as_of: bool | date | None = None
    geography: str | tuple[str, ...]
    content_type: str = Field(min_length=1)
    retrieval_status: SourceRetrievalStatus
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    archive_locator: str | None = None
    methodology_note: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Benchmark 来源也只能使用规范化公网 URL。."""
        return validate_public_http_url(value)

    @field_validator("retrieval_status", mode="before")
    @classmethod
    def normalize_retrieval_status(cls, value: object) -> object:
        """把作者阶段的细分描述归并为四个可审计状态。."""
        aliases = {
            "ok": SourceRetrievalStatus.LIVE_VERIFIED.value,
            "reachable": SourceRetrievalStatus.LIVE_VERIFIED.value,
            "reachable_dynamic_not_frozen": (
                SourceRetrievalStatus.NOT_SNAPSHOTTED.value
            ),
            "not_content_frozen": SourceRetrievalStatus.NOT_SNAPSHOTTED.value,
            "located_via_official_search": (
                SourceRetrievalStatus.NOT_SNAPSHOTTED.value
            ),
            "blocked_after_redirect": SourceRetrievalStatus.BLOCKED.value,
            "reachable_redirected_from_legacy_support": (
                SourceRetrievalStatus.ARCHIVED.value
            ),
            "reachable_archived_page": SourceRetrievalStatus.ARCHIVED.value,
        }
        return aliases.get(value, value) if isinstance(value, str) else value


class CompetitiveSourcesDocument(FrozenModel):
    """v2 sources.json。."""

    schema_version: int = Field(default=2, ge=2)
    case_id: str = Field(min_length=1)
    sources: tuple[CompetitiveBenchmarkSource, ...] = Field(min_length=1)


class ReferenceClaimSupport(FrozenModel):
    """Reference Claim 到固定来源的精确定位。."""

    source_id: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    support_type: ClaimSupportType
    quote: str | None = None
    quote_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_quote_hash(self) -> Self:
        """证据摘录和哈希必须同时存在且可由原文复算。."""
        if (self.quote is None) != (self.quote_sha256 is None):
            raise ValueError("证据摘录和 quote_sha256 必须同时提供")
        if self.quote is not None:
            quote = self.quote.strip()
            if not quote:
                raise ValueError("证据摘录不能为空")
            expected = hashlib.sha256(quote.encode("utf-8")).hexdigest()
            if self.quote_sha256 != expected:
                raise ValueError("quote_sha256 与证据摘录原文不一致")
            object.__setattr__(self, "quote", quote)
        return self


class ReferenceClaim(FrozenModel):
    """Reference 的原子事实、分析、建议或 Unknown。."""

    claim_id: str = Field(min_length=1)
    claim_type: ReferenceClaimType
    text: str = Field(min_length=1)
    entities: tuple[str, ...] = Field(min_length=1)
    level: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    materiality: ClaimMateriality
    time_scope: str = Field(min_length=1)
    geography: str | tuple[str, ...]
    metric_definition: str | None = None
    support: tuple[ReferenceClaimSupport, ...] = ()
    uncertainty: str | None = None
    recommendation_conditions: tuple[str, ...] = ()

    @field_validator("materiality", mode="before")
    @classmethod
    def normalize_materiality(cls, value: object) -> object:
        """兼容作者阶段使用的高、中、低英文别名。."""
        aliases = {
            "high": ClaimMateriality.CRITICAL.value,
            "medium": ClaimMateriality.MAJOR.value,
            "low": ClaimMateriality.SUPPORTING.value,
        }
        return aliases.get(value, value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_claim_semantics(self) -> Self:
        """约束事实、建议和 Unknown 各自所需的审计字段。."""
        if self.claim_type is ReferenceClaimType.UNKNOWN:
            if not self.uncertainty:
                raise ValueError("Unknown Reference Claim 必须说明证据缺口")
        elif self.claim_type is ReferenceClaimType.FACT:
            if not self.support:
                raise ValueError("事实型 Reference Claim 必须具有来源定位")
            direct = [
                item
                for item in self.support
                if item.support_type is ClaimSupportType.DIRECT
            ]
            if not direct:
                raise ValueError("事实型 Reference Claim 必须具有直接来源")
            if self.materiality in {
                ClaimMateriality.CRITICAL,
                ClaimMateriality.MAJOR,
            } and any(item.quote is None for item in direct):
                raise ValueError("关键和主要事实的直接来源必须冻结证据摘录")
        if (
            self.claim_type is ReferenceClaimType.RECOMMENDATION
            and not self.recommendation_conditions
        ):
            raise ValueError("建议型 Reference Claim 必须给出适用条件")
        return self


class ReferenceClaimsDocument(FrozenModel):
    """v2 reference_claims.json。."""

    schema_version: int = Field(default=2, ge=2)
    case_id: str = Field(min_length=1)
    claims: tuple[ReferenceClaim, ...] = Field(min_length=1)


class ReviewAgent(FrozenModel):
    """不冒充人类身份的专家 Agent 记录。."""

    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    model: str | None = None
    prompt_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ReviewIssue(FrozenModel):
    """双专家复核提出并关闭的问题。."""

    issue_id: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    description: str = Field(min_length=1)
    resolved: bool = False
    resolution: str | None = None


class CompetitiveReviewDocument(FrozenModel):
    """Reference 的作者与独立复核审计记录。."""

    schema_version: int = Field(default=2, ge=2)
    case_id: str = Field(min_length=1)
    status: CompetitiveReviewStatus
    review_kind: str = Field(min_length=1)
    author_agent: ReviewAgent
    reviewer_agent: ReviewAgent | None = None
    review_rounds: int = Field(default=0, ge=0)
    issues: tuple[ReviewIssue, ...] = ()
    unresolved_material_issues: int = Field(default=0, ge=0)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_review_status(self) -> Self:
        """复算重大问题并阻止未经证据的审核升级。."""
        unresolved = sum(
            not issue.resolved and issue.severity in {"critical", "major"}
            for issue in self.issues
        )
        if unresolved != self.unresolved_material_issues:
            raise ValueError("未关闭重大问题数量必须由 issues 复算")
        if self.status in {
            CompetitiveReviewStatus.EXPERT_AI_AUTHORED,
            CompetitiveReviewStatus.EXPERT_AI_DUAL_REVIEWED,
        } and (not self.author_agent.model or not self.author_agent.prompt_sha256):
            raise ValueError("专家作者状态必须冻结模型身份和作者 Prompt 哈希")
        if self.status is CompetitiveReviewStatus.EXPERT_AI_DUAL_REVIEWED:
            if (
                self.reviewer_agent is None
                or not self.reviewer_agent.model
                or not self.reviewer_agent.prompt_sha256
                or self.review_rounds < 2
                or unresolved
            ):
                raise ValueError("双 AI 复核状态必须有独立 Reviewer 且无未关闭重大问题")
        if self.status is CompetitiveReviewStatus.HUMAN_APPROVED:
            raise ValueError("v2 当前契约不允许用 AI 审核数据声明 human_approved")
        return self


class CompetitiveCaseDefinition(FrozenModel):
    """v2 case.json。."""

    schema_version: int = Field(default=2, ge=2)
    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    reference_mode: CompetitiveReferenceMode
    as_of: date
    decision_context: DecisionContext
    research_request: ResearchRequest
    expected_scope: ScopeSpec

    @model_validator(mode="after")
    def validate_case_scope(self) -> Self:
        """固定案例不得发现候选，且请求与 Scope 模板必须一致。."""
        if self.research_request.discover_candidates:
            raise ValueError("固定 Bench 案例不能启用候选发现")
        if self.expected_scope.frozen or self.expected_scope.revision != 0:
            raise ValueError("磁盘中的 expected_scope 必须是未运行的固定模板")
        if tuple(self.research_request.entities) != tuple(self.expected_scope.entities):
            raise ValueError("固定 ResearchRequest 与 expected_scope 的对象不一致")
        if tuple(self.research_request.levels) != tuple(self.expected_scope.levels):
            raise ValueError("固定 ResearchRequest 与 expected_scope 的层级不一致")
        return self


class CompetitiveDatasetManifest(FrozenModel):
    """Bench v2 dataset.json。."""

    schema_version: int = Field(default=2, ge=2)
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    language: str = Field(min_length=1)
    author_prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer_prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: tuple[CompetitiveDatasetCaseEntry, ...] = Field(min_length=1)


class CompetitiveDatasetCaseEntry(FrozenModel):
    """数据集索引中的案例路径与冻结语义哈希。."""

    case_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CompetitiveBenchmarkCase(FrozenModel):
    """通过全部跨文件校验的 Bench v2 案例。."""

    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    reference_mode: CompetitiveReferenceMode
    as_of: date
    decision_context: DecisionContext
    research_request: ResearchRequest
    expected_scope: ScopeSpec
    prompt: str = Field(min_length=1)
    reference_report: str = Field(min_length=1)
    criteria: CompetitiveCriteriaDocument
    sources: tuple[CompetitiveBenchmarkSource, ...] = Field(min_length=1)
    reference_claims: tuple[ReferenceClaim, ...] = Field(min_length=1)
    review: CompetitiveReviewDocument

    @field_validator("prompt", mode="before")
    @classmethod
    def normalize_task_prompt(cls, value: object) -> object:
        """在固定案例聚合边界建立唯一、不可变的任务文本。."""
        if not isinstance(value, str):
            return value
        return normalize_competitive_task_text(value)

    @property
    def normalized_task_text(self) -> str:
        """返回正式生成入口与 Judge 共同消费的规范化任务文本。."""
        return self.prompt

    @property
    def normalized_task_sha256(self) -> str:
        """返回规范化任务文本 UTF-8 字节的同源 SHA-256。."""
        return hashlib.sha256(self.normalized_task_text.encode("utf-8")).hexdigest()


class CompetitiveBenchmarkDataset(FrozenModel):
    """完成聚合校验的 Bench v2 数据集。."""

    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    language: str = Field(min_length=1)
    cases: tuple[CompetitiveBenchmarkCase, ...] = Field(min_length=1)

    def get_case(self, case_id: str) -> CompetitiveBenchmarkCase:
        """按稳定 ID 返回固定案例。."""
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise KeyError(f"评测案例不存在：{case_id}")


class CompetitiveCalibrationVariantDefinition(FrozenModel):
    """一个案例专用、可审计的校准变体。."""

    variant_id: str = Field(min_length=1)
    kind: CompetitiveCalibrationKind
    expected_primary_dimension: CompetitiveDimension | None = None
    defect_text: str = ""
    report_anchor: str | None = None
    replace_claim_id: str | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> Self:
        """区分单缺陷和纯文风变体所需字段。."""
        if self.kind is CompetitiveCalibrationKind.STYLE_ONLY:
            if (
                self.expected_primary_dimension is not None
                or self.defect_text
                or self.report_anchor is not None
                or self.replace_claim_id is not None
            ):
                raise ValueError("纯文风变体不能注入缺陷或指定主降维度")
        elif (
            self.expected_primary_dimension is None
            or not self.defect_text.strip()
            or not self.report_anchor
            or not self.report_anchor.strip()
            or not self.replace_claim_id
            or not self.replace_claim_id.strip()
        ):
            raise ValueError("单缺陷变体必须指定主维度、替换锚点、Claim 和缺陷文本")
        return self


class CompetitiveCalibrationDocument(FrozenModel):
    """一个案例的五种单缺陷和一种纯文风校准集合。."""

    schema_version: int = Field(default=2, ge=2)
    case_id: str = Field(min_length=1)
    variants: tuple[CompetitiveCalibrationVariantDefinition, ...]

    @model_validator(mode="after")
    def validate_variants(self) -> Self:
        """要求六种冻结变体完整且唯一。."""
        kinds = [item.kind for item in self.variants]
        if len(kinds) != len(set(kinds)) or set(kinds) != set(
            CompetitiveCalibrationKind
        ):
            raise ValueError("每案必须且只能包含五种单缺陷和一种纯文风变体")
        ids = [item.variant_id for item in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError("校准 variant_id 不能重复")
        return self


class CompetitiveCalibrationVariantResult(FrozenModel):
    """一次真实 Judge 对校准变体的评分结果。."""

    variant_id: str = Field(min_length=1)
    kind: CompetitiveCalibrationKind
    expected_primary_dimension: CompetitiveDimension | None = None
    quality: CompetitiveQualityEvaluation
    quality_repetitions: tuple[CompetitiveQualityEvaluation, ...] = Field(min_length=2)
    repeat_stable: bool
    passed: bool


class CompetitiveCalibrationResult(FrozenModel):
    """一个案例完整六变体的校准摘要。."""

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    judge_model: str | None = None
    judge_prompt_version: str = COMPETITIVE_JUDGE_PROMPT_VERSION
    baseline: CompetitiveQualityEvaluation
    baseline_repetitions: tuple[CompetitiveQualityEvaluation, ...] = Field(min_length=2)
    variants: tuple[CompetitiveCalibrationVariantResult, ...]
    defect_pass_rate: float = Field(ge=0.0, le=1.0)
    style_only_passed: bool
    passed: bool
    evaluated_at: datetime = Field(default_factory=utc_now)


def competitive_case_content_sha256(case: CompetitiveBenchmarkCase) -> str:
    """哈希影响 v2 评测语义的全部固定输入。."""
    payload = case.model_dump(mode="json")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class JudgeLocator(FrozenModel):
    """Judge 理由在输入内的稳定定位。."""

    kind: Literal["report", "claim", "evidence", "source"]
    identifier: str = Field(min_length=1)


class CompetitiveCriterionJudgeScore(FrozenModel):
    """Judge 对一项 Criterion 的成对原始评分。."""

    dimension: CompetitiveDimension
    criterion_id: str = Field(min_length=1)
    article_1_score: float = Field(ge=0.0, le=10.0)
    article_2_score: float = Field(ge=0.0, le=10.0)
    article_1_reason: str = Field(min_length=1)
    article_2_reason: str = Field(min_length=1)
    article_1_locators: tuple[JudgeLocator, ...] = ()
    article_2_locators: tuple[JudgeLocator, ...] = ()
    article_1_material_defects: tuple[str, ...] = ()
    article_2_material_defects: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)


class CompetitiveJudgeWarning(FrozenModel):
    """Judge 发现固定输入不足或其他全局问题。."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    affected_criterion_ids: tuple[str, ...] = ()


class CompetitiveJudgeOutput(FrozenModel):
    """单一文章顺序的 Judge 严格输出。."""

    prompt_version: str = Field(min_length=1)
    input_order_id: str = Field(min_length=1)
    criteria: tuple[CompetitiveCriterionJudgeScore, ...] = Field(min_length=1)
    global_warnings: tuple[CompetitiveJudgeWarning, ...] = ()


class AggregatedCriterionScore(FrozenModel):
    """双顺序平均后的 Criterion 分数与稳定性。."""

    dimension: CompetitiveDimension
    criterion_id: str = Field(min_length=1)
    target_score: float = Field(ge=0.0, le=10.0)
    reference_score: float = Field(ge=0.0, le=10.0)
    target_order_gap: float = Field(ge=0.0)
    reference_order_gap: float = Field(ge=0.0)
    unstable: bool
    critical: bool
    forward: CompetitiveCriterionJudgeScore
    reverse: CompetitiveCriterionJudgeScore


class CompetitiveDimensionScore(FrozenModel):
    """确定性计算的单个竞品质量维度。."""

    dimension: CompetitiveDimension
    target_absolute: float = Field(ge=0.0, le=10.0)
    reference_absolute: float = Field(ge=0.0, le=10.0)
    target_minus_reference: float = Field(ge=-10.0, le=10.0)
    relative_to_reference: float | None = Field(default=None, ge=0.0, le=1.0)
    order_gap: float = Field(ge=0.0)
    unstable: bool


class CompetitiveQualityEvaluation(FrozenModel):
    """五维绝对分、辅助相对分和双顺序稳定性。."""

    status: MetricStatus
    criteria: tuple[AggregatedCriterionScore, ...] = ()
    dimensions: tuple[CompetitiveDimensionScore, ...] = ()
    target_overall: float | None = Field(default=None, ge=0.0, le=10.0)
    reference_overall: float | None = Field(default=None, ge=0.0, le=10.0)
    overall_relative_to_reference: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    overall_order_gap: float | None = Field(default=None, ge=0.0)
    stability: StabilityStatus = StabilityStatus.NOT_RUN
    forward_output: CompetitiveJudgeOutput | None = None
    reverse_output: CompetitiveJudgeOutput | None = None
    reason: str | None = None


class ReferenceClaimCoverage(FrozenModel):
    """Target 对一个固定 Reference Claim 的覆盖判断。."""

    reference_claim_id: str = Field(min_length=1)
    status: ReferenceCoverageStatus
    target_claim_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1)


class TargetClaimSupportAssessment(FrozenModel):
    """Judge 对 Target 中一个 Claim 的整体支持判断。."""

    claim_id: str = Field(min_length=1)
    status: TargetClaimSupportStatus
    evidence_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1)


class CompetitiveFactEvaluation(FrozenModel):
    """Reference Claim 覆盖率与关键缺口。."""

    status: MetricStatus
    claims: tuple[ReferenceClaimCoverage, ...] = ()
    target_claims: tuple[TargetClaimSupportAssessment, ...] = ()
    target_relationships: tuple[FactSupport, ...] = ()
    critical_claim_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    major_claim_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    target_claim_evidence_support_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    target_relationship_support_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    critical_unknown_preservation_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    critical_contradiction_count: int = Field(default=0, ge=0)
    unsupported_important_target_claim_count: int = Field(default=0, ge=0)
    eligible: bool = False
    reason: str | None = None


class CompetitiveFactJudgeOutput(FrozenModel):
    """一次固定输入 FACT Judge 的原始返回。."""

    prompt_version: str = Field(min_length=1)
    reference_claims: tuple[ReferenceClaimCoverage, ...]
    target_claims: tuple[TargetClaimSupportAssessment, ...]
    target_relationships: tuple[FactSupport, ...]
    warnings: tuple[CompetitiveJudgeWarning, ...] = ()


class CompetitiveEvaluationResult(FrozenModel):
    """一个 v2 Case 与一个历史 Bundle revision 的完整结果。."""

    evaluation_id: str = Field(default_factory=lambda: f"eval-v2-{uuid4()}")
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    evaluation_contract_version: str = COMPETITIVE_EVALUATION_CONTRACT_VERSION
    case_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    judge_implementation: str | None = None
    judge_model: str | None = None
    judge_prompt_version: str | None = None
    target: EvaluationTarget
    status: CompetitiveEvaluationStatus
    eligible_for_comparison: bool
    hard_gates: HardGateEvaluation
    quality: CompetitiveQualityEvaluation
    fact: CompetitiveFactEvaluation
    cost: CostMetrics
    evaluated_at: datetime = Field(default_factory=utc_now)


class CompetitiveEvaluationBatchResult(FrozenModel):
    """一次 v2 批量评测结果。."""

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    results: tuple[CompetitiveEvaluationResult, ...] = Field(min_length=1)


class CompetitiveEvaluationRequest(FrozenModel):
    """v2 评测请求。."""

    dataset: Path
    targets: tuple[EvaluationTarget, ...] = Field(min_length=1)
    allow_authored: bool = False
