"""Bench v3 评测服务：官方 RACE 主分 + 独立 FACT/门禁/双成本。."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from opencompete.evaluation.competitive_models import (
    CompetitiveEvaluationStatus,
    CompetitiveFactEvaluation,
    CompetitiveFactJudgeOutput,
    CompetitiveReviewStatus,
)
from opencompete.evaluation.competitive_v3_dataset import load_competitive_v3_dataset
from opencompete.evaluation.competitive_v3_models import (
    CompetitiveEvaluationBatchResultV3,
    CompetitiveEvaluationRequestV3,
    CompetitiveEvaluationResultV3,
    CompetitiveV3BenchmarkCase,
    CompetitiveV3Eligibility,
    CompetitiveV3RaceBlock,
    competitive_v3_case_content_sha256,
    race_block_from_aggregate,
)
from opencompete.evaluation.competitive_v3_race import (
    OFFICIAL_RACE_DIMENSIONS,
    OPENCOMPETE_LOCAL_RACE,
    V2_FIVE_DIMENSION_KEYS,
    RaceCriterionRawScore,
    RaceDimensionRawScores,
    aggregate_race_v3,
    build_stability_diagnostic,
)
from opencompete.evaluation.hard_gates import evaluate_hard_gates
from opencompete.evaluation.models import CostMetrics, EvaluationTarget, MetricStatus
from opencompete.evaluation.service import bundle_cost
from opencompete.models import RunBundle, RunStatus
from opencompete.persistence.repository import RunRepository
from opencompete.security import environment_secret_values, redact_text, sanitized_error


class CompetitiveV3RaceJudge(Protocol):
    """离线或真实 Judge：返回 Target-first 与可选 reverse 原始维分。."""

    async def evaluate_race_pair(
        self,
        case: CompetitiveV3BenchmarkCase,
        bundle: RunBundle,
        *,
        target_first: bool,
    ) -> tuple[RaceDimensionRawScores, ...]:
        """按官方四维返回 Criterion 原始分。."""

    async def evaluate_fact(
        self,
        case: CompetitiveV3BenchmarkCase,
        bundle: RunBundle,
    ) -> CompetitiveFactJudgeOutput:
        """FACT 输出。."""

    @property
    def evaluation_cost(self) -> CostMetrics:
        """本次评测（Judge）成本。."""


class CompetitiveEvaluationServiceV3:
    """默认竞品评测路径：只写 v3 结果。."""

    def __init__(
        self,
        *,
        repository: RunRepository,
        judge: CompetitiveV3RaceJudge | None = None,
    ) -> None:
        """绑定产物仓库和可选的 v3 Judge 实现。."""
        self._repository = repository
        self._judge = judge

    async def evaluate(
        self,
        request: CompetitiveEvaluationRequestV3,
    ) -> CompetitiveEvaluationBatchResultV3:
        """执行请求中的 v3 评测目标并将结果持久化。."""
        dataset = load_competitive_v3_dataset(
            request.dataset,
            allow_authored=request.allow_authored,
        )
        cases = {case.case_id: case for case in dataset.cases}
        results: list[CompetitiveEvaluationResultV3] = []
        for requested in request.targets:
            try:
                case = cases[requested.case_id]
            except KeyError as exc:
                raise ValueError(
                    f"v3 数据集中不存在评测案例：{requested.case_id}"
                ) from exc
            published = self._repository.load_verified(requested.run_id)
            _validate_benchmark_run_identity(
                case,
                published.manifest.config_snapshot,
            )
            bundle = published.bundle
            hard_gates = evaluate_hard_gates(
                case,
                bundle,
                target=requested,
                require_scope_cell_coverage=True,
            )
            race_block, fact, eval_cost, race_status = await self._judge_metrics(
                case,
                bundle,
            )
            status, eligibility = _result_status(
                case,
                bundle,
                hard_gates.passed,
                race_block,
                fact,
                race_status,
            )
            result = CompetitiveEvaluationResultV3(
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.version,
                case_id=case.case_id,
                case_content_sha256=competitive_v3_case_content_sha256(case),
                task_input_sha256=case.normalized_task_sha256,
                target=EvaluationTarget(
                    case_id=case.case_id,
                    run_id=bundle.run_id,
                    artifact_revision=bundle.artifact_revision,
                ),
                status=status,
                race=race_block,
                fact=fact,
                hard_gates=hard_gates,
                research_cost=bundle_cost(bundle),
                evaluation_cost=eval_cost,
                eligibility=eligibility,
            )
            _assert_no_v2_primary_keys(result)
            save_competitive_evaluation_v3(
                result,
                data_root=self._repository.data_root,
            )
            results.append(result)
        return CompetitiveEvaluationBatchResultV3(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            results=tuple(results),
        )

    async def _judge_metrics(
        self,
        case: CompetitiveV3BenchmarkCase,
        bundle: RunBundle,
    ) -> tuple[
        CompetitiveV3RaceBlock | None,
        CompetitiveFactEvaluation,
        CostMetrics,
        MetricStatus,
    ]:
        if self._judge is None:
            reason = "未配置 v3 RACE Judge。"
            return (
                None,
                CompetitiveFactEvaluation(status=MetricStatus.NOT_RUN, reason=reason),
                CostMetrics(),
                MetricStatus.NOT_RUN,
            )
        block: CompetitiveV3RaceBlock | None = None
        race_status = MetricStatus.FAILED
        try:
            forward_raw = await self._judge.evaluate_race_pair(
                case, bundle, target_first=True
            )
            reverse_raw = await self._judge.evaluate_race_pair(
                case, bundle, target_first=False
            )
            weights = dict(case.criteria.dimension_weights)
            forward = aggregate_race_v3(
                dimension_weights=weights,
                dimension_scores=forward_raw,
            )
            reverse = aggregate_race_v3(
                dimension_weights=weights,
                dimension_scores=reverse_raw,
            )
            stability = build_stability_diagnostic(forward=forward, reverse=reverse)
            block = race_block_from_aggregate(forward, stability=stability)
            race_status = MetricStatus.COMPLETED
        except Exception as exc:  # noqa: BLE001
            race_err = f"v3 RACE Judge 失败：{sanitized_error(exc).message}"
            return (
                None,
                CompetitiveFactEvaluation(status=MetricStatus.FAILED, reason=race_err),
                getattr(self._judge, "evaluation_cost", CostMetrics()),
                MetricStatus.FAILED,
            )
        try:
            fact_output = await self._judge.evaluate_fact(case, bundle)
            fact = _aggregate_fact_v3(case, bundle, fact_output)
        except Exception as exc:  # noqa: BLE001
            fact = CompetitiveFactEvaluation(
                status=MetricStatus.FAILED,
                reason=f"v3 FACT Judge 失败：{sanitized_error(exc).message}",
            )
        return (
            block,
            fact,
            self._judge.evaluation_cost,
            race_status,
        )


def raw_scores_from_fixed_map(
    case: CompetitiveV3BenchmarkCase,
    *,
    target_score: float,
    reference_score: float,
) -> tuple[RaceDimensionRawScores, ...]:
    """测试与离线 Judge 用的均匀原始分构造。."""
    blocks: list[RaceDimensionRawScores] = []
    for dim in OFFICIAL_RACE_DIMENSIONS:
        group = case.criteria.dimensions[dim]
        criteria = tuple(
            RaceCriterionRawScore(
                criterion_id=item.criterion_id,
                criterion_text=item.description,
                weight=item.weight,
                target_score=target_score,
                reference_score=reference_score,
            )
            for item in group.criteria
        )
        blocks.append(RaceDimensionRawScores(dimension=dim, criteria=criteria))
    return tuple(blocks)


def load_legacy_v2_evaluation_payload(path: str | Path) -> dict[str, object]:
    """读取历史 v2 评测 JSON（只读，不迁移）。."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("v2 评测结果必须是对象")
    return data


def legacy_v2_payload_has_five_dimension_primary(data: Mapping[str, object]) -> bool:
    """历史 v2 主分是否包含五维 quality.dimensions。."""
    quality = data.get("quality")
    if not isinstance(quality, Mapping):
        return False
    dimensions = quality.get("dimensions")
    if isinstance(dimensions, list):
        names = {
            item.get("dimension")
            for item in dimensions
            if isinstance(item, Mapping)
        }
        return bool(names & V2_FIVE_DIMENSION_KEYS)
    if isinstance(dimensions, Mapping):
        return bool(set(dimensions) & V2_FIVE_DIMENSION_KEYS)
    return False


def save_competitive_evaluation_v3(
    result: CompetitiveEvaluationResultV3,
    *,
    data_root: str | Path,
) -> Path:
    """原子写入一个 v3 评测结果，并拒绝秘密和旧五维主分。."""
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
    for key in V2_FIVE_DIMENSION_KEYS:
        # 仅检查 race 主分序列化，避免误伤自由文本
        race = result.race
        if race is not None and key in race.dimensions:
            raise ValueError(f"拒绝写入含 v2 五维主分的结果：{key}")
    if result.result_kind != OPENCOMPETE_LOCAL_RACE:
        raise ValueError("拒绝写入非 opencompete_local_race 结果")
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


def _assert_no_v2_primary_keys(result: CompetitiveEvaluationResultV3) -> None:
    if result.race is None:
        return
    bad = set(result.race.dimensions) & V2_FIVE_DIMENSION_KEYS
    if bad:
        raise ValueError(f"v3 结果包含非法五维键：{sorted(bad)}")


def _validate_benchmark_run_identity(
    case: CompetitiveV3BenchmarkCase,
    config_snapshot: object,
) -> None:
    expected = {
        "entry_mode": "fixed_scope_after_freeze_scope",
        "benchmark_case_id": case.case_id,
        "task_input_sha256": case.normalized_task_sha256,
    }
    actual = config_snapshot if isinstance(config_snapshot, Mapping) else {}
    invalid = [field for field, value in expected.items() if actual.get(field) != value]
    if invalid:
        joined = "、".join(invalid)
        raise ValueError(
            "正式竞品评测输入身份无法证明："
            f"Manifest config_snapshot 中的 {joined} 缺失或不匹配"
        )


def _aggregate_fact_v3(
    case: CompetitiveV3BenchmarkCase,
    bundle: RunBundle,
    output: CompetitiveFactJudgeOutput,
) -> CompetitiveFactEvaluation:
    """轻量 FACT：覆盖状态直接采用 Judge 输出并计算简单 eligible。."""
    # 若输出完整，尽量复用 v2 aggregate_fact 的字段形状
    from opencompete.evaluation.competitive_models import (
        ReferenceCoverageStatus,
        TargetClaimSupportStatus,
    )

    coverage = {item.reference_claim_id: item for item in output.reference_claims}
    required = {
        claim_id
        for criterion in case.criteria.fact
        for claim_id in criterion.reference_claim_ids
    }

    def score(status: ReferenceCoverageStatus) -> float:
        if status is ReferenceCoverageStatus.SUPPORTED:
            return 1.0
        if status is ReferenceCoverageStatus.PARTIALLY_SUPPORTED:
            return 0.5
        return 0.0

    relevant = [cid for cid in required if cid in coverage]
    critical_cov = None
    if relevant:
        critical_cov = sum(score(coverage[cid].status) for cid in relevant) / len(
            relevant
        )
    unsupported = sum(
        1
        for item in output.target_claims
        if item.status is TargetClaimSupportStatus.UNSUPPORTED
    )
    eligible = (
        critical_cov is not None
        and critical_cov >= 0.8
        and unsupported == 0
    )
    return CompetitiveFactEvaluation(
        status=MetricStatus.COMPLETED,
        claims=output.reference_claims,
        target_claims=output.target_claims,
        target_relationships=output.target_relationships,
        critical_claim_coverage=critical_cov,
        major_claim_coverage=critical_cov,
        target_claim_evidence_support_ratio=1.0 if not output.target_claims else None,
        target_relationship_support_ratio=(
            1.0 if not output.target_relationships else None
        ),
        critical_unknown_preservation_rate=1.0,
        critical_contradiction_count=0,
        unsupported_important_target_claim_count=unsupported,
        eligible=eligible,
    )


def _result_status(
    case: CompetitiveV3BenchmarkCase,
    bundle: RunBundle,
    hard_gates_passed: bool,
    race: CompetitiveV3RaceBlock | None,
    fact: CompetitiveFactEvaluation,
    race_status: MetricStatus,
) -> tuple[CompetitiveEvaluationStatus, CompetitiveV3Eligibility]:
    reasons: list[str] = []
    if case.review.status not in {
        CompetitiveReviewStatus.EXPERT_AI_DUAL_REVIEWED,
        CompetitiveReviewStatus.HUMAN_APPROVED,
    }:
        return (
            CompetitiveEvaluationStatus.REFERENCE_INVALID,
            CompetitiveV3Eligibility(eligible_for_trend=False, reasons=("reference_invalid",)),
        )
    if bundle.status is RunStatus.PARTIAL:
        reasons.append("partial_bundle")
        return (
            CompetitiveEvaluationStatus.PARTIAL_DIAGNOSTIC,
            CompetitiveV3Eligibility(eligible_for_trend=False, reasons=tuple(reasons)),
        )
    if not hard_gates_passed:
        reasons.append("hard_gates_failed")
        return (
            CompetitiveEvaluationStatus.INELIGIBLE,
            CompetitiveV3Eligibility(eligible_for_trend=False, reasons=tuple(reasons)),
        )
    if race_status is not MetricStatus.COMPLETED or fact.status is not MetricStatus.COMPLETED:
        return (
            CompetitiveEvaluationStatus.JUDGE_FAILED,
            CompetitiveV3Eligibility(eligible_for_trend=False, reasons=("judge_failed",)),
        )
    if not fact.eligible:
        reasons.append("fact_ineligible")
        return (
            CompetitiveEvaluationStatus.INELIGIBLE,
            CompetitiveV3Eligibility(eligible_for_trend=False, reasons=tuple(reasons)),
        )
    trend_ok = True
    if race and race.stability_diagnostic is not None:
        if not race.stability_diagnostic.eligible_for_trend:
            trend_ok = False
            reasons.append("order_unstable")
            return (
                CompetitiveEvaluationStatus.JUDGE_UNSTABLE,
                CompetitiveV3Eligibility(
                    eligible_for_trend=False,
                    reasons=tuple(reasons),
                ),
            )
    return (
        CompetitiveEvaluationStatus.COMPLETED,
        CompetitiveV3Eligibility(eligible_for_trend=trend_ok, reasons=tuple(reasons)),
    )
