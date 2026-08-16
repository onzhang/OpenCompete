"""Bench v2 数据契约、双顺序汇总与 FACT 指标测试。."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from opencompete.evaluation.competitive_calibration import (
    calibration_candidate,
    calibration_passed,
    calibration_report,
    evaluate_case_calibration,
    load_calibration_document,
)
from opencompete.evaluation.competitive_dataset import load_competitive_dataset
from opencompete.evaluation.competitive_judge import (
    CompetitiveReportJudge,
    LangChainCompetitiveReportJudge,
    _quality_payload,
    _response_text,
    _validate_fact_output,
    _validate_quality_output,
    build_reference_candidate,
)
from opencompete.evaluation.competitive_models import (
    CompetitiveCalibrationKind,
    CompetitiveCriterionJudgeScore,
    CompetitiveEvaluationRequest,
    CompetitiveFactJudgeOutput,
    CompetitiveJudgeOutput,
    CompetitiveReviewStatus,
    JudgeLocator,
    ReferenceClaimCoverage,
    ReferenceClaimType,
    ReferenceCoverageStatus,
    StabilityStatus,
    TargetClaimSupportAssessment,
    TargetClaimSupportStatus,
)
from opencompete.evaluation.competitive_service import (
    CompetitiveEvaluationService,
    aggregate_fact,
    aggregate_quality,
)
from opencompete.evaluation.models import EvaluationTarget, FactSupport
from opencompete.models import Claim, ClaimStatus, Comparison, RunManifest, RunStatus
from opencompete.persistence.repository import RunRepository
from opencompete.reporting import render_validated_report

DATASET = Path("benchmarks/opencompete/v2")


def test_v2_dataset_has_eight_dual_reviewed_cases() -> None:
    """正式加载默认拒绝作者草稿，并要求八案均完成独立复核。."""
    dataset = load_competitive_dataset(DATASET)

    assert dataset.dataset_id == "opencompete-bench-v2"
    assert len(dataset.cases) == 8
    assert all(
        case.review.status is CompetitiveReviewStatus.EXPERT_AI_DUAL_REVIEWED
        for case in dataset.cases
    )
    assert all(case.review.unresolved_material_issues == 0 for case in dataset.cases)


def test_v2_has_six_calibration_variants_for_every_case() -> None:
    """每案必须冻结五种单缺陷和一种纯文风变体。"""
    dataset = load_competitive_dataset(DATASET)

    for case in dataset.cases:
        document = load_calibration_document(DATASET, case.case_id)
        baseline_candidate = build_reference_candidate(case)
        assert len(document.variants) == 6
        assert {item.kind for item in document.variants} == set(
            CompetitiveCalibrationKind
        )
        for item in document.variants:
            report = calibration_report(
                case,
                item.kind,
                item.defect_text,
                item.report_anchor,
            )
            candidate = calibration_candidate(
                case,
                item.kind,
                item.defect_text,
                item.report_anchor,
                item.replace_claim_id,
            )
            if item.kind is CompetitiveCalibrationKind.STYLE_ONLY:
                assert len(candidate["claims"]) == len(case.reference_claims)
                assert candidate["claims"] == baseline_candidate["claims"]
                assert (
                    candidate["claim_support_cards"]
                    == baseline_candidate["claim_support_cards"]
                )
            else:
                assert item.defect_text in report
                assert item.report_anchor not in report
                assert any(
                    claim["text"] == item.defect_text for claim in candidate["claims"]
                )
                assert len(candidate["claims"]) == len(case.reference_claims)
                assert candidate["sources"] == baseline_candidate["sources"]
                assert all(
                    claim
                    == next(
                        original
                        for original in baseline_candidate["claims"]
                        if original["claim_id"] == claim["claim_id"]
                    )
                    for claim in candidate["claims"]
                    if claim["claim_id"] != item.replace_claim_id
                )


@pytest.mark.asyncio
async def test_calibration_uses_same_dual_order_aggregation() -> None:
    """缺陷校准必须复用正式 Criterion 输出和双顺序确定性聚合。"""
    case = load_competitive_dataset(DATASET).cases[0]
    document = load_calibration_document(DATASET, case.case_id)

    class OfflineCalibrationJudge:
        async def evaluate_candidates(
            self,
            current_case,
            article_1,
            article_2,
            *,
            input_order_id,
        ):
            assert input_order_id.startswith("order_")
            assert not any(
                marker in input_order_id
                for marker in (
                    "baseline",
                    "decision",
                    "comparison",
                    "causal",
                    "evidence",
                    "recommendation",
                    "style",
                )
            )
            assert not input_order_id.endswith(("_a", "_b"))

            def defect_dimension(article):
                report = article["report_markdown"]
                for definition in document.variants:
                    if definition.defect_text and definition.defect_text in report:
                        return definition.expected_primary_dimension
                return None

            first_defect = defect_dimension(article_1)
            second_defect = defect_dimension(article_2)
            criteria = []
            for dimension, group in current_case.criteria.dimensions.items():
                for criterion in group.criteria:
                    criteria.append(
                        CompetitiveCriterionJudgeScore(
                            dimension=dimension,
                            criterion_id=criterion.criterion_id,
                            article_1_score=(8.0 if first_defect is dimension else 9.0),
                            article_2_score=(
                                8.0 if second_defect is dimension else 9.0
                            ),
                            article_1_reason="离线校准",
                            article_2_reason="离线校准",
                            confidence=1.0,
                        )
                    )
            return CompetitiveJudgeOutput(
                prompt_version="opencompete-competitive-judge-v2.2.3",
                input_order_id=input_order_id,
                criteria=tuple(criteria),
            )

    result = await evaluate_case_calibration(
        case,
        document,
        OfflineCalibrationJudge(),
        dataset_id="opencompete-bench-v2",
        dataset_version="test",
    )

    assert result.defect_pass_rate == 1.0
    assert result.style_only_passed is True
    assert result.passed is True


def test_calibration_rejects_collateral_dimension_drift() -> None:
    """目标维下降时，其他维度明显漂移也不能冒充单维缺陷命中。"""
    case = load_competitive_dataset(DATASET).cases[0]
    baseline = aggregate_quality(
        case,
        _judge_output(case, target_score=9.0, reference_score=9.0, reverse=False),
        _judge_output(case, target_score=9.0, reference_score=9.0, reverse=True),
    )
    expected = next(iter(case.criteria.dimensions))
    dimensions = tuple(
        item.model_copy(
            update={"target_absolute": (8.4 if item.dimension is expected else 8.8)}
        )
        for item in baseline.dimensions
    )
    variant = baseline.model_copy(
        update={"dimensions": dimensions, "target_overall": 8.7}
    )

    assert (
        calibration_passed(
            baseline=baseline,
            variant=variant,
            expected_dimension=expected,
        )
        is False
    )


@pytest.mark.asyncio
async def test_calibration_rejects_cross_call_random_drift() -> None:
    """双顺序各自稳定但独立重复漂移过大时，校准仍必须失败。"""
    case = load_competitive_dataset(DATASET).cases[0]
    document = load_calibration_document(DATASET, case.case_id)

    class DriftingJudge:
        def __init__(self) -> None:
            self.calls = 0

        async def evaluate_candidates(
            self,
            current_case,
            article_1,
            article_2,
            *,
            input_order_id,
        ):
            del article_1, article_2
            self.calls += 1
            pair_index = (self.calls - 1) // 2
            drift = 0.4 if pair_index % 2 == 1 else 0.0
            return CompetitiveJudgeOutput(
                prompt_version="opencompete-competitive-judge-v2.2.3",
                input_order_id=input_order_id,
                criteria=tuple(
                    CompetitiveCriterionJudgeScore(
                        dimension=dimension,
                        criterion_id=criterion.criterion_id,
                        article_1_score=9.0 - drift,
                        article_2_score=9.0 - drift,
                        article_1_reason="离线漂移",
                        article_2_reason="离线漂移",
                        confidence=1.0,
                    )
                    for dimension, group in current_case.criteria.dimensions.items()
                    for criterion in group.criteria
                ),
            )

    result = await evaluate_case_calibration(
        case,
        document,
        DriftingJudge(),
        dataset_id="opencompete-bench-v2",
        dataset_version="test",
    )

    assert result.passed is False
    assert all(item.repeat_stable is False for item in result.variants)


def test_dual_order_scores_are_averaged_and_stable() -> None:
    """文章顺序互换但分数一致时，五维与 Overall 可确定性复算。."""
    case = load_competitive_dataset(DATASET).cases[0]
    forward = _judge_output(case, target_score=8.0, reference_score=9.0, reverse=False)
    reverse = _judge_output(case, target_score=8.0, reference_score=9.0, reverse=True)

    result = aggregate_quality(case, forward, reverse)

    assert result.stability is StabilityStatus.STABLE
    assert result.target_overall == 8.0
    assert result.reference_overall == 9.0
    assert result.overall_relative_to_reference == pytest.approx(8 / 17)
    assert result.overall_order_gap == 0.0
    assert all(item.target_absolute == 8.0 for item in result.dimensions)


def test_critical_criterion_order_gap_marks_judge_unstable() -> None:
    """任一关键 Criterion 的明显位置偏差会使本次 Judge 失去排名资格。."""
    case = load_competitive_dataset(DATASET).cases[0]
    forward = _judge_output(case, target_score=8.0, reference_score=9.0, reverse=False)
    reverse = _judge_output(case, target_score=8.0, reference_score=9.0, reverse=True)
    critical_id = next(
        criterion.criterion_id
        for group in case.criteria.dimensions.values()
        for criterion in group.criteria
        if criterion.critical
    )
    reverse_items = tuple(
        item.model_copy(update={"article_2_score": 4.0})
        if item.criterion_id == critical_id
        else item
        for item in reverse.criteria
    )
    reverse = reverse.model_copy(update={"criteria": reverse_items})

    result = aggregate_quality(case, forward, reverse)

    assert result.stability is StabilityStatus.UNSTABLE
    assert any(
        item.criterion_id == critical_id and item.unstable for item in result.criteria
    )


def test_reference_fact_coverage_is_recomputed_from_atomic_statuses(
    valid_bundle,
) -> None:
    """关键与主要事实覆盖率必须来自逐 Claim 状态，而非 Judge 自报总分。."""
    case = load_competitive_dataset(DATASET).cases[0]
    required_claim_ids = {
        claim_id
        for criterion in case.criteria.fact
        for claim_id in criterion.reference_claim_ids
    }
    fact_claims = tuple(
        claim for claim in case.reference_claims if claim.claim_id in required_claim_ids
    )
    output = CompetitiveFactJudgeOutput(
        prompt_version="opencompete-competitive-judge-v2.2.3",
        reference_claims=tuple(
            ReferenceClaimCoverage(
                reference_claim_id=claim.claim_id,
                status=ReferenceCoverageStatus.SUPPORTED,
                reason="测试固定为支持",
            )
            for claim in fact_claims
        ),
        target_claims=tuple(
            TargetClaimSupportAssessment(
                claim_id=claim.claim_id,
                status=(
                    TargetClaimSupportStatus.HONEST_UNKNOWN
                    if claim.status.value == "unknown"
                    else TargetClaimSupportStatus.SUPPORTED
                ),
                evidence_ids=claim.evidence_ids,
                reason="测试固定为支持",
            )
            for claim in valid_bundle.claims
        ),
        target_relationships=(),
    )

    result = aggregate_fact(case, valid_bundle, output)

    assert result.critical_claim_coverage == 1.0
    if result.major_claim_coverage is not None:
        assert result.major_claim_coverage == 1.0
    assert result.target_claim_evidence_support_ratio == 1.0


def test_partially_supported_reference_claim_counts_as_half(valid_bundle) -> None:
    """FACT 的部分支持只能贡献 0.5，不能与完全支持同分。"""
    case = load_competitive_dataset(DATASET).cases[0]
    required_claim_ids = {
        claim_id
        for criterion in case.criteria.fact
        for claim_id in criterion.reference_claim_ids
    }
    fact_claims = tuple(
        claim
        for claim in case.reference_claims
        if claim.claim_id in required_claim_ids
        and claim.claim_type is not ReferenceClaimType.UNKNOWN
    )
    output = CompetitiveFactJudgeOutput(
        prompt_version="opencompete-competitive-judge-v2.2.3",
        reference_claims=tuple(
            ReferenceClaimCoverage(
                reference_claim_id=claim.claim_id,
                status=ReferenceCoverageStatus.PARTIALLY_SUPPORTED,
                reason="只覆盖了一半口径",
            )
            for claim in fact_claims
        ),
        target_claims=tuple(
            TargetClaimSupportAssessment(
                claim_id=claim.claim_id,
                status=TargetClaimSupportStatus.SUPPORTED,
                evidence_ids=claim.evidence_ids,
                reason="固定支持",
            )
            for claim in valid_bundle.claims
        ),
        target_relationships=(),
    )

    result = aggregate_fact(case, valid_bundle, output)

    assert result.critical_claim_coverage == 0.5
    assert result.eligible is False


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"criteria": []}', '{"criteria": []}'),
        ('```json\n{"criteria": []}\n```', '{"criteria": []}'),
    ],
)
def test_response_text_accepts_plain_or_fenced_json(
    content: str,
    expected: str,
) -> None:
    """普通模型返回的纯 JSON 和 JSON 代码块都能进入本地严格校验。"""
    assert _response_text(AIMessage(content=content)) == expected


def test_response_text_rejects_non_json() -> None:
    """Judge 没有返回 JSON 对象时必须失败，不能静默生成缺省分。"""
    with pytest.raises(ValueError, match="没有返回 JSON"):
        _response_text(AIMessage(content="无法完成评测"))


@pytest.mark.asyncio
async def test_candidate_judge_retries_invalid_json_once() -> None:
    """普通 JSON Judge 应只重试无效输出，不重放整个研究 Graph。"""
    case = load_competitive_dataset(DATASET).cases[0]
    valid = _judge_output(
        case,
        target_score=9.0,
        reference_score=9.0,
        reverse=False,
    ).model_copy(update={"input_order_id": "calibration_retry"})

    class StaticModel:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="未按要求返回")
            assert "上一次 质量 输出未通过" in messages[-1].content
            assert "locator.kind 只能是" in messages[-1].content
            return AIMessage(content=valid.model_dump_json())

    judge = LangChainCompetitiveReportJudge(
        model="openai:test",
        max_output_attempts=2,
    )
    model = StaticModel()
    judge._model = model
    candidate = build_reference_candidate(case)

    result = await judge.evaluate_candidates(
        case,
        candidate,
        candidate,
        input_order_id="calibration_retry",
    )

    assert result.input_order_id == "calibration_retry"
    assert model.calls == 2


@pytest.mark.asyncio
async def test_candidate_judge_restores_opaque_locators() -> None:
    """模型只能看到 opaque ID，返回结果必须在本地恢复为真实定位。"""
    case = load_competitive_dataset(DATASET).cases[0]
    candidate = build_reference_candidate(case)
    valid = _judge_output(
        case,
        target_score=9.0,
        reference_score=9.0,
        reverse=False,
    ).model_copy(update={"input_order_id": "opaque_round_trip"})
    first = valid.criteria[0].model_copy(
        update={
            "article_1_locators": (JudgeLocator(kind="claim", identifier="c001"),),
            "article_2_locators": (JudgeLocator(kind="evidence", identifier="e001"),),
        }
    )
    valid = valid.model_copy(update={"criteria": (first, *valid.criteria[1:])})

    class StaticModel:
        async def ainvoke(self, messages):
            serialized = str(messages[-1].content)
            assert candidate["claims"][0]["claim_id"] not in serialized
            return AIMessage(content=valid.model_dump_json())

    judge = LangChainCompetitiveReportJudge(model="openai:test")
    judge._model = StaticModel()
    result = await judge.evaluate_candidates(
        case,
        candidate,
        candidate,
        input_order_id="opaque_round_trip",
    )

    restored = result.criteria[0]
    assert (
        restored.article_1_locators[0].identifier == candidate["claims"][0]["claim_id"]
    )
    assert restored.article_2_locators[0].identifier.startswith(
        candidate["claims"][0]["claim_id"]
    )


@pytest.mark.asyncio
async def test_candidate_judge_rejects_wrong_locator_kind() -> None:
    """相同 opaque 文本使用错误定位类型时也必须拒绝。"""
    case = load_competitive_dataset(DATASET).cases[0]
    candidate = build_reference_candidate(case)
    invalid = _judge_output(
        case,
        target_score=9.0,
        reference_score=9.0,
        reverse=False,
    ).model_copy(update={"input_order_id": "wrong_kind"})
    first = invalid.criteria[0].model_copy(
        update={"article_1_locators": (JudgeLocator(kind="source", identifier="c001"),)}
    )
    invalid = invalid.model_copy(update={"criteria": (first, *invalid.criteria[1:])})

    class StaticModel:
        async def ainvoke(self, messages):
            del messages
            return AIMessage(content=invalid.model_dump_json())

    judge = LangChainCompetitiveReportJudge(
        model="openai:test",
        max_output_attempts=1,
    )
    judge._model = StaticModel()
    with pytest.raises(ValueError, match="多次返回无效质量 JSON"):
        await judge.evaluate_candidates(
            case,
            candidate,
            candidate,
            input_order_id="wrong_kind",
        )


def test_quality_payload_is_anonymous_and_symmetric(valid_bundle) -> None:
    """质量 Judge 不应看到 Target/Reference 身份，双边结构必须相同。"""
    case = load_competitive_dataset(DATASET).cases[0]
    serialized = _quality_payload(case, valid_bundle, target_first=True)
    payload = json.loads(
        serialized.split("<untrusted_evaluation_data>\n", 1)[1].split(
            "\n</untrusted_evaluation_data>",
            1,
        )[0]
    )

    assert "fixed_reference" not in serialized
    assert "target_run_bundle" not in serialized
    assert "target" not in payload["input_order_id"]
    assert "reference" not in payload["input_order_id"]
    assert set(payload["article_1"]) == set(payload["article_2"])
    assert "claim_id" in json.dumps(payload["article_1"], ensure_ascii=False)
    for candidate in (build_reference_candidate(case),):
        for claim in candidate["claims"]:
            assert claim["claim_id"] not in serialized
        for source in candidate["sources"]:
            assert source["source_id"] not in serialized
    for claim in valid_bundle.claims:
        assert claim.claim_id not in serialized
    for source in valid_bundle.sources:
        assert source.source_id not in serialized
    for evidence in valid_bundle.evidence:
        assert evidence.evidence_id not in serialized
    assert set(payload["article_1"]["claims"][0]) == set(
        payload["article_2"]["claims"][0]
    )
    assert {claim["semantic_type"] for claim in payload["article_1"]["claims"]} <= {
        "assertion",
        "unknown",
    }
    assert {claim["semantic_type"] for claim in payload["article_2"]["claims"]} <= {
        "assertion",
        "unknown",
    }
    for article in (payload["article_1"], payload["article_2"]):
        assert all(
            source["title"] is None
            and source["publisher"] is None
            and source["url"] is None
            and source["published_at"] is None
            for source in article["sources"]
        )
        for card in article["claim_support_cards"]:
            for support in card["supports"]:
                excerpt = support["excerpt"]
                assert support["excerpt_sha256"] == (
                    hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
                    if excerpt
                    else None
                )


def test_quality_output_rejects_missing_criterion(valid_bundle) -> None:
    """质量 Judge 遗漏固定 Criterion 时必须拒绝整次评测。"""
    case = load_competitive_dataset(DATASET).cases[0]
    output = _judge_output(
        case,
        target_score=8.0,
        reference_score=9.0,
        reverse=False,
    )
    incomplete = output.model_copy(update={"criteria": output.criteria[:-1]})

    with pytest.raises(ValueError, match="完整且不重复"):
        _validate_quality_output(
            case,
            valid_bundle,
            incomplete,
            target_first=True,
        )


def test_fact_output_rejects_dangling_target_claim_id(valid_bundle) -> None:
    """Reference 覆盖不得引用 Target Bundle 中不存在的 Claim。"""
    case = load_competitive_dataset(DATASET).cases[0]
    required_claim_ids = {
        claim_id
        for criterion in case.criteria.fact
        for claim_id in criterion.reference_claim_ids
    }
    required_claims = tuple(
        claim for claim in case.reference_claims if claim.claim_id in required_claim_ids
    )
    output = CompetitiveFactJudgeOutput(
        prompt_version="opencompete-competitive-judge-v2.2.3",
        reference_claims=tuple(
            ReferenceClaimCoverage(
                reference_claim_id=claim.claim_id,
                status=ReferenceCoverageStatus.SUPPORTED,
                target_claim_ids=("clm-does-not-exist",),
                reason="故意构造悬空引用",
            )
            for claim in required_claims
        ),
        target_claims=tuple(
            TargetClaimSupportAssessment(
                claim_id=claim.claim_id,
                status=TargetClaimSupportStatus.SUPPORTED,
                evidence_ids=claim.evidence_ids,
                reason="固定支持",
            )
            for claim in valid_bundle.claims
        ),
        target_relationships=tuple(
            FactSupport(
                claim_id=claim.claim_id,
                evidence_id=evidence_id,
                supported=True,
                reason="固定支持",
            )
            for claim in valid_bundle.claims
            for evidence_id in claim.evidence_ids
        ),
    )

    with pytest.raises(ValueError, match="不存在或重复"):
        _validate_fact_output(case, valid_bundle, output)


def test_fact_output_rejects_incomplete_target_evidence(valid_bundle) -> None:
    """Claim 级 FACT 审计必须完整且不重复地覆盖其声明 Evidence。"""
    case = load_competitive_dataset(DATASET).cases[0]
    required_claim_ids = {
        claim_id
        for criterion in case.criteria.fact
        for claim_id in criterion.reference_claim_ids
    }
    omitted_evidence_claim_id = next(
        claim.claim_id for claim in valid_bundle.claims if claim.evidence_ids
    )
    target_claims = tuple(
        TargetClaimSupportAssessment(
            claim_id=claim.claim_id,
            status=(
                TargetClaimSupportStatus.HONEST_UNKNOWN
                if claim.status is ClaimStatus.UNKNOWN
                else TargetClaimSupportStatus.SUPPORTED
            ),
            evidence_ids=(
                ()
                if claim.claim_id == omitted_evidence_claim_id
                else claim.evidence_ids
            ),
            reason="构造不完整 Evidence 审计",
        )
        for claim in valid_bundle.claims
    )
    output = CompetitiveFactJudgeOutput(
        prompt_version="opencompete-competitive-judge-v2.2.3",
        reference_claims=tuple(
            ReferenceClaimCoverage(
                reference_claim_id=claim.claim_id,
                status=(
                    ReferenceCoverageStatus.HONEST_UNKNOWN
                    if claim.claim_type is ReferenceClaimType.UNKNOWN
                    else ReferenceCoverageStatus.SUPPORTED
                ),
                reason="固定覆盖",
            )
            for claim in case.reference_claims
            if claim.claim_id in required_claim_ids
        ),
        target_claims=target_claims,
        target_relationships=tuple(
            FactSupport(
                claim_id=claim.claim_id,
                evidence_id=evidence_id,
                supported=True,
                reason="固定支持",
            )
            for claim in valid_bundle.claims
            for evidence_id in claim.evidence_ids
        ),
    )

    with pytest.raises(ValueError, match="完整且不重复"):
        _validate_fact_output(case, valid_bundle, output)


def test_important_nonfactual_analysis_cannot_bypass_fact_gate(valid_bundle) -> None:
    """重要事实断言不能靠 nonfactual_analysis 标签绕过支持门禁。"""
    case = load_competitive_dataset(DATASET).cases[0]
    required_claim_ids = {
        claim_id
        for criterion in case.criteria.fact
        for claim_id in criterion.reference_claim_ids
    }
    important_id = next(
        claim.claim_id for claim in valid_bundle.claims if claim.important
    )
    output = CompetitiveFactJudgeOutput(
        prompt_version="opencompete-competitive-judge-v2.2.3",
        reference_claims=tuple(
            ReferenceClaimCoverage(
                reference_claim_id=claim.claim_id,
                status=(
                    ReferenceCoverageStatus.HONEST_UNKNOWN
                    if claim.claim_type is ReferenceClaimType.UNKNOWN
                    else ReferenceCoverageStatus.SUPPORTED
                ),
                reason="固定覆盖",
            )
            for claim in case.reference_claims
            if claim.claim_id in required_claim_ids
        ),
        target_claims=tuple(
            TargetClaimSupportAssessment(
                claim_id=claim.claim_id,
                status=(
                    TargetClaimSupportStatus.NONFACTUAL_ANALYSIS
                    if claim.claim_id == important_id
                    else (
                        TargetClaimSupportStatus.HONEST_UNKNOWN
                        if claim.status is ClaimStatus.UNKNOWN
                        else TargetClaimSupportStatus.SUPPORTED
                    )
                ),
                evidence_ids=claim.evidence_ids,
                reason="测试标签绕过",
            )
            for claim in valid_bundle.claims
        ),
        target_relationships=tuple(
            FactSupport(
                claim_id=claim.claim_id,
                evidence_id=evidence_id,
                supported=True,
                reason="关系本身支持",
            )
            for claim in valid_bundle.claims
            for evidence_id in claim.evidence_ids
        ),
    )

    result = aggregate_fact(case, valid_bundle, output)

    assert result.unsupported_important_target_claim_count == 1
    assert result.eligible is False


class _OfflineCompetitiveJudge(CompetitiveReportJudge):
    """不联网且固定返回稳定结果的 v2 Judge。"""

    model_name = "offline:test"
    prompt_version = "opencompete-competitive-judge-v2.2.3"

    async def evaluate_pair(self, case, bundle, *, target_first):
        del bundle
        return _judge_output(
            case,
            target_score=8.0,
            reference_score=9.0,
            reverse=not target_first,
        )

    async def evaluate_fact(self, case, bundle):
        required_claim_ids = {
            claim_id
            for criterion in case.criteria.fact
            for claim_id in criterion.reference_claim_ids
        }
        return CompetitiveFactJudgeOutput(
            prompt_version="opencompete-competitive-judge-v2.2.3",
            reference_claims=tuple(
                ReferenceClaimCoverage(
                    reference_claim_id=claim.claim_id,
                    status=(
                        ReferenceCoverageStatus.HONEST_UNKNOWN
                        if claim.claim_type is ReferenceClaimType.UNKNOWN
                        else ReferenceCoverageStatus.SUPPORTED
                    ),
                    target_claim_ids=(bundle.claims[0].claim_id,),
                    reason="离线固定覆盖",
                )
                for claim in case.reference_claims
                if claim.claim_id in required_claim_ids
            ),
            target_claims=tuple(
                TargetClaimSupportAssessment(
                    claim_id=claim.claim_id,
                    status=(
                        TargetClaimSupportStatus.HONEST_UNKNOWN
                        if claim.status.value == "unknown"
                        else TargetClaimSupportStatus.SUPPORTED
                    ),
                    evidence_ids=claim.evidence_ids,
                    reason="离线固定支持",
                )
                for claim in bundle.claims
            ),
            target_relationships=tuple(
                FactSupport(
                    claim_id=claim.claim_id,
                    evidence_id=evidence_id,
                    supported=True,
                    reason="离线固定支持",
                )
                for claim in bundle.claims
                for evidence_id in claim.evidence_ids
            ),
        )


@pytest.mark.asyncio
async def test_competitive_service_reads_verified_bundle_and_persists_result(
    tmp_path: Path,
    valid_bundle,
) -> None:
    """v2 服务只读取已发布 revision，并写回同一任务 evaluations 目录。"""
    case = load_competitive_dataset(DATASET).cases[0]
    scope = case.expected_scope.model_copy(
        update={
            "confirmed_at": valid_bundle.scope.confirmed_at,
            "revision": 1,
            "frozen": True,
        }
    )
    claim = valid_bundle.claims[0].model_copy(
        update={
            "entity_id": scope.entities[0].entity_id,
            "level": scope.levels[0],
            "dimension": scope.dimensions[scope.levels[0]][0],
        }
    )
    claims = [claim]
    covered = {(claim.entity_id, claim.level, claim.dimension)}
    for entity in scope.entities:
        for level in scope.levels:
            for dimension in scope.dimensions[level]:
                if (entity.entity_id, level, dimension) in covered:
                    continue
                claims.append(
                    Claim(
                        text=f"{entity.name} 在 {dimension} 暂无可验证结论",
                        entity_id=entity.entity_id,
                        level=level,
                        dimension=dimension,
                        status=ClaimStatus.UNKNOWN,
                        uncertainty="离线测试显式覆盖冻结范围单元",
                    )
                )
    comparisons = tuple(
        Comparison(
            entity_ids=tuple(entity.entity_id for entity in scope.entities),
            level=level,
            dimension=dimension,
            claim_ids=tuple(
                item.claim_id
                for item in claims
                if item.level is level and item.dimension == dimension
            ),
        )
        for level in scope.levels
        for dimension in scope.dimensions[level]
    )
    report = valid_bundle.report.model_copy(
        update={
            "markdown": render_validated_report(
                scope=scope,
                claims=tuple(claims),
                comparisons=comparisons,
                sources=valid_bundle.sources,
                evidence=valid_bundle.evidence,
                limitations=(),
            ),
            "claim_ids": tuple(item.claim_id for item in claims),
            "comparison_ids": tuple(item.comparison_id for item in comparisons),
        }
    )
    bundle = valid_bundle.model_copy(
        update={
            "scope": scope,
            "claims": tuple(claims),
            "comparisons": comparisons,
            "report": report,
        }
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
            scope_revision=scope.revision,
            artifact_revision=bundle.artifact_revision,
            config_snapshot={
                "entry_mode": "fixed_scope_after_freeze_scope",
                "benchmark_case_id": case.case_id,
                "task_input_sha256": case.normalized_task_sha256,
            },
            budget_summary=bundle.budget,
        ),
    )
    service = CompetitiveEvaluationService(
        repository=repository,
        judge=_OfflineCompetitiveJudge(),
    )

    batch = await service.evaluate(
        CompetitiveEvaluationRequest(
            dataset=DATASET,
            targets=(EvaluationTarget(case_id=case.case_id, run_id=bundle.run_id),),
        )
    )

    result = batch.results[0]
    assert result.status.value == "completed"
    assert result.eligible_for_comparison is True
    assert result.quality.target_overall == 8.0
    assert result.fact.critical_claim_coverage == 1.0
    saved = list((tmp_path / "runs" / bundle.run_id / "evaluations").glob("*.json"))
    assert len(saved) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_field", "wrong_value"),
    (
        ("entry_mode", "interactive_scope_confirmation"),
        ("benchmark_case_id", "other-benchmark-case"),
        ("task_input_sha256", "0" * 64),
    ),
)
async def test_competitive_service_rejects_mismatched_benchmark_identity(
    tmp_path: Path,
    valid_bundle,
    identity_field: str,
    wrong_value: str,
) -> None:
    """正式评分不得接受入口、案例或规范化任务哈希不一致的 Run。"""
    case = load_competitive_dataset(DATASET).cases[0]
    config_snapshot = {
        "entry_mode": "fixed_scope_after_freeze_scope",
        "benchmark_case_id": case.case_id,
        "task_input_sha256": case.normalized_task_sha256,
    }
    config_snapshot[identity_field] = wrong_value
    repository = _publish_service_target(
        tmp_path,
        valid_bundle,
        config_snapshot=config_snapshot,
    )
    service = CompetitiveEvaluationService(
        repository=repository,
        judge=_OfflineCompetitiveJudge(),
    )

    with pytest.raises(ValueError, match=identity_field):
        await service.evaluate(
            CompetitiveEvaluationRequest(
                dataset=DATASET,
                targets=(
                    EvaluationTarget(
                        case_id=case.case_id,
                        run_id=valid_bundle.run_id,
                    ),
                ),
            )
        )

    assert not list(
        (tmp_path / "runs" / valid_bundle.run_id / "evaluations").glob("*.json")
    )


@pytest.mark.asyncio
async def test_competitive_service_rejects_recovered_manifest_without_case_identity(
    tmp_path: Path,
    valid_bundle,
) -> None:
    """Manifest 重建后无法恢复案例身份时只能拒绝正式评分。"""
    case = load_competitive_dataset(DATASET).cases[0]
    repository = _publish_service_target(
        tmp_path,
        valid_bundle,
        config_snapshot={
            "entry_mode": "fixed_scope_after_freeze_scope",
            "task_input_sha256": case.normalized_task_sha256,
        },
    )
    service = CompetitiveEvaluationService(
        repository=repository,
        judge=_OfflineCompetitiveJudge(),
    )

    with pytest.raises(ValueError, match="benchmark_case_id"):
        await service.evaluate(
            CompetitiveEvaluationRequest(
                dataset=DATASET,
                targets=(
                    EvaluationTarget(
                        case_id=case.case_id,
                        run_id=valid_bundle.run_id,
                    ),
                ),
            )
        )

    assert not list(
        (tmp_path / "runs" / valid_bundle.run_id / "evaluations").glob("*.json")
    )


def _publish_service_target(
    data_root: Path,
    bundle,
    *,
    config_snapshot: dict[str, str],
) -> RunRepository:
    repository = RunRepository(data_root)
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
            config_snapshot=config_snapshot,
            budget_summary=bundle.budget,
        ),
    )
    return repository


def _judge_output(
    case,
    *,
    target_score: float,
    reference_score: float,
    reverse: bool,
) -> CompetitiveJudgeOutput:
    criteria = []
    for dimension, group in case.criteria.dimensions.items():
        for criterion in group.criteria:
            article_1_score = reference_score if reverse else target_score
            article_2_score = target_score if reverse else reference_score
            criteria.append(
                CompetitiveCriterionJudgeScore(
                    dimension=dimension,
                    criterion_id=criterion.criterion_id,
                    article_1_score=article_1_score,
                    article_2_score=article_2_score,
                    article_1_reason="达到固定测试锚点",
                    article_2_reason="达到固定测试锚点",
                    confidence=1.0,
                )
            )
    return CompetitiveJudgeOutput(
        prompt_version="opencompete-competitive-judge-v2.2.3",
        input_order_id=("pair_order_2" if reverse else "pair_order_1"),
        criteria=tuple(criteria),
    )
