"""OpenCompete Bench 评测测试夹具。"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from opencompete.evaluation.models import (
    BenchmarkCase,
    BenchmarkCriteria,
    BenchmarkSource,
    FactCriterion,
    RaceCriteria,
    ReferenceReview,
    ReviewStatus,
    SourceAuthority,
)
from opencompete.models import (
    AnalysisLevel,
    BudgetEstimate,
    BudgetSnapshot,
    Claim,
    ClaimStatus,
    Comparison,
    Evidence,
    ReportArtifact,
    ResearchEntity,
    RunBundle,
    RunStatus,
    ScopeSpec,
    Source,
)
from opencompete.reporting import render_validated_report


@pytest.fixture
def benchmark_case() -> BenchmarkCase:
    """返回一个无需网络即可使用的已审核案例。"""
    entities = (ResearchEntity(name="甲公司"), ResearchEntity(name="乙公司"))
    scope = ScopeSpec(
        entities=entities,
        levels=(AnalysisLevel.COMPANY_ORGANIZATION,),
        dimensions={AnalysisLevel.COMPANY_ORGANIZATION: ("商业模式",)},
    )
    return BenchmarkCase(
        case_id="ocb-test",
        title="甲公司与乙公司对比",
        as_of=date(2026, 7, 15),
        expected_scope=scope,
        prompt="# 测试任务\n\n比较甲公司与乙公司的商业模式。",
        reference_report="# 人工参考报告\n\n这是已审核的测试参考。",
        reference_review=ReferenceReview(
            status=ReviewStatus.APPROVED,
            reviewed_by="测试审核员",
            reviewed_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
            notes="仅用于离线单元测试。",
        ),
        criteria=BenchmarkCriteria(
            case_id="ocb-test",
            race=RaceCriteria(
                comprehensiveness=("覆盖两个对象",),
                depth=("解释差异原因",),
                instruction_following=("限定商业模式维度",),
                readability=("结构清晰",),
            ),
            fact=(
                FactCriterion(
                    criterion_id="fact-1",
                    description="关键商业模式结论有来源支持",
                    source_ids=("official-a",),
                ),
            ),
        ),
        sources=(
            BenchmarkSource(
                source_id="official-a",
                title="甲公司官网",
                publisher="甲公司",
                url="https://example.com/company-a",
                authority=SourceAuthority.OFFICIAL,
                as_of=date(2026, 7, 15),
            ),
        ),
    )


@pytest.fixture
def valid_bundle(benchmark_case: BenchmarkCase) -> RunBundle:
    """返回与测试案例完全对齐且通过确定性门禁的 Bundle。"""
    expected = benchmark_case.expected_scope
    scope = expected.model_copy(
        update={
            "confirmed_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
            "revision": 1,
            "frozen": True,
        }
    )
    source = Source(
        source_id="src-a",
        url="https://example.com/company-a",
        title="甲公司官网",
        accessed_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    evidence = Evidence(
        evidence_id="evd-a",
        source_id=source.source_id,
        excerpt="甲公司采用订阅模式。",
    )
    claim = Claim(
        claim_id="clm-a",
        text="甲公司采用订阅模式",
        entity_id=scope.entities[0].entity_id,
        level=AnalysisLevel.COMPANY_ORGANIZATION,
        dimension="商业模式",
        evidence_ids=(evidence.evidence_id,),
        status=ClaimStatus.SUPPORTED,
    )
    comparison = Comparison(
        comparison_id="cmp-a",
        entity_ids=tuple(entity.entity_id for entity in scope.entities),
        level=AnalysisLevel.COMPANY_ORGANIZATION,
        dimension="商业模式",
        claim_ids=(claim.claim_id,),
        differences=("两者收入模式不同",),
    )
    spent = BudgetEstimate(
        input_tokens=100,
        output_tokens=50,
        tavily_calls=1,
        cost_usd=0.1,
    )
    limit = BudgetEstimate(
        input_tokens=1000,
        output_tokens=1000,
        tavily_calls=10,
        cost_usd=1.0,
    )
    return RunBundle(
        run_id="run-evaluation",
        scope=scope,
        report=ReportArtifact(
            markdown=render_validated_report(
                scope=scope,
                claims=(claim,),
                comparisons=(comparison,),
                sources=(source,),
                evidence=(evidence,),
                limitations=(),
            ),
            claim_ids=(claim.claim_id,),
            comparison_ids=(comparison.comparison_id,),
            source_ids=(source.source_id,),
        ),
        budget=BudgetSnapshot(
            limit=limit,
            spent=spent,
            remaining=BudgetEstimate(
                input_tokens=900,
                output_tokens=950,
                tavily_calls=9,
                cost_usd=0.9,
            ),
        ),
        status=RunStatus.COMPLETED,
        sources=(source,),
        evidence=(evidence,),
        claims=(claim,),
        comparisons=(comparison,),
        artifact_revision=1,
    )
