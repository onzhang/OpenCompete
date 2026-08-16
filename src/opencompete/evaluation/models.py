"""OpenCompete Bench 的固定数据、评分和结果契约。."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Self
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from opencompete.models import (
    BudgetEstimate,
    FrozenModel,
    GateSeverity,
    GateViolation,
    RunBundle,
    ScopeSpec,
    utc_now,
)
from opencompete.security import validate_public_http_url

EVALUATION_CONTRACT_VERSION = "opencompete-evaluation-v1"


class ReviewStatus(str, Enum):
    """参考报告的人工审核状态。."""

    DRAFT = "draft"
    APPROVED = "approved"


class SourceAuthority(str, Enum):
    """Benchmark 来源的权威类型。."""

    OFFICIAL = "official"
    REGULATOR = "regulator"
    FILING = "filing"
    STANDARD = "standard"
    INTERGOVERNMENTAL = "intergovernmental"
    OTHER = "other"


class MetricStatus(str, Enum):
    """需要 Judge 的指标运行状态。."""

    COMPLETED = "completed"
    NOT_RUN = "not_run"
    FAILED = "failed"


class RaceDimension(str, Enum):
    """RACE 的四个报告质量维度。."""

    COMPREHENSIVENESS = "comprehensiveness"
    DEPTH = "depth"
    INSTRUCTION_FOLLOWING = "instruction_following"
    READABILITY = "readability"


class ReferenceReview(FrozenModel):
    """参考报告是否经过真实人工审核。."""

    status: ReviewStatus = ReviewStatus.DRAFT
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    notes: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_review_evidence(self) -> Self:
        """只有审核人和时间都存在时才能声明已审核。."""
        if self.status is ReviewStatus.APPROVED:
            if not self.reviewed_by or self.reviewed_at is None:
                raise ValueError("已审核参考报告必须记录审核人和审核时间")
        elif self.reviewed_by is not None or self.reviewed_at is not None:
            raise ValueError("草稿参考报告不能伪造审核人或审核时间")
        return self


class BenchmarkSource(FrozenModel):
    """案例维护者预先固定的权威来源元数据。."""

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    url: str = Field(min_length=1)
    authority: SourceAuthority
    as_of: date

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Benchmark 也只允许公开网页 URL。."""
        return validate_public_http_url(value)


class RaceCriteria(FrozenModel):
    """单个案例针对 RACE 四维的任务级判定要点。."""

    comprehensiveness: tuple[str, ...] = Field(min_length=1)
    depth: tuple[str, ...] = Field(min_length=1)
    instruction_following: tuple[str, ...] = Field(min_length=1)
    readability: tuple[str, ...] = Field(min_length=1)


class FactCriterion(FrozenModel):
    """单个案例要求 Judge 核验的一类事实支持关系。."""

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_ids: tuple[str, ...] = Field(min_length=1)


class BenchmarkCriteria(FrozenModel):
    """一项 Benchmark Case 的 RACE 与 FACT Criteria。."""

    case_id: str = Field(min_length=1)
    race: RaceCriteria
    fact: tuple[FactCriterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        """任务级 FACT 标准必须可稳定定位。."""
        identifiers = [criterion.criterion_id for criterion in self.fact]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("FACT Criteria 标识不能重复")
        return self


class BenchmarkCase(FrozenModel):
    """加载并交给硬门禁或 Judge 的完整固定案例。."""

    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    as_of: date
    expected_scope: ScopeSpec
    prompt: str = Field(min_length=1)
    reference_report: str = Field(min_length=1)
    reference_review: ReferenceReview
    criteria: BenchmarkCriteria
    sources: tuple[BenchmarkSource, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_contract(self) -> Self:
        """对齐案例、Criteria、来源和预期范围。."""
        if self.criteria.case_id != self.case_id:
            raise ValueError("Criteria 的 case_id 与案例不一致")
        if self.expected_scope.frozen:
            raise ValueError("Benchmark 预期范围不能冒充用户已确认范围")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Benchmark 来源标识不能重复")
        available = set(source_ids)
        for criterion in self.criteria.fact:
            if not set(criterion.source_ids) <= available:
                raise ValueError("FACT Criteria 引用了案例中不存在的来源")
        if any(source.as_of > self.as_of for source in self.sources):
            raise ValueError("来源的 as_of 不能晚于案例截止日期")
        return self


def benchmark_case_content_sha256(case: BenchmarkCase) -> str:
    """哈希影响评测语义的全部固定案例输入。."""
    payload = {
        "case_id": case.case_id,
        "title": case.title,
        "as_of": case.as_of.isoformat(),
        "expected_scope": case.expected_scope.model_dump(mode="json"),
        "prompt": case.prompt,
        "reference_report": case.reference_report,
        "reference_review": case.reference_review.model_dump(mode="json"),
        "criteria": case.criteria.model_dump(mode="json"),
        "sources": [source.model_dump(mode="json") for source in case.sources],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class DatasetCaseEntry(FrozenModel):
    """dataset.json 中一个案例目录的固定索引。."""

    case_id: str = Field(min_length=1)
    path: str = Field(min_length=1)


class DatasetManifest(FrozenModel):
    """dataset.json 的磁盘格式。."""

    schema_version: int = Field(default=1, ge=1)
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    language: str = Field(min_length=1)
    cases: tuple[DatasetCaseEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_cases(self) -> Self:
        """一个版本不能重复声明同一案例或目录。."""
        case_ids = [entry.case_id for entry in self.cases]
        paths = [entry.path for entry in self.cases]
        if len(case_ids) != len(set(case_ids)) or len(paths) != len(set(paths)):
            raise ValueError("数据集不能包含重复案例或目录")
        return self


class CaseDefinition(FrozenModel):
    """case.json 的磁盘格式。."""

    schema_version: int = Field(default=1, ge=1)
    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    as_of: date
    expected_scope: ScopeSpec
    reference_review: ReferenceReview


class SourcesDocument(FrozenModel):
    """sources.json 的磁盘格式。."""

    schema_version: int = Field(default=1, ge=1)
    case_id: str = Field(min_length=1)
    sources: tuple[BenchmarkSource, ...] = Field(min_length=1)


class BenchmarkDataset(FrozenModel):
    """完成跨文件校验后的内存数据集。."""

    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    language: str = Field(min_length=1)
    cases: tuple[BenchmarkCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_cases(self) -> Self:
        """加载后的案例标识仍必须唯一。."""
        identifiers = [case.case_id for case in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("加载后的 Benchmark 案例标识不能重复")
        return self

    def get_case(self, case_id: str) -> BenchmarkCase:
        """按稳定标识查找案例。."""
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise KeyError(f"评测案例不存在：{case_id}")


class EvaluationTarget(FrozenModel):
    """Benchmark Case 到历史 Run revision 的绑定。."""

    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    artifact_revision: int | None = Field(default=None, ge=1)


class TargetsDocument(FrozenModel):
    """批量 targets.json 的磁盘格式。."""

    targets: tuple[EvaluationTarget, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_targets(self) -> Self:
        """同一目标绑定不能在一次请求内重复执行。."""
        keys = [
            (target.case_id, target.run_id, target.artifact_revision)
            for target in self.targets
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("targets.json 包含重复目标绑定")
        return self


class EvaluationRequest(FrozenModel):
    """Application Runner 接收的离线评测请求。."""

    dataset: Path
    targets: tuple[EvaluationTarget, ...] = Field(min_length=1)
    allow_draft: bool = False

    @model_validator(mode="after")
    def validate_unique_targets(self) -> Self:
        """程序构造的请求也不能重复绑定。."""
        TargetsDocument(targets=self.targets)
        return self


class HardGateEvaluation(FrozenModel):
    """确定性门禁结果；不依赖 Judge。."""

    passed: bool
    violations: tuple[GateViolation, ...] = ()

    @model_validator(mode="after")
    def validate_passed_flag(self) -> Self:
        """确保 passed 与 ERROR 级问题保持一致。."""
        has_errors = any(
            violation.severity is GateSeverity.ERROR
            for violation in self.violations
        )
        if self.passed == has_errors:
            raise ValueError("硬门禁 passed 与 violations 不一致")
        return self


class RaceDimensionScore(FrozenModel):
    """Judge 对单个 RACE 维度的标准化评分。."""

    dimension: RaceDimension
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class RaceEvaluation(FrozenModel):
    """RACE 四维结果或明确的未运行状态。."""

    status: MetricStatus
    dimensions: tuple[RaceDimensionScore, ...] = ()
    reason: str | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        """没有 Judge 时禁止携带任何伪分数。."""
        if self.status is MetricStatus.COMPLETED:
            dimensions = [item.dimension for item in self.dimensions]
            if len(dimensions) != len(set(dimensions)) or set(dimensions) != set(
                RaceDimension
            ):
                raise ValueError("完成的 RACE 必须且只能包含四个维度")
        elif self.dimensions:
            raise ValueError("未完成的 RACE 不能包含分数")
        elif not self.reason:
            raise ValueError("未运行或失败的 RACE 必须记录原因")
        return self


class FactSupport(FrozenModel):
    """Judge 对一条 Claim-Evidence 支持关系的判断。."""

    claim_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    supported: bool
    reason: str = Field(min_length=1)


class FactEvaluation(FrozenModel):
    """FACT 支持关系及其可复算比例。."""

    status: MetricStatus
    relationships: tuple[FactSupport, ...] = ()
    support_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        """FACT 分数必须来自显式支持关系。."""
        keys = [
            (relationship.claim_id, relationship.evidence_id)
            for relationship in self.relationships
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("FACT 不能重复计算同一 Claim-Evidence 关系")
        if self.status is MetricStatus.COMPLETED:
            if self.relationships:
                expected = sum(item.supported for item in self.relationships) / len(
                    self.relationships
                )
                if self.support_ratio is None or abs(self.support_ratio - expected) > 1e-9:
                    raise ValueError("FACT support_ratio 必须由支持关系复算")
            elif self.support_ratio is not None:
                raise ValueError("没有支持关系时不能生成 FACT 分数")
            elif not self.reason:
                raise ValueError("没有可评 Claim 时必须记录 FACT 原因")
        elif self.relationships or self.support_ratio is not None:
            raise ValueError("未完成的 FACT 不能包含关系或分数")
        elif not self.reason:
            raise ValueError("未运行或失败的 FACT 必须记录原因")
        return self


class JudgeEvaluation(FrozenModel):
    """ReportJudge 的完整结构化返回值。."""

    race: RaceEvaluation
    fact: FactEvaluation


class CostMetrics(FrozenModel):
    """从历史 Bundle 读取实际消耗与可能已消费的保守成本。."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tavily_calls: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    runtime_seconds: float | None = Field(default=None, ge=0.0)

    @classmethod
    def from_bundle(cls, bundle: RunBundle) -> CostMetrics:
        """把终态未结算预留视为可能已消费，不推测供应商最终账单。."""
        conservative_usage: BudgetEstimate = (
            bundle.budget.spent + bundle.budget.reserved
        )
        return cls(
            input_tokens=conservative_usage.input_tokens,
            output_tokens=conservative_usage.output_tokens,
            tavily_calls=conservative_usage.tavily_calls,
            cost_usd=conservative_usage.cost_usd,
        )


class EvaluationCaseResult(FrozenModel):
    """一个案例与一个历史 revision 的不可变评测结果。."""

    evaluation_id: str = Field(default_factory=lambda: f"eval-{uuid4()}", min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    evaluation_contract_version: str = Field(
        default=EVALUATION_CONTRACT_VERSION,
        min_length=1,
    )
    case_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    judge_implementation: str | None = None
    judge_model: str | None = None
    judge_max_tokens: int | None = Field(default=None, ge=1)
    judge_prompt_version: str | None = None
    target: EvaluationTarget
    hard_gates: HardGateEvaluation
    race: RaceEvaluation
    fact: FactEvaluation
    cost: CostMetrics
    evaluated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_pinned_target(self) -> Self:
        """持久化结果必须精确绑定一个 artifact revision。."""
        if self.case_id != self.target.case_id:
            raise ValueError("评测结果的 case_id 与目标不一致")
        if self.target.artifact_revision is None:
            raise ValueError("评测结果必须绑定明确的 artifact revision")
        return self


class EvaluationBatchResult(FrozenModel):
    """一次离线批量评测的结果集合。."""

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    results: tuple[EvaluationCaseResult, ...] = Field(min_length=1)
