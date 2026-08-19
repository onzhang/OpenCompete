from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from opencompete.api.schemas import run_list_item
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
    RunManifest,
    RunStatus,
    ScopeSpec,
    Source,
)


def test_partial_reason_does_not_reuse_historical_error_message() -> None:
    manifest = RunManifest(
        run_id="run-partial",
        status=RunStatus.PARTIAL,
        scope_revision=1,
        artifact_revision=1,
        errors=(),
    )
    view = run_list_item(manifest, {}, active=False)
    assert view["partial_reason"] == "研究部分完成，已发布可用成果"

    explicit = manifest.model_copy(update={"partial_reason": "预算已停止后续调用"})
    assert run_list_item(explicit, {}, active=False)["partial_reason"] == (
        "预算已停止后续调用"
    )


def test_manifest_allows_token_budget_names_but_rejects_credentials() -> None:
    common = {
        "run_id": "run-config",
        "status": RunStatus.DRAFT,
        "scope_revision": 0,
        "artifact_revision": 0,
    }

    manifest = RunManifest(
        **common,
        config_snapshot={"max_input_tokens": 1000, "max_output_tokens": 500},
    )

    assert manifest.config_snapshot["max_input_tokens"] == 1000
    with pytest.raises(ValueError, match="密钥字段"):
        RunManifest(**common, config_snapshot={"access_token": "secret"})


def test_research_entity_requires_a_non_empty_name() -> None:
    with pytest.raises(ValidationError):
        ResearchEntity(name="   ")


def test_supported_claim_requires_evidence_ids() -> None:
    with pytest.raises(ValidationError):
        Claim(
            text="产品 A 的公开价格更低",
            entity_id="entity-a",
            level=AnalysisLevel.PRODUCT_SERVICE,
            dimension="pricing",
            status=ClaimStatus.SUPPORTED,
        )

    with pytest.raises(ValidationError):
        Claim(
            text="暂时无法判断产品 A 的公开价格",
            entity_id="entity-a",
            level=AnalysisLevel.PRODUCT_SERVICE,
            dimension="pricing",
            status=ClaimStatus.UNKNOWN,
        )
    with pytest.raises(ValidationError):
        Claim(
            text="暂时无法判断产品 A 的公开价格",
            entity_id="entity-a",
            level=AnalysisLevel.PRODUCT_SERVICE,
            dimension="pricing",
            evidence_ids=("evidence-a",),
            status=ClaimStatus.UNKNOWN,
            uncertainty="证据不足",
        )

    claim = Claim(
        text="暂时无法判断产品 A 的公开价格",
        entity_id="entity-a",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        status=ClaimStatus.UNKNOWN,
        uncertainty="没有捕获到可验证的定价证据",
    )
    assert claim.status is ClaimStatus.UNKNOWN


def test_source_requires_a_public_http_url() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValidationError):
        Source(url="file:///etc/passwd", title="本地文件", accessed_at=now)

    source = Source(
        url="HTTPS://EXAMPLE.COM:443/a#fragment",
        title="公开来源",
        accessed_at=now,
    )
    assert source.url == "https://example.com/a"


def test_traceability_gate_finds_dangling_references() -> None:
    now = datetime.now(UTC)
    entity = ResearchEntity(name="产品 A", entity_id="entity-a")
    source = Source(
        source_id="source-a",
        url="https://example.com/a",
        title="产品 A 定价页",
        publisher="产品 A",
        accessed_at=now,
    )
    evidence = Evidence(
        evidence_id="evidence-a",
        source_id="missing-source",
        excerpt="公开价格为每月 10 美元。",
    )
    claim = Claim(
        claim_id="claim-a",
        text="产品 A 每月 10 美元",
        entity_id=entity.entity_id,
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=(evidence.evidence_id,),
        status=ClaimStatus.SUPPORTED,
    )
    comparison = Comparison(
        comparison_id="comparison-a",
        entity_ids=(entity.entity_id,),
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        claim_ids=(claim.claim_id, "missing-claim"),
        differences=("产品 A 已公开价格",),
    )
    scope = _frozen_scope(entity)
    budget = BudgetSnapshot(
        limit=BudgetEstimate(),
        spent=BudgetEstimate(),
        reserved=BudgetEstimate(),
        remaining=BudgetEstimate(),
    )
    bundle = RunBundle(
        run_id="run-a",
        scope=scope,
        sources=(source,),
        evidence=(evidence,),
        claims=(claim,),
        comparisons=(comparison,),
        report=ReportArtifact(
            markdown="# 报告",
            comparison_ids=(comparison.comparison_id,),
            source_ids=(source.source_id,),
        ),
        budget=budget,
        status=RunStatus.PARTIAL,
    )

    codes = {violation.code for violation in bundle.traceability_violations()}

    assert "evidence_source_missing" in codes
    assert "comparison_claim_missing" in codes


def test_traceability_rejects_comparison_context_and_cross_level_free_text() -> None:
    now = datetime.now(UTC)
    entity_a = ResearchEntity(name="产品 A", entity_id="entity-a")
    entity_b = ResearchEntity(name="产品 B", entity_id="entity-b")
    source = Source(
        source_id="source-a",
        url="https://example.com/a",
        title="产品 A 定价页",
        accessed_at=now,
    )
    evidence = Evidence(
        evidence_id="evidence-a",
        source_id=source.source_id,
        excerpt="产品 A 的价格为每月 10 美元。",
    )
    claim = Claim(
        claim_id="claim-a",
        text="产品 A 的价格为每月 10 美元",
        entity_id=entity_a.entity_id,
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=(evidence.evidence_id,),
        status=ClaimStatus.SUPPORTED,
    )
    comparison = Comparison(
        comparison_id="comparison-a",
        entity_ids=(entity_b.entity_id,),
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="features",
        claim_ids=(claim.claim_id,),
    )
    scope = ScopeSpec(
        entities=(entity_a, entity_b),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing",)},
        confirmed_at=now,
        revision=1,
        frozen=True,
    )
    bundle = RunBundle(
        run_id="run-context",
        scope=scope,
        sources=(source,),
        evidence=(evidence,),
        claims=(claim,),
        comparisons=(comparison,),
        report=ReportArtifact(
            markdown="# 报告",
            claim_ids=(claim.claim_id,),
            comparison_ids=(comparison.comparison_id,),
            source_ids=(source.source_id,),
            cross_level_conclusions=("未经验证的跨层结论",),
        ),
        budget=BudgetSnapshot(),
        status=RunStatus.PARTIAL,
    )

    codes = {violation.code for violation in bundle.traceability_violations()}

    assert "comparison_dimension_out_of_scope" in codes
    assert "comparison_claim_entity_mismatch" in codes
    assert "comparison_claim_context_mismatch" in codes
    assert "report_cross_level_conclusions_unverified" in codes


def _frozen_scope(entity: ResearchEntity):
    from opencompete.models import ScopeSpec

    return ScopeSpec(
        entities=(entity,),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing",)},
        confirmed_at=datetime.now(UTC),
        revision=1,
        frozen=True,
    )
