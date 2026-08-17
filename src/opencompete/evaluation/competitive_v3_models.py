"""OpenCompete Bench v3：官方 RACE 四维 + 独立 FACT/门禁/成本契约。."""

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

from opencompete.evaluation.competitive_models import (
    ClaimMateriality,
    CompetitiveEvaluationStatus,
    CompetitiveFactEvaluation,
    CompetitiveReferenceMode,
    CompetitiveReviewStatus,
    ReferenceClaimType,
)
from opencompete.evaluation.competitive_v3_race import (
    OFFICIAL_RACE_DIMENSIONS,
    OPENCOMPETE_LOCAL_RACE,
    OfficialMethodBase,
    RaceAggregateResult,
    RaceStabilityDiagnostic,
)
from opencompete.evaluation.models import (
    CostMetrics,
    EvaluationTarget,
    HardGateEvaluation,
)
from opencompete.models import FrozenModel, ResearchRequest, ScopeSpec, utc_now
from opencompete.security import validate_public_http_url

COMPETITIVE_V3_EVALUATION_CONTRACT_VERSION = "opencompete-competitive-evaluation-v3.0.0"


def normalize_v3_task_text(value: str) -> str:
    """规范 v3 任务文本的 Unicode 与换行格式。."""
    normalized = unicodedata.normalize("NFC", value)
    return normalized.replace("\r\n", "\n").replace("\r", "\n").strip()


class RaceV3Dimension(str, Enum):
    """官方 RACE 四维（insight，不用本地旧名 depth）。."""

    COMPREHENSIVENESS = "comprehensiveness"
    INSIGHT = "insight"
    INSTRUCTION_FOLLOWING = "instruction_following"
    READABILITY = "readability"


class RaceV3Criterion(FrozenModel):
    """官方 RACE 维度内的带权 Criterion。."""

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    weight: float = Field(gt=0.0, le=1.0)
    critical: bool = False


class RaceV3DimensionCriteria(FrozenModel):
    """单个官方 RACE 维度的定义与 Criterion 集合。."""

    definition: str = Field(min_length=1)
    criteria: tuple[RaceV3Criterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_weights(self) -> Self:
        """校验 Criterion 标识唯一且权重总和为一。."""
        identifiers = [item.criterion_id for item in self.criteria]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("同一维度 criterion_id 不能重复")
        if abs(sum(item.weight for item in self.criteria) - 1.0) > 1e-9:
            raise ValueError("维内 Criterion 权重之和必须为 1")
        return self


class RaceV3FactCriterion(FrozenModel):
    """v3 FACT 评测需要覆盖的 Reference Claim 集合。."""

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    reference_claim_ids: tuple[str, ...] = Field(min_length=1)
    critical: bool = False


class RaceV3CriteriaDocument(FrozenModel):
    """四维 Schema，禁止 v2 五维键。."""

    schema_version: int = Field(default=3, ge=3)
    case_id: str = Field(min_length=1)
    dimension_weights: Mapping[str, float]
    dimensions: Mapping[str, RaceV3DimensionCriteria]
    fact: tuple[RaceV3FactCriterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        """校验 v3 criteria 恰好覆盖官方四维和唯一标识。."""
        expected = set(OFFICIAL_RACE_DIMENSIONS)
        if set(self.dimension_weights) != expected or set(self.dimensions) != expected:
            raise ValueError("v3 criteria 必须且只能包含官方 RACE 四维")
        if abs(sum(self.dimension_weights.values()) - 1.0) > 1e-9:
            raise ValueError("四维权重之和必须为 1")
        ids = [
            item.criterion_id
            for dim in self.dimensions.values()
            for item in dim.criteria
        ]
        ids.extend(item.criterion_id for item in self.fact)
        if len(ids) != len(set(ids)):
            raise ValueError("criterion_id 必须全局唯一")
        return self


class CompetitiveV3CaseDefinition(FrozenModel):
    """v3 案例的任务、冻结范围和时间边界定义。."""

    schema_version: int = Field(default=3, ge=3)
    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    as_of: date
    reference_mode: CompetitiveReferenceMode = CompetitiveReferenceMode.POSITIVE
    research_request: ResearchRequest
    expected_scope: ScopeSpec


class CompetitiveV3ReferenceClaim(FrozenModel):
    """v3 Reference 中用于 FACT 对照的原子结论。."""

    claim_id: str = Field(min_length=1)
    claim_type: ReferenceClaimType
    materiality: ClaimMateriality = ClaimMateriality.MAJOR
    text: str = Field(min_length=1)
    source_ids: tuple[str, ...] = ()


class CompetitiveV3ReferenceClaimsDocument(FrozenModel):
    """一个 v3 案例的 Reference Claim 文档。."""

    schema_version: int = Field(default=3, ge=3)
    case_id: str = Field(min_length=1)
    claims: tuple[CompetitiveV3ReferenceClaim, ...] = Field(min_length=1)


class CompetitiveV3Source(FrozenModel):
    """v3 Reference 的公开来源元数据。."""

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    publisher: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """仅接受符合安全边界的公开 HTTP/HTTPS URL。."""
        return validate_public_http_url(value)


class CompetitiveV3SourcesDocument(FrozenModel):
    """一个 v3 案例的公开来源文档。."""

    schema_version: int = Field(default=3, ge=3)
    case_id: str = Field(min_length=1)
    sources: tuple[CompetitiveV3Source, ...] = Field(min_length=1)


class CompetitiveV3ReviewDocument(FrozenModel):
    """v3 案例的作者与复核状态记录。."""

    schema_version: int = Field(default=3, ge=3)
    case_id: str = Field(min_length=1)
    status: CompetitiveReviewStatus
    notes: str = Field(min_length=1)


class CompetitiveV3BenchmarkCase(FrozenModel):
    """已校验并可执行的完整 v3 Benchmark 案例。."""

    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    as_of: date
    reference_mode: CompetitiveReferenceMode
    research_request: ResearchRequest
    expected_scope: ScopeSpec
    prompt: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    criteria: RaceV3CriteriaDocument
    reference_claims: tuple[CompetitiveV3ReferenceClaim, ...]
    sources: tuple[CompetitiveV3Source, ...]
    review: CompetitiveV3ReviewDocument
    normalized_task_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CompetitiveV3DatasetCaseEntry(FrozenModel):
    """数据集索引中一个案例的相对路径和内容哈希。."""

    case_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CompetitiveV3DatasetManifest(FrozenModel):
    """v3 数据集索引与版本元数据。."""

    schema_version: int = Field(default=3, ge=3)
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    language: str = Field(min_length=1)
    cases: tuple[CompetitiveV3DatasetCaseEntry, ...] = Field(min_length=1)


class CompetitiveV3BenchmarkDataset(FrozenModel):
    """加载完成后的 v3 数据集及其案例集合。."""

    dataset_id: str
    version: str
    language: str
    cases: tuple[CompetitiveV3BenchmarkCase, ...] = Field(min_length=1)

    def get(self, case_id: str) -> CompetitiveV3BenchmarkCase:
        """按案例标识返回唯一的已加载案例。."""
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise KeyError(case_id)


class CompetitiveV3RaceBlock(FrozenModel):
    """结果中的 RACE 主分块。."""

    dimension_weights: Mapping[str, float]
    dimensions: Mapping[str, float | None]
    target_weighted_total: float
    reference_weighted_total: float
    overall: float | None
    stability_diagnostic: RaceStabilityDiagnostic | None = None

    @model_validator(mode="after")
    def validate_keys(self) -> Self:
        """校验序列化 RACE 主分只含官方四维。."""
        if set(self.dimensions) != set(OFFICIAL_RACE_DIMENSIONS):
            raise ValueError("race.dimensions 必须为官方四维")
        return self


class CompetitiveV3Eligibility(FrozenModel):
    """v3 结果是否满足趋势比较资格及其原因。."""

    eligible_for_trend: bool
    reasons: tuple[str, ...] = ()


class CompetitiveEvaluationResultV3(FrozenModel):
    """一个 Target 运行与 v3 Benchmark 案例的完整评测结果。."""

    evaluation_id: str = Field(default_factory=lambda: f"eval-v3-{uuid4()}")
    evaluation_contract_version: str = COMPETITIVE_V3_EVALUATION_CONTRACT_VERSION
    result_kind: Literal["opencompete_local_race"] = OPENCOMPETE_LOCAL_RACE
    official_method_base: OfficialMethodBase = Field(default_factory=OfficialMethodBase)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    case_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: EvaluationTarget
    status: CompetitiveEvaluationStatus
    race: CompetitiveV3RaceBlock | None = None
    fact: CompetitiveFactEvaluation
    hard_gates: HardGateEvaluation
    research_cost: CostMetrics
    evaluation_cost: CostMetrics
    eligibility: CompetitiveV3Eligibility
    evaluated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        """拒绝伪装官方评分或包含旧五维主分的结果。."""
        if self.result_kind != OPENCOMPETE_LOCAL_RACE:
            raise ValueError("v3 结果必须为 opencompete_local_race")
        if self.result_kind == "official_race":  # type: ignore[comparison-overlap]
            raise ValueError("禁止 official_race")
        payload = self.model_dump(mode="json")
        if "decision_relevance" in json.dumps(payload):
            # 主分块禁止五维键；允许偶然出现在自由文本 reason 中则过严，检查 race 块
            pass
        if self.race is not None:
            for bad in (
                "decision_relevance",
                "comparison_rigor",
                "causal_synthesis",
                "evidence_grounding",
                "recommendation_quality",
            ):
                if bad in self.race.dimensions:
                    raise ValueError(f"v3 race 不得包含 v2 维度 {bad}")
        return self


class CompetitiveEvaluationBatchResultV3(FrozenModel):
    """一次 v3 批量评测的结果集合。."""

    dataset_id: str
    dataset_version: str
    results: tuple[CompetitiveEvaluationResultV3, ...] = Field(min_length=1)


class CompetitiveEvaluationRequestV3(FrozenModel):
    """发起 v3 数据集评测所需的目标与选项。."""

    dataset: Path
    targets: tuple[EvaluationTarget, ...] = Field(min_length=1)
    allow_authored: bool = False


def race_block_from_aggregate(
    aggregate: RaceAggregateResult,
    *,
    stability: RaceStabilityDiagnostic | None = None,
) -> CompetitiveV3RaceBlock:
    """将官方公式聚合结果映射为 v3 产物中的 RACE 主分块。."""
    dims = {
        item.dimension: item.normalized for item in aggregate.dimensions
    }
    return CompetitiveV3RaceBlock(
        dimension_weights=dict(aggregate.dimension_weights),
        dimensions=dims,
        target_weighted_total=aggregate.target_weighted_total,
        reference_weighted_total=aggregate.reference_weighted_total,
        overall=aggregate.overall,
        stability_diagnostic=stability,
    )


def competitive_v3_case_content_sha256(case: CompetitiveV3BenchmarkCase) -> str:
    """计算用于锁定 v3 案例内容的确定性 SHA-256。."""
    payload = {
        "case_id": case.case_id,
        "prompt": normalize_v3_task_text(case.prompt),
        "reference": normalize_v3_task_text(case.reference),
        "criteria": case.criteria.model_dump(mode="json"),
        "claims": [item.model_dump(mode="json") for item in case.reference_claims],
        "sources": [item.model_dump(mode="json") for item in case.sources],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
