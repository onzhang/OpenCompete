"""默认路径不写 v2 五维主分；历史 v2 可读；v3 数据集与服务形状。."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opencompete.evaluation.competitive_models import CompetitiveDimension
from opencompete.evaluation.competitive_v3_dataset import load_competitive_v3_dataset
from opencompete.evaluation.competitive_v3_judge import _parse_fact
from opencompete.evaluation.competitive_v3_models import CompetitiveEvaluationRequestV3
from opencompete.evaluation.competitive_v3_race import (
    OFFICIAL_RACE_DIMENSIONS,
    V2_FIVE_DIMENSION_KEYS,
)
from opencompete.evaluation.competitive_v3_service import (
    CompetitiveEvaluationServiceV3,
    legacy_v2_payload_has_five_dimension_primary,
    load_legacy_v2_evaluation_payload,
    raw_scores_from_fixed_map,
)
from opencompete.evaluation.models import CostMetrics, EvaluationTarget
from opencompete.models import RunManifest, RunStatus
from opencompete.persistence.repository import RunRepository

V3_DATASET = Path("benchmarks/opencompete/v3")


def test_v3_ocb006_loads_with_four_dimension_criteria() -> None:
    dataset = load_competitive_v3_dataset(V3_DATASET)
    assert dataset.dataset_id == "opencompete-bench-v3"
    case = dataset.get("ocb-006-spotify-vs-apple-music")
    assert set(case.criteria.dimensions) == set(OFFICIAL_RACE_DIMENSIONS)
    assert "readability" in case.criteria.dimensions
    assert "insight" in case.criteria.dimensions
    assert not (set(case.criteria.dimensions) & V2_FIVE_DIMENSION_KEYS)
    assert "显式 Unknown" not in case.prompt
    assert "冻结范围覆盖清单" not in case.prompt


def test_legacy_v2_evaluation_json_remains_readable(tmp_path: Path) -> None:
    quality_dimensions = [
        {
            "dimension": dim.value,
            "target_absolute": 8.0,
            "reference_absolute": 9.0,
            "target_minus_reference": -1.0,
            "relative_to_reference": 8 / 17,
            "order_gap": 0.0,
            "unstable": False,
        }
        for dim in CompetitiveDimension
    ]
    payload = {
        "evaluation_id": "eval-v2-historical",
        "dataset_id": "opencompete-bench-v2",
        "dataset_version": "2.2.0",
        "case_id": "ocb-006-spotify-vs-apple-music",
        "evaluation_contract_version": "opencompete-competitive-evaluation-v2.2.3",
        "case_content_sha256": "c" * 64,
        "target": {
            "case_id": "ocb-006-spotify-vs-apple-music",
            "run_id": "run-hist",
            "artifact_revision": 1,
        },
        "status": "completed",
        "eligible_for_comparison": True,
        "hard_gates": {"passed": True, "violations": []},
        "quality": {
            "status": "completed",
            "criteria": [],
            "dimensions": quality_dimensions,
            "target_overall": 8.0,
            "reference_overall": 9.0,
            "overall_relative_to_reference": 8 / 17,
            "overall_order_gap": 0.0,
            "stability": "stable",
        },
        "fact": {"status": "not_run", "reason": "historical fixture"},
        "cost": {
            "input_tokens": 1,
            "output_tokens": 1,
            "tavily_calls": 0,
            "cost_usd": 0.0,
        },
        "evaluated_at": "2026-07-17T00:00:00+00:00",
    }
    path = tmp_path / "hist.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_legacy_v2_evaluation_payload(path)
    assert legacy_v2_payload_has_five_dimension_primary(loaded)
    assert loaded["quality"]["dimensions"][0]["dimension"] == "decision_relevance"


class _OfflineV3Judge:
    def __init__(self) -> None:
        self._cost = CostMetrics(input_tokens=10, output_tokens=20, cost_usd=0.01)

    @property
    def evaluation_cost(self) -> CostMetrics:
        return self._cost

    async def evaluate_race_pair(self, case, bundle, *, target_first: bool):
        del target_first
        return raw_scores_from_fixed_map(case, target_score=8.0, reference_score=6.0)

    async def evaluate_fact(self, case, bundle):
        from opencompete.evaluation.competitive_models import (
            CompetitiveFactJudgeOutput,
            ReferenceClaimCoverage,
            ReferenceCoverageStatus,
        )

        claims = tuple(
            ReferenceClaimCoverage(
                reference_claim_id=claim.claim_id,
                status=ReferenceCoverageStatus.SUPPORTED,
                target_claim_ids=(),
                reason="offline supported",
            )
            for claim in case.reference_claims
        )
        return CompetitiveFactJudgeOutput(
            prompt_version="offline-v3",
            reference_claims=claims,
            target_claims=(),
            target_relationships=(),
        )


def test_v3_fact_parser_keeps_reference_and_target_status_types(valid_bundle) -> None:
    """FACT 的两套状态必须分别落入各自的枚举与结果字段。."""
    case = load_competitive_v3_dataset(V3_DATASET).get(
        "ocb-006-spotify-vs-apple-music"
    )
    bundle = valid_bundle.model_copy(update={"run_id": "run-v3-fact"})
    result = _parse_fact(
        case,
        bundle,
        {
            "reference_claims": [
                {
                    "reference_claim_id": case.reference_claims[0].claim_id,
                    "status": "partially_supported",
                    "reason": "部分覆盖",
                }
            ],
            "target_claims": [
                {
                    "claim_id": bundle.claims[0].claim_id,
                    "status": "unsupported",
                    "reason": "证据不足",
                }
            ],
            "target_relationships": [],
        },
        "test-v3-fact",
    )

    assert result.reference_claims[0].status.value == "partially_supported"
    assert result.target_claims[0].status.value == "unsupported"
    assert result.target_relationships[0].claim_id == bundle.claims[0].claim_id


@pytest.mark.asyncio
async def test_v3_service_writes_local_race_without_five_dims(
    tmp_path: Path,
    valid_bundle,
) -> None:
    dataset = load_competitive_v3_dataset(V3_DATASET)
    case = dataset.get("ocb-006-spotify-vs-apple-music")
    bundle = valid_bundle.model_copy(
        update={"run_id": "run-v3-1", "status": RunStatus.COMPLETED}
    )
    repository = RunRepository(tmp_path)
    repository.initialize(
        RunManifest(
            run_id=bundle.run_id,
            status=RunStatus.DRAFT,
            scope_revision=0,
            artifact_revision=0,
        )
    )
    repository.publish(
        bundle,
        RunManifest(
            run_id=bundle.run_id,
            status=bundle.status,
            scope_revision=bundle.scope.revision,
            artifact_revision=bundle.artifact_revision,
            config_snapshot={
                "entry_mode": "fixed_scope_after_freeze_scope",
                "benchmark_case_id": case.case_id,
                "task_input_sha256": case.normalized_task_sha256,
            },
            budget_summary=bundle.budget,
        ),
    )

    service = CompetitiveEvaluationServiceV3(
        repository=repository,
        judge=_OfflineV3Judge(),
    )
    batch = await service.evaluate(
        CompetitiveEvaluationRequestV3(
            dataset=V3_DATASET,
            targets=(EvaluationTarget(case_id=case.case_id, run_id=bundle.run_id),),
        )
    )
    result = batch.results[0]
    assert result.result_kind == "opencompete_local_race"
    assert result.race is not None
    assert set(result.race.dimensions) == set(OFFICIAL_RACE_DIMENSIONS)
    assert result.evaluation_cost.output_tokens == 20
    assert result.research_cost.input_tokens >= 0
    dumped = json.loads(result.model_dump_json())
    assert not (set(dumped["race"]["dimensions"]) & V2_FIVE_DIMENSION_KEYS)
    saved = list((tmp_path / "runs" / bundle.run_id / "evaluations").glob("*.json"))
    assert len(saved) == 1
    saved_payload = json.loads(saved[0].read_text(encoding="utf-8"))
    assert saved_payload["result_kind"] == "opencompete_local_race"
    assert not legacy_v2_payload_has_five_dimension_primary(saved_payload)
    # 默认 application 路径指向 v3
    app_src = Path("src/opencompete/application.py").read_text(encoding="utf-8")
    assert "CompetitiveEvaluationServiceV3" in app_src
