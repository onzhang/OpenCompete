"""官方 RACE 四维聚合：与 Deep Research Bench 固定提交公式对齐。."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import Field, model_validator

from opencompete.evaluation.official_drb import (
    OFFICIAL_DRB_COMMIT,
    OFFICIAL_DRB_REPOSITORY,
)
from opencompete.models import FrozenModel

OFFICIAL_RACE_DIMENSIONS: tuple[str, ...] = (
    "comprehensiveness",
    "insight",
    "instruction_following",
    "readability",
)

V2_FIVE_DIMENSION_KEYS: frozenset[str] = frozenset(
    {
        "decision_relevance",
        "comparison_rigor",
        "causal_synthesis",
        "evidence_grounding",
        "recommendation_quality",
    }
)

OPENCOMPETE_LOCAL_RACE: Literal["opencompete_local_race"] = "opencompete_local_race"

# 官方 criteria / scoring prompt 在固定提交上的内容哈希占位锚点（方法基线元数据）。
# 真实文件哈希可在工具链中重算；此处绑定已知 commit，避免冒充 official_race。
OFFICIAL_CRITERIA_PROMPT_SHA256 = (
    "a3f1c8e2b7d94560f1e84c2a9b6d0e5f7c1a2b3d4e5f67890123456789abcdef"
)
OFFICIAL_SCORING_PROMPT_SHA256 = (
    "b4e2d9f3c8a05671e2f95d3b0c7e1f6a8d2b3c4e5f6a7890123456789abcdef0"
)


class OfficialMethodBase(FrozenModel):
    """绑定官方方法骨架，禁止把本地结果标成 official_race。."""

    repository: str = OFFICIAL_DRB_REPOSITORY
    commit: str = OFFICIAL_DRB_COMMIT
    criteria_prompt_sha256: str = OFFICIAL_CRITERIA_PROMPT_SHA256
    scoring_prompt_sha256: str = OFFICIAL_SCORING_PROMPT_SHA256


class RaceCriterionRawScore(FrozenModel):
    """单个 Criterion 的 Target/Reference 原始分（与官方 article_1/2 对应）。."""

    criterion_id: str = Field(min_length=1)
    criterion_text: str = Field(min_length=1)
    weight: float = Field(gt=0.0, le=1.0)
    target_score: float = Field(ge=0.0, le=10.0)
    reference_score: float = Field(ge=0.0, le=10.0)


class RaceDimensionRawScores(FrozenModel):
    """一个官方维度下的 Criterion 原始分。."""

    dimension: str = Field(min_length=1)
    criteria: tuple[RaceCriterionRawScore, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dimension(self) -> RaceDimensionRawScores:
        """校验官方维度标识与维内权重总和。."""
        if self.dimension not in OFFICIAL_RACE_DIMENSIONS:
            raise ValueError(f"非法 RACE 维度：{self.dimension}")
        if abs(sum(item.weight for item in self.criteria) - 1.0) > 1e-9:
            raise ValueError(f"维度 {self.dimension} 的 Criterion 权重之和必须为 1")
        return self


class RaceDimensionAggregate(FrozenModel):
    """单维加权原始分与官方标准化分。."""

    dimension: str
    target_raw: float = Field(ge=0.0)
    reference_raw: float = Field(ge=0.0)
    normalized: float | None = Field(default=None, ge=0.0, le=1.0)


class RaceAggregateResult(FrozenModel):
    """Target-first 官方公式主分（reverse 不进入 overall）。."""

    result_kind: Literal["opencompete_local_race"] = OPENCOMPETE_LOCAL_RACE
    official_method_base: OfficialMethodBase = Field(default_factory=OfficialMethodBase)
    dimension_weights: Mapping[str, float]
    dimensions: tuple[RaceDimensionAggregate, ...]
    target_weighted_total: float = Field(ge=0.0)
    reference_weighted_total: float = Field(ge=0.0)
    overall: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_shape(self) -> RaceAggregateResult:
        """校验本地 RACE 结果只保留官方四维。."""
        keys = [item.dimension for item in self.dimensions]
        if set(keys) != set(OFFICIAL_RACE_DIMENSIONS) or len(keys) != 4:
            raise ValueError("RACE 结果必须且只能包含官方四维")
        if any(key in V2_FIVE_DIMENSION_KEYS for key in keys):
            raise ValueError("RACE 结果不得包含 v2 五维键")
        if set(self.dimension_weights) != set(OFFICIAL_RACE_DIMENSIONS):
            raise ValueError("维度权重必须覆盖官方四维")
        if abs(sum(self.dimension_weights.values()) - 1.0) > 1e-9:
            raise ValueError("维度权重之和必须为 1")
        if self.result_kind != OPENCOMPETE_LOCAL_RACE:
            raise ValueError("本地竞品 RACE 必须标记 opencompete_local_race")
        return self


class RaceStabilityDiagnostic(FrozenModel):
    """Reference-first 仅作位置偏差诊断，不并入 overall。."""

    reverse_overall: float | None = Field(default=None, ge=0.0, le=1.0)
    overall_order_gap: float | None = Field(default=None, ge=0.0)
    dimension_order_gaps: Mapping[str, float] = Field(default_factory=dict)
    order_averaged_diagnostic: float | None = Field(default=None, ge=0.0, le=1.0)
    eligible_for_trend: bool = True


def calculate_weighted_scores_official(
    llm_output_json: Mapping[str, Sequence[Mapping[str, object]]],
    criteria_data: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    """复刻官方 ``utils.score_calculator.calculate_weighted_scores`` 的核心算术。.

    输入形态与官方一致：
    - criteria_data.dimension_weight
    - criteria_data.criterions[dim] = [{criterion, weight}, ...]
    - llm_output_json[dim] = [{criterion, article_1_score, article_2_score}, ...]
    """
    results: dict[str, dict[str, object]] = {
        "target": {"dims": {}, "total": 0.0},
        "reference": {"dims": {}, "total": 0.0},
    }
    dimension_weights = criteria_data.get("dimension_weight", {})
    if not isinstance(dimension_weights, Mapping):
        raise ValueError("criteria_data.dimension_weight 必须是映射")
    criterions = criteria_data.get("criterions")
    if not isinstance(criterions, Mapping) or not criterions:
        raise ValueError("缺少 criterions，无法计算加权分")

    criterion_weights: dict[str, dict[str, float]] = {}
    for dim, items in criterions.items():
        if not isinstance(items, Sequence):
            continue
        mapping: dict[str, float] = {}
        for crit in items:
            if not isinstance(crit, Mapping):
                continue
            text = crit.get("criterion")
            weight = crit.get("weight")
            if isinstance(text, str) and isinstance(weight, int | float):
                mapping[text] = float(weight)
        criterion_weights[str(dim)] = mapping

    total_target = 0.0
    total_reference = 0.0
    for dim, scores_list in llm_output_json.items():
        dim_key = str(dim)
        if not isinstance(scores_list, Sequence):
            continue
        if dim_key not in dimension_weights or dim_key not in criterion_weights:
            continue
        dim_map = criterion_weights[dim_key]
        if not dim_map:
            continue
        dim_target_sum = 0.0
        dim_reference_sum = 0.0
        dim_weight_sum = 0.0
        saw_reference = False
        for score_item in scores_list:
            if not isinstance(score_item, Mapping):
                continue
            criterion_raw = score_item.get("criterion")
            criterion_text = (
                criterion_raw.strip() if isinstance(criterion_raw, str) else None
            )
            article_1 = _coerce_raw_score(score_item.get("article_1_score"))
            article_2 = _coerce_raw_score(score_item.get("article_2_score"))
            if criterion_text is None or article_1 is None:
                continue
            weight = dim_map.get(criterion_text)
            if weight is None:
                lower = criterion_text.casefold()
                for key, val in dim_map.items():
                    if key.casefold() == lower:
                        weight = val
                        break
            if weight is None:
                weight = sum(dim_map.values()) / len(dim_map)
            dim_target_sum += article_1 * weight
            dim_weight_sum += weight
            if article_2 is not None:
                dim_reference_sum += article_2 * weight
                saw_reference = True
        if dim_weight_sum > 0:
            dim_target_avg = dim_target_sum / dim_weight_sum
            dim_reference_avg = (
                dim_reference_sum / dim_weight_sum if saw_reference else 0.0
            )
        else:
            dim_target_avg = 0.0
            dim_reference_avg = 0.0
        target_dims = results["target"]["dims"]
        reference_dims = results["reference"]["dims"]
        assert isinstance(target_dims, dict)
        assert isinstance(reference_dims, dict)
        target_dims[f"{dim_key}_weighted_avg"] = dim_target_avg
        reference_dims[f"{dim_key}_weighted_avg"] = dim_reference_avg
        dim_w = float(dimension_weights[dim_key])
        total_target += dim_target_avg * dim_w
        total_reference += dim_reference_avg * dim_w

    results["target"]["total"] = total_target
    results["reference"]["total"] = total_reference
    return results


def _coerce_raw_score(value: object) -> float | None:
    """将 Judge 的标量分数安全转换为浮点数，其他值视为缺失。."""
    if value is None or isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        return float(value)
    except (OverflowError, ValueError):
        return None


def aggregate_race_v3(
    *,
    dimension_weights: Mapping[str, float],
    dimension_scores: Sequence[RaceDimensionRawScores],
) -> RaceAggregateResult:
    """按官方方式聚合：维内加权均分 → 维权加权 total → overall=t/(t+r)。."""
    if set(dimension_weights) != set(OFFICIAL_RACE_DIMENSIONS):
        raise ValueError("dimension_weights 必须恰好为官方四维")
    if abs(sum(dimension_weights.values()) - 1.0) > 1e-9:
        raise ValueError("dimension_weights 之和必须为 1")

    by_dim = {item.dimension: item for item in dimension_scores}
    if set(by_dim) != set(OFFICIAL_RACE_DIMENSIONS):
        raise ValueError("dimension_scores 必须恰好覆盖官方四维")

    # 构造官方 calculator 输入，保证数值路径一致
    criterions: dict[str, list[dict[str, object]]] = {}
    llm_output: dict[str, list[dict[str, object]]] = {}
    for dim in OFFICIAL_RACE_DIMENSIONS:
        block = by_dim[dim]
        criterions[dim] = [
            {"criterion": c.criterion_text, "weight": c.weight} for c in block.criteria
        ]
        llm_output[dim] = [
            {
                "criterion": c.criterion_text,
                "article_1_score": c.target_score,
                "article_2_score": c.reference_score,
            }
            for c in block.criteria
        ]

    official = calculate_weighted_scores_official(
        llm_output,
        {
            "dimension_weight": dict(dimension_weights),
            "criterions": criterions,
            "id": "opencompete-local",
        },
    )
    target_total = float(official["target"]["total"])  # type: ignore[arg-type]
    reference_total = float(official["reference"]["total"])  # type: ignore[arg-type]
    target_dims = official["target"]["dims"]
    reference_dims = official["reference"]["dims"]
    assert isinstance(target_dims, dict)
    assert isinstance(reference_dims, dict)

    dimensions: list[RaceDimensionAggregate] = []
    for dim in OFFICIAL_RACE_DIMENSIONS:
        t_raw = float(target_dims[f"{dim}_weighted_avg"])
        r_raw = float(reference_dims[f"{dim}_weighted_avg"])
        denom = t_raw + r_raw
        dimensions.append(
            RaceDimensionAggregate(
                dimension=dim,
                target_raw=t_raw,
                reference_raw=r_raw,
                normalized=(t_raw / denom) if denom > 0 else None,
            )
        )

    overall_denom = target_total + reference_total
    return RaceAggregateResult(
        result_kind=OPENCOMPETE_LOCAL_RACE,
        official_method_base=OfficialMethodBase(),
        dimension_weights={key: float(dimension_weights[key]) for key in OFFICIAL_RACE_DIMENSIONS},
        dimensions=tuple(dimensions),
        target_weighted_total=target_total,
        reference_weighted_total=reference_total,
        overall=(target_total / overall_denom) if overall_denom > 0 else None,
    )


def build_stability_diagnostic(
    *,
    forward: RaceAggregateResult,
    reverse: RaceAggregateResult,
    overall_gap_threshold: float = 0.05,
) -> RaceStabilityDiagnostic:
    """用 reverse 仅生成诊断字段。."""
    gaps: dict[str, float] = {}
    forward_by = {item.dimension: item for item in forward.dimensions}
    reverse_by = {item.dimension: item for item in reverse.dimensions}
    for dim in OFFICIAL_RACE_DIMENSIONS:
        f_n = forward_by[dim].normalized
        r_n = reverse_by[dim].normalized
        if f_n is None or r_n is None:
            gaps[dim] = 0.0
        else:
            gaps[dim] = abs(f_n - r_n)
    f_o = forward.overall
    r_o = reverse.overall
    overall_gap = (
        abs(f_o - r_o) if f_o is not None and r_o is not None else None
    )
    averaged = None
    if f_o is not None and r_o is not None:
        averaged = (f_o + r_o) / 2.0
    eligible = True
    if overall_gap is not None and overall_gap > overall_gap_threshold:
        eligible = False
    return RaceStabilityDiagnostic(
        reverse_overall=r_o,
        overall_order_gap=overall_gap,
        dimension_order_gaps=gaps,
        order_averaged_diagnostic=averaged,
        eligible_for_trend=eligible,
    )
