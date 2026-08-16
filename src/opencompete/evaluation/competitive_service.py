"""读取正式 RunBundle 并执行 Bench v2 的本地竞品评测。."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from opencompete.evaluation.competitive_dataset import load_competitive_dataset
from opencompete.evaluation.competitive_judge import CompetitiveReportJudge
from opencompete.evaluation.competitive_models import (
    AggregatedCriterionScore,
    ClaimMateriality,
    CompetitiveBenchmarkCase,
    CompetitiveDimension,
    CompetitiveDimensionScore,
    CompetitiveEvaluationBatchResult,
    CompetitiveEvaluationRequest,
    CompetitiveEvaluationResult,
    CompetitiveEvaluationStatus,
    CompetitiveFactEvaluation,
    CompetitiveFactJudgeOutput,
    CompetitiveJudgeOutput,
    CompetitiveQualityEvaluation,
    CompetitiveReviewStatus,
    ReferenceClaimType,
    ReferenceCoverageStatus,
    StabilityStatus,
    TargetClaimSupportStatus,
    competitive_case_content_sha256,
)
from opencompete.evaluation.hard_gates import evaluate_hard_gates
from opencompete.evaluation.models import EvaluationTarget, MetricStatus
from opencompete.evaluation.service import bundle_cost
from opencompete.models import ClaimStatus, RunBundle, RunStatus
from opencompete.persistence.repository import RunRepository
from opencompete.security import environment_secret_values, redact_text, sanitized_error

CRITERION_ORDER_GAP_THRESHOLD = 2.0
DIMENSION_ORDER_GAP_THRESHOLD = 1.0
OVERALL_ORDER_GAP_THRESHOLD = 0.75
CRITICAL_FACT_COVERAGE_THRESHOLD = 0.80
MAJOR_FACT_COVERAGE_THRESHOLD = 0.60
TARGET_CLAIM_SUPPORT_THRESHOLD = 0.90
TARGET_RELATIONSHIP_SUPPORT_THRESHOLD = 0.90


class CompetitiveEvaluationService:
    """在不修改历史业务产物的前提下执行 Bench v2。."""

    def __init__(
        self,
        *,
        repository: RunRepository,
        judge: CompetitiveReportJudge | None = None,
    ) -> None:
        """绑定正式 Run Repository 和可选固定输入 Judge。."""
        self._repository = repository
        self._judge = judge

    async def evaluate(
        self,
        request: CompetitiveEvaluationRequest,
    ) -> CompetitiveEvaluationBatchResult:
        """逐项读取固定案例与已发布 Bundle 并持久化 v2 结果。."""
        dataset = load_competitive_dataset(
            request.dataset,
            allow_authored=request.allow_authored,
        )
        cases = {case.case_id: case for case in dataset.cases}
        results: list[CompetitiveEvaluationResult] = []
        for requested_target in request.targets:
            try:
                case = cases[requested_target.case_id]
            except KeyError as exc:
                raise ValueError(
                    f"v2 数据集中不存在评测案例：{requested_target.case_id}"
                ) from exc
            published = self._repository.load_verified(requested_target.run_id)
            _validate_benchmark_run_identity(
                case,
                published.manifest.config_snapshot,
            )
            bundle = published.bundle
            hard_gates = evaluate_hard_gates(
                case,
                bundle,
                target=requested_target,
                require_scope_cell_coverage=True,
            )
            quality, fact = await self._judge_metrics(case, bundle)
            target = EvaluationTarget(
                case_id=case.case_id,
                run_id=bundle.run_id,
                artifact_revision=bundle.artifact_revision,
            )
            status, eligible = _result_status(
                case,
                bundle,
                hard_gates.passed,
                quality,
                fact,
            )
            result = CompetitiveEvaluationResult(
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.version,
                case_id=case.case_id,
                case_content_sha256=competitive_case_content_sha256(case),
                judge_implementation=_judge_implementation(self._judge),
                judge_model=_judge_attribute(self._judge, "model_name"),
                judge_prompt_version=_judge_attribute(self._judge, "prompt_version"),
                target=target,
                status=status,
                eligible_for_comparison=eligible,
                hard_gates=hard_gates,
                quality=quality,
                fact=fact,
                cost=bundle_cost(bundle),
            )
            save_competitive_evaluation(result, data_root=self._repository.data_root)
            results.append(result)
        return CompetitiveEvaluationBatchResult(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            results=tuple(results),
        )

    async def _judge_metrics(
        self,
        case: CompetitiveBenchmarkCase,
        bundle: RunBundle,
    ) -> tuple[CompetitiveQualityEvaluation, CompetitiveFactEvaluation]:
        if self._judge is None:
            reason = "未配置竞品分析 Judge。"
            return (
                CompetitiveQualityEvaluation(
                    status=MetricStatus.NOT_RUN,
                    reason=reason,
                ),
                CompetitiveFactEvaluation(
                    status=MetricStatus.NOT_RUN,
                    reason=reason,
                ),
            )
        try:
            forward = await self._judge.evaluate_pair(
                case,
                bundle,
                target_first=True,
            )
            reverse = await self._judge.evaluate_pair(
                case,
                bundle,
                target_first=False,
            )
            fact_output = await self._judge.evaluate_fact(case, bundle)
            return (
                aggregate_quality(case, forward, reverse),
                aggregate_fact(case, bundle, fact_output),
            )
        except Exception as exc:
            reason = f"竞品分析 Judge 运行失败：{sanitized_error(exc).message}"
            return (
                CompetitiveQualityEvaluation(
                    status=MetricStatus.FAILED,
                    reason=reason,
                ),
                CompetitiveFactEvaluation(
                    status=MetricStatus.FAILED,
                    reason=reason,
                ),
            )


def _validate_benchmark_run_identity(
    case: CompetitiveBenchmarkCase,
    config_snapshot: object,
) -> None:
    """正式评分前证明目标 Run 来自同一固定 Benchmark 输入。."""
    expected = {
        "entry_mode": "fixed_scope_after_freeze_scope",
        "benchmark_case_id": case.case_id,
        "task_input_sha256": case.normalized_task_sha256,
    }
    actual = config_snapshot if isinstance(config_snapshot, Mapping) else {}
    invalid_fields = [
        field for field, value in expected.items() if actual.get(field) != value
    ]
    if invalid_fields:
        joined = "、".join(invalid_fields)
        raise ValueError(
            "正式竞品评测输入身份无法证明："
            f"Manifest config_snapshot 中的 {joined} 缺失或不匹配"
        )


def aggregate_quality(
    case: CompetitiveBenchmarkCase,
    forward: CompetitiveJudgeOutput,
    reverse: CompetitiveJudgeOutput,
) -> CompetitiveQualityEvaluation:
    """反向映射两次 Judge 输出并确定性计算五维与 Overall。."""
    forward_by_id = {item.criterion_id: item for item in forward.criteria}
    reverse_by_id = {item.criterion_id: item for item in reverse.criteria}
    aggregated: list[AggregatedCriterionScore] = []
    dimension_scores: list[CompetitiveDimensionScore] = []
    forward_target_dimensions: dict[CompetitiveDimension, float] = {}
    reverse_target_dimensions: dict[CompetitiveDimension, float] = {}
    forward_reference_dimensions: dict[CompetitiveDimension, float] = {}
    reverse_reference_dimensions: dict[CompetitiveDimension, float] = {}

    for dimension, group in case.criteria.dimensions.items():
        forward_target = 0.0
        reverse_target = 0.0
        forward_reference = 0.0
        reverse_reference = 0.0
        for criterion in group.criteria:
            item_forward = forward_by_id[criterion.criterion_id]
            item_reverse = reverse_by_id[criterion.criterion_id]
            target_a = item_forward.article_1_score
            target_b = item_reverse.article_2_score
            reference_a = item_forward.article_2_score
            reference_b = item_reverse.article_1_score
            target_gap = abs(target_a - target_b)
            reference_gap = abs(reference_a - reference_b)
            aggregated.append(
                AggregatedCriterionScore(
                    dimension=dimension,
                    criterion_id=criterion.criterion_id,
                    target_score=(target_a + target_b) / 2,
                    reference_score=(reference_a + reference_b) / 2,
                    target_order_gap=target_gap,
                    reference_order_gap=reference_gap,
                    unstable=max(target_gap, reference_gap)
                    > CRITERION_ORDER_GAP_THRESHOLD,
                    critical=criterion.critical,
                    forward=item_forward,
                    reverse=item_reverse,
                )
            )
            forward_target += target_a * criterion.weight
            reverse_target += target_b * criterion.weight
            forward_reference += reference_a * criterion.weight
            reverse_reference += reference_b * criterion.weight
        forward_target_dimensions[dimension] = forward_target
        reverse_target_dimensions[dimension] = reverse_target
        forward_reference_dimensions[dimension] = forward_reference
        reverse_reference_dimensions[dimension] = reverse_reference
        target_absolute = (forward_target + reverse_target) / 2
        reference_absolute = (forward_reference + reverse_reference) / 2
        denominator = target_absolute + reference_absolute
        order_gap = max(
            abs(forward_target - reverse_target),
            abs(forward_reference - reverse_reference),
        )
        dimension_scores.append(
            CompetitiveDimensionScore(
                dimension=dimension,
                target_absolute=target_absolute,
                reference_absolute=reference_absolute,
                target_minus_reference=target_absolute - reference_absolute,
                relative_to_reference=(
                    target_absolute / denominator if denominator > 0 else None
                ),
                order_gap=order_gap,
                unstable=order_gap > DIMENSION_ORDER_GAP_THRESHOLD,
            )
        )

    weights = case.criteria.dimension_weights
    target_forward_overall = sum(
        forward_target_dimensions[dimension] * weights[dimension]
        for dimension in CompetitiveDimension
    )
    target_reverse_overall = sum(
        reverse_target_dimensions[dimension] * weights[dimension]
        for dimension in CompetitiveDimension
    )
    reference_forward_overall = sum(
        forward_reference_dimensions[dimension] * weights[dimension]
        for dimension in CompetitiveDimension
    )
    reference_reverse_overall = sum(
        reverse_reference_dimensions[dimension] * weights[dimension]
        for dimension in CompetitiveDimension
    )
    overall_gap = max(
        abs(target_forward_overall - target_reverse_overall),
        abs(reference_forward_overall - reference_reverse_overall),
    )
    critical_unstable = any(
        item.critical
        and (
            item.unstable
            or item.forward.confidence < 0.6
            or item.reverse.confidence < 0.6
        )
        for item in aggregated
    )
    unstable_dimensions = sum(item.unstable for item in dimension_scores)
    unstable = (
        critical_unstable
        or unstable_dimensions >= 2
        or overall_gap > OVERALL_ORDER_GAP_THRESHOLD
    )
    target_overall = (target_forward_overall + target_reverse_overall) / 2
    reference_overall = (reference_forward_overall + reference_reverse_overall) / 2
    overall_denominator = target_overall + reference_overall
    return CompetitiveQualityEvaluation(
        status=MetricStatus.COMPLETED,
        criteria=tuple(aggregated),
        dimensions=tuple(dimension_scores),
        target_overall=target_overall,
        reference_overall=reference_overall,
        overall_relative_to_reference=(
            target_overall / overall_denominator if overall_denominator > 0 else None
        ),
        overall_order_gap=overall_gap,
        stability=(StabilityStatus.UNSTABLE if unstable else StabilityStatus.STABLE),
        forward_output=forward,
        reverse_output=reverse,
        reason=("双顺序 Judge 结果不稳定" if unstable else None),
    )


def aggregate_fact(
    case: CompetitiveBenchmarkCase,
    bundle: RunBundle,
    output: CompetitiveFactJudgeOutput,
) -> CompetitiveFactEvaluation:
    """由原子覆盖状态复算关键/主要 Claim 与目标证据支持率。."""
    coverage = {item.reference_claim_id: item for item in output.reference_claims}
    required_claim_ids = {
        claim_id
        for criterion in case.criteria.fact
        for claim_id in criterion.reference_claim_ids
    }
    critical_requirement_ids = {
        claim_id
        for criterion in case.criteria.fact
        if criterion.critical
        for claim_id in criterion.reference_claim_ids
    }

    def coverage_score(status: ReferenceCoverageStatus) -> float:
        if status is ReferenceCoverageStatus.SUPPORTED:
            return 1.0
        if status is ReferenceCoverageStatus.PARTIALLY_SUPPORTED:
            return 0.5
        return 0.0

    def materiality_ratio(materiality: ClaimMateriality) -> float | None:
        relevant = [
            claim
            for claim in case.reference_claims
            if claim.claim_id in required_claim_ids
            and claim.claim_type is not ReferenceClaimType.UNKNOWN
            and (
                (
                    materiality is ClaimMateriality.CRITICAL
                    and (
                        claim.materiality is ClaimMateriality.CRITICAL
                        or claim.claim_id in critical_requirement_ids
                    )
                )
                or (
                    materiality is ClaimMateriality.MAJOR
                    and claim.materiality is ClaimMateriality.MAJOR
                    and claim.claim_id not in critical_requirement_ids
                )
            )
            and claim.claim_id in coverage
        ]
        if not relevant:
            return None
        return sum(
            coverage_score(coverage[claim.claim_id].status) for claim in relevant
        ) / len(relevant)

    critical_unknowns = [
        claim
        for claim in case.reference_claims
        if claim.claim_type is ReferenceClaimType.UNKNOWN
        and (
            claim.materiality is ClaimMateriality.CRITICAL
            or claim.claim_id in critical_requirement_ids
        )
        and claim.claim_id in coverage
    ]
    unknown_preservation = (
        sum(
            coverage[claim.claim_id].status
            in {
                ReferenceCoverageStatus.HONEST_UNKNOWN,
                ReferenceCoverageStatus.SUPPORTED,
            }
            for claim in critical_unknowns
        )
        / len(critical_unknowns)
        if critical_unknowns
        else None
    )

    relationship_ratio = (
        sum(item.supported for item in output.target_relationships)
        / len(output.target_relationships)
        if output.target_relationships
        else None
    )
    bundle_claims = {claim.claim_id: claim for claim in bundle.claims}

    def target_score(item) -> float:
        if item.status is TargetClaimSupportStatus.SUPPORTED:
            return 1.0
        if item.status is TargetClaimSupportStatus.NONFACTUAL_ANALYSIS:
            return 0.0
        if item.status is TargetClaimSupportStatus.PARTIALLY_SUPPORTED:
            return 0.5
        if (
            item.status is TargetClaimSupportStatus.HONEST_UNKNOWN
            and bundle_claims[item.claim_id].status is ClaimStatus.UNKNOWN
        ):
            return 1.0
        return 0.0

    important_target = [
        item for item in output.target_claims if bundle_claims[item.claim_id].important
    ]
    target_claim_ratio = (
        sum(target_score(item) for item in important_target) / len(important_target)
        if important_target
        else None
    )
    contradictions = sum(
        item.status is ReferenceCoverageStatus.CONTRADICTED
        and (
            next(
                claim.materiality
                for claim in case.reference_claims
                if claim.claim_id == item.reference_claim_id
            )
            is ClaimMateriality.CRITICAL
            or item.reference_claim_id in critical_requirement_ids
        )
        for item in output.reference_claims
    )
    unsupported_important = sum(target_score(item) == 0.0 for item in important_target)
    relationship_required = any(
        claim.important and claim.status is not ClaimStatus.UNKNOWN
        for claim in bundle.claims
    )
    critical_coverage = materiality_ratio(ClaimMateriality.CRITICAL)
    major_coverage = materiality_ratio(ClaimMateriality.MAJOR)
    eligible = (
        (
            critical_coverage is None
            or critical_coverage >= CRITICAL_FACT_COVERAGE_THRESHOLD
        )
        and (major_coverage is None or major_coverage >= MAJOR_FACT_COVERAGE_THRESHOLD)
        and (
            target_claim_ratio is not None
            and target_claim_ratio >= TARGET_CLAIM_SUPPORT_THRESHOLD
        )
        and (
            (not relationship_required and relationship_ratio is None)
            or (
                relationship_ratio is not None
                and relationship_ratio >= TARGET_RELATIONSHIP_SUPPORT_THRESHOLD
            )
        )
        and (unknown_preservation is None or unknown_preservation == 1.0)
        and contradictions == 0
        and unsupported_important == 0
    )
    return CompetitiveFactEvaluation(
        status=MetricStatus.COMPLETED,
        claims=output.reference_claims,
        target_claims=output.target_claims,
        target_relationships=output.target_relationships,
        critical_claim_coverage=critical_coverage,
        major_claim_coverage=major_coverage,
        target_claim_evidence_support_ratio=target_claim_ratio,
        target_relationship_support_ratio=relationship_ratio,
        critical_unknown_preservation_rate=unknown_preservation,
        critical_contradiction_count=contradictions,
        unsupported_important_target_claim_count=unsupported_important,
        eligible=eligible,
        reason=(None if eligible else "FACT 或关键 Unknown 资格门禁未通过"),
    )


def _result_status(
    case: CompetitiveBenchmarkCase,
    bundle: RunBundle,
    hard_gates_passed: bool,
    quality: CompetitiveQualityEvaluation,
    fact: CompetitiveFactEvaluation,
) -> tuple[CompetitiveEvaluationStatus, bool]:
    if case.review.status is not CompetitiveReviewStatus.EXPERT_AI_DUAL_REVIEWED:
        return CompetitiveEvaluationStatus.REFERENCE_INVALID, False
    if bundle.status is RunStatus.PARTIAL:
        return CompetitiveEvaluationStatus.PARTIAL_DIAGNOSTIC, False
    if not hard_gates_passed:
        return CompetitiveEvaluationStatus.INELIGIBLE, False
    if (
        quality.status is not MetricStatus.COMPLETED
        or fact.status is not MetricStatus.COMPLETED
    ):
        return CompetitiveEvaluationStatus.JUDGE_FAILED, False
    if not fact.eligible:
        return CompetitiveEvaluationStatus.INELIGIBLE, False
    if quality.stability is StabilityStatus.UNSTABLE:
        return CompetitiveEvaluationStatus.JUDGE_UNSTABLE, False
    return CompetitiveEvaluationStatus.COMPLETED, True


def save_competitive_evaluation(
    result: CompetitiveEvaluationResult,
    *,
    data_root: str | Path,
) -> Path:
    """在原任务 evaluations 目录原子保存 v2 结果。."""
    run_dir = Path(data_root) / "runs" / result.target.run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"评测目标任务目录不存在：{result.target.run_id}")
    evaluation_dir = run_dir / "evaluations"
    destination = evaluation_dir / f"{result.evaluation_id}.json"
    if destination.exists():
        raise FileExistsError(f"评测结果已存在，拒绝覆盖：{result.evaluation_id}")
    payload = (
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    serialized = payload.decode("utf-8")
    if redact_text(serialized, environment_secret_values()) != serialized:
        raise ValueError("评测结果疑似包含秘密或认证信息，拒绝持久化")
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=evaluation_dir,
        prefix=f".{result.evaluation_id}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _judge_implementation(judge: CompetitiveReportJudge | None) -> str | None:
    if judge is None:
        return None
    judge_type = type(judge)
    return f"{judge_type.__module__}.{judge_type.__qualname__}"


def _judge_attribute(judge: CompetitiveReportJudge | None, name: str) -> str | None:
    if judge is None:
        return None
    value = getattr(judge, name, None)
    return value if isinstance(value, str) else None
