"""Bench v2.2 缺陷变体加载、候选构造和校准结果判定。."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opencompete.evaluation.competitive_judge import (
    CompetitiveCandidateJudge,
    build_reference_candidate,
)
from opencompete.evaluation.competitive_models import (
    COMPETITIVE_DIMENSION_WEIGHTS,
    CompetitiveBenchmarkCase,
    CompetitiveCalibrationDocument,
    CompetitiveCalibrationKind,
    CompetitiveCalibrationResult,
    CompetitiveCalibrationVariantResult,
    CompetitiveDimension,
    CompetitiveQualityEvaluation,
    StabilityStatus,
)
from opencompete.evaluation.competitive_service import aggregate_quality


def load_calibration_document(
    root: str | Path,
    case_id: str,
) -> CompetitiveCalibrationDocument:
    """读取一个固定案例的六种校准变体。."""
    path = Path(root) / "calibration" / f"{case_id}.json"
    try:
        document = CompetitiveCalibrationDocument.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取校准变体：{path}") from exc
    if document.case_id != case_id:
        raise ValueError(f"校准文件 case_id 不一致：{case_id}")
    return document


def calibration_report(
    case: CompetitiveBenchmarkCase,
    kind: CompetitiveCalibrationKind,
    defect_text: str,
    report_anchor: str | None = None,
) -> str:
    """用唯一锚点替换生成单缺陷报告，避免与原结论并存。."""
    if kind is CompetitiveCalibrationKind.STYLE_ONLY:
        return _style_only_transform(case.reference_report)
    if not report_anchor or case.reference_report.count(report_anchor) != 1:
        raise ValueError("校准报告锚点必须在 Reference 中唯一出现")
    return case.reference_report.replace(report_anchor, defect_text.strip(), 1)


def calibration_candidate(
    case: CompetitiveBenchmarkCase,
    kind: CompetitiveCalibrationKind,
    defect_text: str,
    report_anchor: str | None = None,
    replace_claim_id: str | None = None,
) -> dict[str, Any]:
    """同步物化报告、Claim 与支持卡，避免只改正文造成候选自相矛盾。."""
    candidate = build_reference_candidate(
        case,
        report_markdown=calibration_report(
            case,
            kind,
            defect_text,
            report_anchor,
        ),
    )
    if kind is CompetitiveCalibrationKind.STYLE_ONLY:
        return candidate
    matching = [
        claim for claim in candidate["claims"] if claim["claim_id"] == replace_claim_id
    ]
    if len(matching) != 1:
        raise ValueError("校准 replace_claim_id 必须唯一引用 Reference Claim")
    matching[0]["text"] = defect_text.strip()
    matching[0]["uncertainty"] = None
    if kind in {
        CompetitiveCalibrationKind.DECISION_SCOPE_VIOLATION,
        CompetitiveCalibrationKind.CAUSAL_OVERCLAIM,
        CompetitiveCalibrationKind.UNCONDITIONAL_RECOMMENDATION,
    }:
        matching[0]["important"] = False
        support_card = next(
            card
            for card in candidate["claim_support_cards"]
            if card["claim_id"] == replace_claim_id
        )
        support_card["supports"] = []
    return candidate


def calibration_passed(
    *,
    baseline: CompetitiveQualityEvaluation,
    variant: CompetitiveQualityEvaluation,
    expected_dimension: CompetitiveDimension | None,
) -> bool:
    """按冻结阈值判断单个变体是否通过语义校准。."""
    if (
        baseline.target_overall is None
        or variant.target_overall is None
        or baseline.stability is not StabilityStatus.STABLE
        or variant.stability is not StabilityStatus.STABLE
    ):
        return False
    baseline_dimensions = {
        item.dimension: item.target_absolute for item in baseline.dimensions
    }
    variant_dimensions = {
        item.dimension: item.target_absolute for item in variant.dimensions
    }
    if set(baseline_dimensions) != set(CompetitiveDimension) or set(
        variant_dimensions
    ) != set(CompetitiveDimension):
        return False
    if expected_dimension is None:
        dimension_deltas = [
            abs(baseline_dimensions[item] - variant_dimensions[item])
            for item in CompetitiveDimension
        ]
        critical_deltas = [
            abs(
                item.target_score
                - next(
                    other.target_score
                    for other in variant.criteria
                    if other.criterion_id == item.criterion_id
                )
            )
            for item in baseline.criteria
            if item.critical
        ]
        return (
            abs(baseline.target_overall - variant.target_overall) <= 0.20
            and max(dimension_deltas, default=0.0) <= 0.30
            and max(critical_deltas, default=0.0) <= 0.50
        )
    drops = {
        dimension: baseline_dimensions[dimension] - variant_dimensions[dimension]
        for dimension in CompetitiveDimension
    }
    other_drops = [
        drop for dimension, drop in drops.items() if dimension is not expected_dimension
    ]
    non_target_changes = [
        abs(variant_dimensions[dimension] - baseline_dimensions[dimension])
        for dimension in CompetitiveDimension
        if dimension is not expected_dimension
    ]
    return (
        drops[expected_dimension] >= 0.50
        and drops[expected_dimension] - max(other_drops, default=0.0) >= 0.20
        and max(non_target_changes, default=0.0) <= 0.10
        and baseline.target_overall - variant.target_overall
        >= COMPETITIVE_DIMENSION_WEIGHTS[expected_dimension] * 0.50
    )


async def evaluate_case_calibration(
    case: CompetitiveBenchmarkCase,
    document: CompetitiveCalibrationDocument,
    judge: CompetitiveCandidateJudge,
    *,
    dataset_id: str,
    dataset_version: str,
    repetitions: int = 2,
) -> CompetitiveCalibrationResult:
    """使用重复的匿名双顺序 Judge 评价六种校准变体。."""
    if repetitions < 2:
        raise ValueError("校准至少需要两次独立重复以检测跨调用漂移")
    reference = build_reference_candidate(case)
    baseline_repetitions: list[CompetitiveQualityEvaluation] = []
    for repetition in range(1, repetitions + 1):
        baseline_forward = await judge.evaluate_candidates(
            case,
            reference,
            reference,
            input_order_id=_opaque_order_id(case.case_id, "baseline", repetition, 1),
        )
        baseline_reverse = await judge.evaluate_candidates(
            case,
            reference,
            reference,
            input_order_id=_opaque_order_id(case.case_id, "baseline", repetition, 2),
        )
        baseline_repetitions.append(
            aggregate_quality(case, baseline_forward, baseline_reverse)
        )
    baseline = baseline_repetitions[0]
    results: list[CompetitiveCalibrationVariantResult] = []
    for definition in document.variants:
        variant = calibration_candidate(
            case,
            definition.kind,
            definition.defect_text,
            definition.report_anchor,
            definition.replace_claim_id,
        )
        quality_repetitions: list[CompetitiveQualityEvaluation] = []
        for repetition in range(1, repetitions + 1):
            forward = await judge.evaluate_candidates(
                case,
                variant,
                reference,
                input_order_id=_opaque_order_id(
                    case.case_id,
                    definition.variant_id,
                    repetition,
                    1,
                ),
            )
            reverse = await judge.evaluate_candidates(
                case,
                reference,
                variant,
                input_order_id=_opaque_order_id(
                    case.case_id,
                    definition.variant_id,
                    repetition,
                    2,
                ),
            )
            quality_repetitions.append(aggregate_quality(case, forward, reverse))
        quality = quality_repetitions[0]
        repeat_stable = _quality_repeats_stable(
            tuple(baseline_repetitions)
        ) and _quality_repeats_stable(tuple(quality_repetitions))
        passed = repeat_stable and all(
            calibration_passed(
                baseline=baseline_repeat,
                variant=variant_repeat,
                expected_dimension=definition.expected_primary_dimension,
            )
            for baseline_repeat, variant_repeat in zip(
                baseline_repetitions,
                quality_repetitions,
                strict=True,
            )
        )
        results.append(
            CompetitiveCalibrationVariantResult(
                variant_id=definition.variant_id,
                kind=definition.kind,
                expected_primary_dimension=definition.expected_primary_dimension,
                quality=quality,
                quality_repetitions=tuple(quality_repetitions),
                repeat_stable=repeat_stable,
                passed=passed,
            )
        )
    defect_results = [
        item
        for item in results
        if item.kind is not CompetitiveCalibrationKind.STYLE_ONLY
    ]
    style_result = next(
        item for item in results if item.kind is CompetitiveCalibrationKind.STYLE_ONLY
    )
    defect_pass_rate = sum(item.passed for item in defect_results) / len(defect_results)
    return CompetitiveCalibrationResult(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        case_id=case.case_id,
        judge_model=getattr(judge, "model_name", None),
        baseline=baseline,
        baseline_repetitions=tuple(baseline_repetitions),
        variants=tuple(results),
        defect_pass_rate=defect_pass_rate,
        style_only_passed=style_result.passed,
        passed=defect_pass_rate == 1.0 and style_result.passed,
    )


def _quality_repeats_stable(
    evaluations: tuple[CompetitiveQualityEvaluation, ...],
) -> bool:
    """拒绝顺序稳定但独立调用间随机漂移过大的 Judge。."""
    if any(
        item.stability is not StabilityStatus.STABLE or item.target_overall is None
        for item in evaluations
    ):
        return False
    overall: list[float] = []
    for item in evaluations:
        assert item.target_overall is not None
        overall.append(item.target_overall)
    if max(overall) - min(overall) > 0.25:
        return False
    by_dimension: dict[CompetitiveDimension, list[float]] = {
        dimension: [] for dimension in CompetitiveDimension
    }
    for evaluation in evaluations:
        for dimension_score in evaluation.dimensions:
            by_dimension[dimension_score.dimension].append(
                dimension_score.target_absolute
            )
    return all(
        len(values) == len(evaluations) and max(values) - min(values) <= 0.25
        for values in by_dimension.values()
    )


def _opaque_order_id(
    case_id: str,
    candidate_key: str,
    repetition: int,
    order: int,
) -> str:
    """生成不泄露缺陷类型、候选位置或 Reference 身份的顺序标识。."""
    digest = hashlib.sha256(
        f"{case_id}\0{candidate_key}\0{repetition}\0{order}".encode()
    ).hexdigest()
    return f"order_{digest[:20]}"


def save_calibration_result(
    result: CompetitiveCalibrationResult,
    *,
    data_root: str | Path,
) -> Path:
    """把校准结果原子保存到本地数据目录。."""
    directory = (
        Path(data_root) / "calibrations" / result.dataset_id / result.dataset_version
    )
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{result.case_id}.json"
    payload = (
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=directory,
        prefix=f".{result.case_id}.",
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


def _style_only_transform(markdown: str) -> str:
    """只改变 Markdown 表面格式，不增删语义句子。."""
    lines = []
    for line in markdown.splitlines():
        transformed = re.sub(r"^#{1,6}\s+", "", line)
        transformed = re.sub(r"^\s*[-*+]\s+", "• ", transformed)
        lines.append(transformed.rstrip())
    return "\n".join(lines).strip() + "\n"


__all__ = [
    "calibration_passed",
    "calibration_candidate",
    "calibration_report",
    "evaluate_case_calibration",
    "load_calibration_document",
    "save_calibration_result",
]
