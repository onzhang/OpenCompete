"""OpenCompete Bench 确定性硬门禁测试。"""

from __future__ import annotations

import pytest

from opencompete.evaluation.hard_gates import evaluate_hard_gates
from opencompete.evaluation.models import (
    CostMetrics,
    EvaluationTarget,
    HardGateEvaluation,
)
from opencompete.models import (
    AnalysisLevel,
    BudgetEstimate,
    BudgetSnapshot,
    ClaimStatus,
    Comparison,
    RunBundle,
    RunStatus,
)
from opencompete.reporting import render_validated_report


def _codes(result: HardGateEvaluation) -> set[str]:
    return {item.code for item in result.violations}


def test_valid_bundle_passes_all_hard_gates(benchmark_case, valid_bundle) -> None:
    target = EvaluationTarget(
        case_id=benchmark_case.case_id,
        run_id=valid_bundle.run_id,
        artifact_revision=valid_bundle.artifact_revision,
    )

    result = evaluate_hard_gates(benchmark_case, valid_bundle, target=target)

    assert result.passed is True


def test_competitive_scope_cell_coverage_cannot_be_omitted(
    benchmark_case,
    valid_bundle,
) -> None:
    """专用竞品 Bench 必须以冻结 Scope 单元为外部分母，防止少写少错。"""
    result = evaluate_hard_gates(
        benchmark_case,
        valid_bundle,
        require_scope_cell_coverage=True,
    )

    assert result.passed is False
    assert "scope_cell_without_claim_or_unknown" in _codes(result)


def test_scope_and_dangling_references_fail_closed(benchmark_case, valid_bundle) -> None:
    out_of_scope = valid_bundle.scope.model_copy(
        update={"entities": valid_bundle.scope.entities[:1]}
    )
    dangling_claim = valid_bundle.claims[0].model_copy(
        update={"evidence_ids": ("evd-missing",)}
    )
    bundle = valid_bundle.model_copy(
        update={"scope": out_of_scope, "claims": (dangling_claim,)}
    )

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert result.passed is False
    assert "scope_entities_mismatch" in _codes(result)
    assert "claim_evidence_missing" in _codes(result)


def test_unknown_claim_requires_a_reason_and_must_not_cite_evidence(
    benchmark_case, valid_bundle
) -> None:
    wrong_unknown = valid_bundle.claims[0].model_copy(
        update={"status": ClaimStatus.UNKNOWN, "uncertainty": None}
    )
    bundle = valid_bundle.model_copy(update={"claims": (wrong_unknown,)})

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert {"unknown_with_evidence", "unknown_without_reason"} <= _codes(result)


def test_budget_overrun_and_inconsistent_remaining_fail(benchmark_case, valid_bundle) -> None:
    invalid_budget = BudgetSnapshot(
        limit=BudgetEstimate(input_tokens=10, cost_usd=0.1),
        spent=BudgetEstimate(input_tokens=11, cost_usd=0.2),
        remaining=BudgetEstimate(input_tokens=10, cost_usd=0.1),
    )
    bundle = valid_bundle.model_copy(update={"budget": invalid_budget})

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert "budget_limit_exceeded" in _codes(result)
    assert "budget_snapshot_inconsistent" in _codes(result)


def test_terminal_reserved_budget_is_conservatively_counted_as_cost(
    benchmark_case,
    valid_bundle,
) -> None:
    budget = BudgetSnapshot(
        limit=BudgetEstimate(
            input_tokens=1_000,
            output_tokens=1_000,
            tavily_calls=10,
            cost_usd=1,
        ),
        spent=BudgetEstimate(
            input_tokens=100,
            output_tokens=50,
            tavily_calls=1,
            cost_usd=0.1,
        ),
        reserved=BudgetEstimate(
            input_tokens=25,
            output_tokens=10,
            tavily_calls=2,
            cost_usd=0.2,
        ),
        remaining=BudgetEstimate(
            input_tokens=875,
            output_tokens=940,
            tavily_calls=7,
            cost_usd=0.7,
        ),
    )
    bundle = valid_bundle.model_copy(update={"budget": budget})

    result = evaluate_hard_gates(benchmark_case, bundle)
    cost = CostMetrics.from_bundle(bundle)

    assert "budget_limit_exceeded" not in _codes(result)
    assert "budget_snapshot_inconsistent" not in _codes(result)
    assert cost.input_tokens == 125
    assert cost.output_tokens == 60
    assert cost.tavily_calls == 3
    assert cost.cost_usd == pytest.approx(0.3)


def test_target_revision_and_run_binding_are_hard_gates(
    benchmark_case, valid_bundle: RunBundle
) -> None:
    target = EvaluationTarget(
        case_id=benchmark_case.case_id,
        run_id="another-run",
        artifact_revision=valid_bundle.artifact_revision + 1,
    )

    result = evaluate_hard_gates(benchmark_case, valid_bundle, target=target)

    assert {"target_run_mismatch", "artifact_revision_mismatch"} <= _codes(result)


def test_empty_structured_artifacts_cannot_pass_as_a_complete_report(
    benchmark_case, valid_bundle
) -> None:
    bundle = valid_bundle.model_copy(
        update={
            "sources": (),
            "evidence": (),
            "claims": (),
            "comparisons": (),
            "report": valid_bundle.report.model_copy(
                update={"comparison_ids": (), "source_ids": ()}
            ),
        }
    )

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert {
        "target_sources_empty",
        "target_evidence_empty",
        "target_claims_empty",
        "target_comparisons_empty",
    } <= _codes(result)


def test_hard_gate_independently_rejects_completed_unknown_and_limitations(
    benchmark_case, valid_bundle
) -> None:
    unknown = valid_bundle.claims[0].model_copy(
        update={
            "status": ClaimStatus.UNKNOWN,
            "evidence_ids": (),
            "uncertainty": "证据不足",
        }
    )
    comparison = valid_bundle.comparisons[0].model_copy(
        update={"claim_ids": (unknown.claim_id,)}
    )
    limitations = ("存在未关闭缺口",)
    report = valid_bundle.report.model_copy(
        update={
            "markdown": render_validated_report(
                scope=valid_bundle.scope,
                claims=(unknown,),
                comparisons=(comparison,),
                sources=valid_bundle.sources,
                evidence=valid_bundle.evidence,
                limitations=limitations,
            ),
            "claim_ids": (unknown.claim_id,),
            "comparison_ids": (comparison.comparison_id,),
            "limitations": limitations,
        }
    )
    bundle = valid_bundle.model_copy(
        update={
            "claims": (unknown,),
            "comparisons": (comparison,),
            "report": report,
            "status": RunStatus.COMPLETED,
        }
    )

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert "completed_with_unknown" not in _codes(result)
    assert "completed_with_limitations" not in _codes(result)


def test_hard_gate_rejects_invalid_comparison_context_and_cross_level_text(
    benchmark_case, valid_bundle
) -> None:
    comparison = Comparison(
        entity_ids=valid_bundle.comparisons[0].entity_ids,
        level=AnalysisLevel.COMPANY_ORGANIZATION,
        dimension="范围外维度",
        claim_ids=valid_bundle.comparisons[0].claim_ids,
    )
    report = valid_bundle.report.model_copy(
        update={
            "markdown": render_validated_report(
                scope=valid_bundle.scope,
                claims=valid_bundle.claims,
                comparisons=(comparison,),
                sources=valid_bundle.sources,
                evidence=valid_bundle.evidence,
                limitations=(),
            ),
            "comparison_ids": (comparison.comparison_id,),
            "cross_level_conclusions": ("未经验证的跨层结论",),
        }
    )
    bundle = valid_bundle.model_copy(
        update={"comparisons": (comparison,), "report": report}
    )

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert "comparison_dimension_out_of_scope" in _codes(result)
    assert "comparison_claim_context_mismatch" in _codes(result)
    assert "report_cross_level_conclusions_unverified" in _codes(result)


def test_hard_gate_defensively_rejects_unsafe_source_url(
    benchmark_case, valid_bundle
) -> None:
    unsafe_source = valid_bundle.sources[0].model_copy(
        update={"url": "file:///etc/passwd"}
    )
    report = valid_bundle.report.model_copy(
        update={
            "markdown": render_validated_report(
                scope=valid_bundle.scope,
                claims=valid_bundle.claims,
                comparisons=valid_bundle.comparisons,
                sources=(unsafe_source,),
                evidence=valid_bundle.evidence,
                limitations=(),
            )
        }
    )
    bundle = valid_bundle.model_copy(
        update={"sources": (unsafe_source,), "report": report}
    )

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert "source_url_not_public_http" in _codes(result)


def test_hard_gate_allows_narrative_report_independent_of_structured_artifacts(
    benchmark_case, valid_bundle
) -> None:
    bundle = valid_bundle.model_copy(
        update={
            "report": valid_bundle.report.model_copy(
                update={"markdown": "# 被篡改的自由文本报告"}
            )
        }
    )

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert result.passed is True


def test_hard_gate_rejects_internal_identifier_in_user_report(
    benchmark_case, valid_bundle
) -> None:
    internal_id = valid_bundle.claims[0].claim_id
    bundle = valid_bundle.model_copy(
        update={
            "report": valid_bundle.report.model_copy(
                update={"markdown": f"# 用户报告\n\n内部引用：{internal_id}"}
            )
        }
    )

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert "report_internal_identifier_exposed" in _codes(result)


def test_structural_gate_does_not_claim_fact_entailment(
    benchmark_case, valid_bundle
) -> None:
    unrelated_claim = valid_bundle.claims[0].model_copy(
        update={"text": "甲公司已经拥有百分之九十九的全球市场份额"}
    )
    report = valid_bundle.report.model_copy(
        update={"markdown": "# 甲公司与乙公司分析\n\n这是独立的用户叙事报告。"}
    )
    bundle = valid_bundle.model_copy(
        update={"claims": (unrelated_claim,), "report": report}
    )

    result = evaluate_hard_gates(benchmark_case, bundle)

    assert result.passed is True
    assert unrelated_claim.text not in bundle.report.markdown
