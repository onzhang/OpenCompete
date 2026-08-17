"""v3 官方 RACE 四维聚合与 result_kind 契约测试。."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opencompete.evaluation.competitive_v3_race import (
    OFFICIAL_RACE_DIMENSIONS,
    OPENCOMPETE_LOCAL_RACE,
    V2_FIVE_DIMENSION_KEYS,
    RaceCriterionRawScore,
    RaceDimensionRawScores,
    aggregate_race_v3,
    calculate_weighted_scores_official,
)
from opencompete.evaluation.official_drb import OFFICIAL_DRB_COMMIT


def _uniform_dimensions(
    *,
    target: float,
    reference: float,
) -> tuple[RaceDimensionRawScores, ...]:
    blocks = []
    for dim in OFFICIAL_RACE_DIMENSIONS:
        blocks.append(
            RaceDimensionRawScores(
                dimension=dim,
                criteria=(
                    RaceCriterionRawScore(
                        criterion_id=f"{dim}-a",
                        criterion_text=f"{dim} criterion A",
                        weight=0.6,
                        target_score=target,
                        reference_score=reference,
                    ),
                    RaceCriterionRawScore(
                        criterion_id=f"{dim}-b",
                        criterion_text=f"{dim} criterion B",
                        weight=0.4,
                        target_score=target,
                        reference_score=reference,
                    ),
                ),
            )
        )
    return tuple(blocks)


def test_aggregate_race_v3_four_dimensions_and_local_kind() -> None:
    weights = {
        "comprehensiveness": 0.3,
        "insight": 0.25,
        "instruction_following": 0.2,
        "readability": 0.25,
    }
    result = aggregate_race_v3(
        dimension_weights=weights,
        dimension_scores=_uniform_dimensions(target=8.0, reference=6.0),
    )

    assert result.result_kind == OPENCOMPETE_LOCAL_RACE
    assert result.result_kind != "official_race"
    assert set(item.dimension for item in result.dimensions) == set(
        OFFICIAL_RACE_DIMENSIONS
    )
    assert "insight" in {item.dimension for item in result.dimensions}
    assert "depth" not in {item.dimension for item in result.dimensions}
    assert not (set(item.dimension for item in result.dimensions) & V2_FIVE_DIMENSION_KEYS)
    assert result.official_method_base.commit == OFFICIAL_DRB_COMMIT
    # 8/(8+6)=0.5714...
    assert result.overall == pytest.approx(8.0 / (8.0 + 6.0))
    for item in result.dimensions:
        assert item.normalized == pytest.approx(8.0 / 14.0)


def test_aggregate_matches_official_calculator_fixture() -> None:
    """固定样例与官方 calculate_weighted_scores 核心算术逐位一致。."""
    weights = {
        "comprehensiveness": 0.4,
        "insight": 0.2,
        "instruction_following": 0.2,
        "readability": 0.2,
    }
    # 非均匀维内分，避免退化
    dimension_scores = (
        RaceDimensionRawScores(
            dimension="comprehensiveness",
            criteria=(
                RaceCriterionRawScore(
                    criterion_id="c1",
                    criterion_text="c1",
                    weight=0.7,
                    target_score=9.0,
                    reference_score=5.0,
                ),
                RaceCriterionRawScore(
                    criterion_id="c2",
                    criterion_text="c2",
                    weight=0.3,
                    target_score=7.0,
                    reference_score=6.0,
                ),
            ),
        ),
        RaceDimensionRawScores(
            dimension="insight",
            criteria=(
                RaceCriterionRawScore(
                    criterion_id="i1",
                    criterion_text="i1",
                    weight=1.0,
                    target_score=6.0,
                    reference_score=8.0,
                ),
            ),
        ),
        RaceDimensionRawScores(
            dimension="instruction_following",
            criteria=(
                RaceCriterionRawScore(
                    criterion_id="f1",
                    criterion_text="f1",
                    weight=0.5,
                    target_score=8.0,
                    reference_score=8.0,
                ),
                RaceCriterionRawScore(
                    criterion_id="f2",
                    criterion_text="f2",
                    weight=0.5,
                    target_score=4.0,
                    reference_score=2.0,
                ),
            ),
        ),
        RaceDimensionRawScores(
            dimension="readability",
            criteria=(
                RaceCriterionRawScore(
                    criterion_id="r1",
                    criterion_text="r1",
                    weight=1.0,
                    target_score=10.0,
                    reference_score=4.0,
                ),
            ),
        ),
    )

    result = aggregate_race_v3(
        dimension_weights=weights,
        dimension_scores=dimension_scores,
    )

    llm_output = {}
    criterions = {}
    for block in dimension_scores:
        llm_output[block.dimension] = [
            {
                "criterion": c.criterion_text,
                "article_1_score": c.target_score,
                "article_2_score": c.reference_score,
            }
            for c in block.criteria
        ]
        criterions[block.dimension] = [
            {"criterion": c.criterion_text, "weight": c.weight} for c in block.criteria
        ]
    official = calculate_weighted_scores_official(
        llm_output,
        {"dimension_weight": weights, "criterions": criterions, "id": "fix"},
    )
    target_total = float(official["target"]["total"])  # type: ignore[arg-type]
    reference_total = float(official["reference"]["total"])  # type: ignore[arg-type]
    expected_overall = target_total / (target_total + reference_total)

    assert result.target_weighted_total == pytest.approx(target_total)
    assert result.reference_weighted_total == pytest.approx(reference_total)
    assert result.overall == pytest.approx(expected_overall)

    # 与官方仓库实现交叉验证（若可导入）
    score_path = (
        Path(__file__).resolve().parents[3]
        / ".opencompete-tools"
        / "deep_research_bench"
        / "utils"
        / "score_calculator.py"
    )
    if score_path.is_file():
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "official_score_calculator",
            score_path,
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        vendored = module.calculate_weighted_scores(
            llm_output,
            {"dimension_weight": weights, "criterions": criterions, "id": "fix"},
            language="zh",
        )
        assert vendored["target"]["total"] == pytest.approx(result.target_weighted_total)
        assert vendored["reference"]["total"] == pytest.approx(
            result.reference_weighted_total
        )


def test_official_score_calculator_ignores_non_scalar_judge_scores() -> None:
    """Judge 返回的对象、布尔值和空值不能进入官方算术。."""
    score = calculate_weighted_scores_official(
        {
            "comprehensiveness": (
                {
                    "criterion": "coverage",
                    "article_1_score": 8,
                    "article_2_score": 6,
                },
                {
                    "criterion": "coverage",
                    "article_1_score": {"invalid": 1},
                    "article_2_score": 10,
                },
                {
                    "criterion": "coverage",
                    "article_1_score": True,
                    "article_2_score": 10,
                },
            )
        },
        {
            "dimension_weight": {"comprehensiveness": 1.0},
            "criterions": {
                "comprehensiveness": ({"criterion": "coverage", "weight": 1.0},)
            },
        },
    )

    assert score["target"]["total"] == pytest.approx(8.0)
    assert score["reference"]["total"] == pytest.approx(6.0)


def test_result_v3_model_rejects_official_race_and_five_dims() -> None:
    from opencompete.evaluation.competitive_models import (
        CompetitiveEvaluationStatus,
        CompetitiveFactEvaluation,
    )
    from opencompete.evaluation.competitive_v3_models import (
        CompetitiveEvaluationResultV3,
        CompetitiveV3Eligibility,
        CompetitiveV3RaceBlock,
    )
    from opencompete.evaluation.models import (
        CostMetrics,
        EvaluationTarget,
        HardGateEvaluation,
        MetricStatus,
    )

    race = CompetitiveV3RaceBlock(
        dimension_weights={d: 0.25 for d in OFFICIAL_RACE_DIMENSIONS},
        dimensions={d: 0.5 for d in OFFICIAL_RACE_DIMENSIONS},
        target_weighted_total=5.0,
        reference_weighted_total=5.0,
        overall=0.5,
    )
    result = CompetitiveEvaluationResultV3(
        dataset_id="opencompete-bench-v3",
        dataset_version="3.0.0",
        case_id="ocb-006-spotify-vs-apple-music",
        case_content_sha256="a" * 64,
        task_input_sha256="b" * 64,
        target=EvaluationTarget(case_id="ocb-006-spotify-vs-apple-music", run_id="run-1"),
        status=CompetitiveEvaluationStatus.COMPLETED,
        race=race,
        fact=CompetitiveFactEvaluation(status=MetricStatus.NOT_RUN, reason="n/a"),
        hard_gates=HardGateEvaluation(passed=True, violations=()),
        research_cost=CostMetrics(),
        evaluation_cost=CostMetrics(),
        eligibility=CompetitiveV3Eligibility(eligible_for_trend=True),
    )
    payload = json.loads(result.model_dump_json())
    assert payload["result_kind"] == "opencompete_local_race"
    assert "decision_relevance" not in json.dumps(payload["race"])
