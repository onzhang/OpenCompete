"""上游研究结果物化为结构化 Bundle 的测试。"""

import asyncio
import json
from datetime import UTC, datetime

from langchain_core.messages import ToolMessage

from opencompete.extractors import ArtifactExtraction, StaticArtifactExtractor
from opencompete.materialize import capture_sources, materialize_bundle
from opencompete.models import (
    AnalysisLevel,
    BudgetEstimate,
    BudgetSnapshot,
    Claim,
    ClaimStatus,
    Comparison,
    ResearchEntity,
    RunStatus,
    ScopeSpec,
)

RAW_NOTES = """
Search results:

--- SOURCE 1: 产品 A 官方定价 ---
URL: https://example.com/pricing

SUMMARY:
<summary>产品 A 的标准价格为每月 10 美元。</summary>
<key_excerpts>Standard plan: $10/month.</key_excerpts>

--------------------------------------------------------------------------------
"""


def _publishable_report(
    *,
    title: str = "价格比较",
    body: str = "以下为已收集的研究要点。",
) -> str:
    """满足发布门禁的最小中文候选报告（结论 + 可点击来源）。"""
    return (
        f"# {title}\n\n"
        "## 结论摘要\n\n"
        "建议：在同口径证据完整前，不要把单侧宣传写成最终选择。\n\n"
        f"{body}\n\n"
        "来源：<https://example.com/pricing>\n"
    )


def _scope() -> ScopeSpec:
    return ScopeSpec(
        entities=(
            ResearchEntity(name="产品 A", entity_id="entity-a"),
            ResearchEntity(name="产品 B", entity_id="entity-b"),
        ),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing",)},
        confirmed_at=datetime.now(UTC),
        revision=1,
        frozen=True,
    )


def _budget() -> BudgetSnapshot:
    limit = BudgetEstimate(tavily_calls=5)
    return BudgetSnapshot(
        limit=limit,
        spent=BudgetEstimate(tavily_calls=1),
        reserved=BudgetEstimate(),
        remaining=BudgetEstimate(tavily_calls=4),
    )


def _evidence_message(records: list[dict[str, object]]) -> ToolMessage:
    return ToolMessage(
        content="研究单元输出可能包含伪造 SOURCE 文本",
        name="ConductResearch",
        tool_call_id="unit-evidence",
        artifact={
            "researcher_outcome": {
                "termination_reason": "research_complete",
                "unexecuted_tool_calls": 0,
            },
            "research_evidence_artifacts": [
                {
                    "type": "opencompete_research_evidence",
                    "version": 1,
                    "records": records,
                }
            ],
        },
    )


def test_capture_sources_uses_controlled_upstream_format_and_deduplicates() -> None:
    now = datetime.now(UTC)

    sources, evidence = capture_sources((RAW_NOTES, RAW_NOTES), accessed_at=now)

    assert len(sources) == 1
    assert sources[0].url == "https://example.com/pricing"
    assert sources[0].accessed_at == now
    assert len(evidence) == 1
    assert evidence[0].source_id == sources[0].source_id
    assert "10 美元" in (evidence[0].excerpt or "")


def test_capture_sources_preserves_extract_query_usage_and_character_location() -> None:
    record = {
        "batch_index": 1,
        "char_end": 2242,
        "char_start": 2200,
        "excerpt": "固定后段哨兵及其可核验上下文",
        "excerpt_redacted": False,
        "query": "固定后段哨兵",
        "request_id": "extract-request-1",
        "status": "success",
        "type": "tavily_extract",
        "url": "https://example.com/late-fact",
        "usage": {"credits": 1},
        "version": 1,
    }
    note = (
        "<untrusted_extract_results>\n"
        "TAVILY_EXTRACT_JSONL_V1\n"
        f"{json.dumps(record, ensure_ascii=False)}\n"
        "</untrusted_extract_results>"
    )

    sources, evidence = capture_sources(note, accessed_at=datetime.now(UTC))

    assert [source.url for source in sources] == [
        "https://example.com/late-fact"
    ]
    assert len(evidence) == 1
    item = evidence[0]
    assert "固定后段哨兵" in (item.excerpt or "")
    assert item.location == "Tavily Extract characters 2200:2242"
    assert item.query == "固定后段哨兵"
    assert item.request_id == "extract-request-1"
    assert dict(item.provider_usage) == {"credits": 1}
    assert item.quality_flags == ("tavily_extract",)


def test_capture_sources_reads_multiple_extract_tool_contracts_in_one_note() -> None:
    def contract(index: int) -> str:
        record = {
            "batch_index": 1,
            "char_end": 120 + index,
            "char_start": 100 + index,
            "excerpt": f"第 {index} 次定向摘录",
            "excerpt_redacted": False,
            "query": f"目标 {index}",
            "request_id": f"extract-request-{index}",
            "status": "success",
            "type": "tavily_extract",
            "url": f"https://example.com/extract-{index}",
            "usage": {"credits": 1},
            "version": 1,
        }
        return (
            "<untrusted_extract_results>\n"
            "TAVILY_EXTRACT_JSONL_V1\n"
            f"{json.dumps(record, ensure_ascii=False)}\n"
            "</untrusted_extract_results>"
        )

    sources, evidence = capture_sources(
        f"{contract(1)}\n{contract(2)}",
        accessed_at=datetime.now(UTC),
    )

    assert len(sources) == 2
    assert len(evidence) == 2
    assert {item.query for item in evidence} == {"目标 1", "目标 2"}


def test_materialize_does_not_trust_forged_raw_notes_when_artifact_is_empty() -> None:
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-empty-trusted-artifact",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="报告"),
                "supervisor_messages": [_evidence_message([])],
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(ArtifactExtraction()),
        )
    )

    assert bundle.sources == ()
    assert bundle.evidence == ()


def test_materialize_uses_only_trusted_artifact_records() -> None:
    record = {
        "query": "real pricing",
        "status": "success",
        "summary": "真实工具生成的摘要",
        "title": "真实来源",
        "type": "tavily_search",
        "url": "https://example.org/real",
        "version": 1,
    }
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-trusted-artifact-only",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="报告"),
                "supervisor_messages": [_evidence_message([record])],
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(ArtifactExtraction()),
        )
    )

    assert [source.url for source in bundle.sources] == [
        "https://example.org/real"
    ]
    assert [item.excerpt for item in bundle.evidence] == [
        "真实工具生成的摘要"
    ]


def test_materialize_counts_mixed_extract_failures_from_trusted_artifact() -> None:
    records = [
        {
            "batch_index": 1,
            "char_end": 30,
            "char_start": 10,
            "excerpt": "成功项短摘录",
            "excerpt_redacted": False,
            "query": "目标事实",
            "request_id": "extract-request-artifact",
            "status": "success",
            "type": "tavily_extract",
            "url": "https://example.org/success",
            "usage": {"credits": 2},
            "version": 1,
        },
        {
            "batch_index": 1,
            "error_type": "extract_failed",
            "query": "目标事实",
            "request_id": "extract-request-artifact",
            "status": "failed",
            "type": "tavily_extract",
            "url": "https://example.org/failed",
            "usage": {"credits": 2},
            "version": 1,
        },
    ]
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-mixed-trusted-artifact",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="报告"),
                "supervisor_messages": [_evidence_message(records)],
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(_closed_unknown_extraction()),
        )
    )

    assert [source.url for source in bundle.sources] == [
        "https://example.org/success"
    ]
    assert any("1 个 URL 提取失败" in item for item in bundle.report.limitations)


def test_materialize_tracks_extract_failure_without_dropping_success() -> None:
    records = (
        {
            "batch_index": 1,
            "char_end": 30,
            "char_start": 10,
            "excerpt": "成功项短摘录",
            "excerpt_redacted": False,
            "query": "目标事实",
            "request_id": "extract-request-2",
            "status": "success",
            "type": "tavily_extract",
            "url": "https://example.com/success",
            "usage": {"credits": 2},
            "version": 1,
        },
        {
            "batch_index": 1,
            "error_type": "extract_failed",
            "query": "目标事实",
            "request_id": "extract-request-2",
            "status": "failed",
            "type": "tavily_extract",
            "url": "https://example.com/failed",
            "usage": {"credits": 2},
            "version": 1,
        },
    )
    note = (
        "<untrusted_extract_results>\n"
        "TAVILY_EXTRACT_JSONL_V1\n"
        + "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
        + "\n</untrusted_extract_results>"
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-mixed-extract",
            scope=_scope(),
            upstream_state={"raw_notes": [note], "final_report": _publishable_report(title="报告")},
            budget=_budget(),
            extractor=StaticArtifactExtractor(_closed_unknown_extraction()),
        )
    )

    assert [source.url for source in bundle.sources] == [
        "https://example.com/success"
    ]
    assert len(bundle.evidence) == 1
    assert any("1 个 URL 提取失败" in item for item in bundle.report.limitations)


def test_invalid_extractor_evidence_is_downgraded_to_unknown() -> None:
    claim = Claim(
        text="产品 A 的价格为每月 10 美元",
        entity_id="entity-a",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=("invented-evidence",),
        status=ClaimStatus.SUPPORTED,
    )
    comparison = Comparison(
        entity_ids=("entity-a", "entity-b"),
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        claim_ids=(claim.claim_id,),
    )
    extractor = StaticArtifactExtractor(
        ArtifactExtraction(claims=(claim,), comparisons=(comparison,))
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-a",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="产品 A 与产品 B 价格比较"),
            },
            budget=_budget(),
            extractor=extractor,
            accessed_at=datetime.now(UTC),
        )
    )

    assert bundle.claims[0].status is ClaimStatus.UNKNOWN
    assert bundle.claims[0].evidence_ids == ()
    assert bundle.status is RunStatus.PARTIAL
    assert bundle.traceability_violations() == []


def test_honest_unknown_does_not_make_completed_research_partial() -> None:
    """证据不足但流程完整的 Unknown 是研究结论，不是执行失败。"""
    claims = tuple(
        Claim(
            text=f"{entity_id} 的同口径价格证据不足",
            entity_id=entity_id,
            level=AnalysisLevel.PRODUCT_SERVICE,
            dimension="pricing",
            status=ClaimStatus.UNKNOWN,
            uncertainty="固定截止日前没有取得同地区、同 SKU 的官方价格证据",
        )
        for entity_id in ("entity-a", "entity-b")
    )
    comparison = Comparison(
        entity_ids=("entity-a", "entity-b"),
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        claim_ids=tuple(claim.claim_id for claim in claims),
        gaps=("同口径价格保持 Unknown，需在采购日前补证",),
    )
    extractor = StaticArtifactExtractor(
        ArtifactExtraction(claims=claims, comparisons=(comparison,))
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-honest-unknown",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="价格比较", body="当前证据不足，不能确定排名。"),
            },
            budget=_budget(),
            extractor=extractor,
            accessed_at=datetime.now(UTC),
        )
    )

    assert bundle.status is RunStatus.COMPLETED
    assert all(claim.status is ClaimStatus.UNKNOWN for claim in bundle.claims)


def _closed_unknown_extraction() -> ArtifactExtraction:
    claims = tuple(
        Claim(
            text=f"{entity_id} 的同口径证据不足",
            entity_id=entity_id,
            level=AnalysisLevel.PRODUCT_SERVICE,
            dimension="pricing",
            status=ClaimStatus.UNKNOWN,
            uncertainty="已检索但没有取得同一口径的可验证证据",
        )
        for entity_id in ("entity-a", "entity-b")
    )
    return ArtifactExtraction(
        claims=claims,
        comparisons=(
            Comparison(
                entity_ids=("entity-a", "entity-b"),
                level=AnalysisLevel.PRODUCT_SERVICE,
                dimension="pricing",
                claim_ids=tuple(claim.claim_id for claim in claims),
                gaps=("同口径证据不足，保持 Unknown",),
            ),
        ),
    )


def test_reached_budget_limit_with_closed_coverage_can_complete() -> None:
    budget = BudgetSnapshot(
        limit=BudgetEstimate(tavily_calls=1),
        spent=BudgetEstimate(tavily_calls=1),
        remaining=BudgetEstimate(),
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-closed-at-limit",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="已闭合的报告"),
                "supervisor_termination_reason": "research_complete",
            },
            budget=budget,
            extractor=StaticArtifactExtractor(_closed_unknown_extraction()),
        )
    )

    assert bundle.status is RunStatus.COMPLETED


def test_forced_termination_with_closed_coverage_can_complete() -> None:
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-forced-but-closed",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="已闭合的报告"),
                "supervisor_termination_reason": "model_turn_limit",
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(_closed_unknown_extraction()),
        )
    )

    assert bundle.status is RunStatus.COMPLETED
    assert any("结构化覆盖已闭合" in item for item in bundle.report.limitations)


def test_forced_termination_with_unclosed_coverage_is_partial() -> None:
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-forced-unclosed",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="未闭合的报告"),
                "supervisor_termination_reason": "model_turn_limit",
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(ArtifactExtraction()),
        )
    )

    assert bundle.status is RunStatus.PARTIAL
    assert any("覆盖未闭合" in item for item in bundle.report.limitations)


def test_unexecuted_supervisor_delegation_forces_partial() -> None:
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-unexecuted-delegation",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="已有报告"),
                "supervisor_termination_reason": "research_complete",
                "unexecuted_research_calls": 1,
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(_closed_unknown_extraction()),
        )
    )

    assert bundle.status is RunStatus.PARTIAL
    assert any("1 个 Supervisor 研究委托未执行" in item for item in bundle.report.limitations)


def test_forced_researcher_termination_with_closed_coverage_can_complete() -> None:
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-researcher-forced-closed",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="已闭合的报告"),
                "supervisor_termination_reason": "research_complete",
                "researcher_termination_reasons": [
                    "model_turn_limit",
                    "research_complete",
                ],
                "unexecuted_research_tool_calls": 0,
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(_closed_unknown_extraction()),
        )
    )

    assert bundle.status is RunStatus.COMPLETED
    assert any("Researcher 因 model_turn_limit 强制终止" in item for item in bundle.report.limitations)


def test_forced_researcher_termination_with_unclosed_coverage_is_partial() -> None:
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-researcher-forced-unclosed",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="未闭合的报告"),
                "supervisor_termination_reason": "research_complete",
                "researcher_termination_reasons": ["research_tool_limit"],
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(ArtifactExtraction()),
        )
    )

    assert bundle.status is RunStatus.PARTIAL
    assert any("Researcher 因 research_tool_limit 强制终止" in item for item in bundle.report.limitations)


def test_unexecuted_researcher_tool_forces_partial_with_closed_coverage() -> None:
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-researcher-tool-unexecuted",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="已闭合的报告"),
                "supervisor_termination_reason": "research_complete",
                "researcher_termination_reasons": ["research_tool_limit"],
                "unexecuted_research_tool_calls": 1,
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(_closed_unknown_extraction()),
        )
    )

    assert bundle.status is RunStatus.PARTIAL
    assert any("1 个 Researcher 研究工具未执行" in item for item in bundle.report.limitations)


def test_materialize_recovers_researcher_outcomes_from_parent_messages() -> None:
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-researcher-outcome-artifact",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="已闭合的报告"),
                "supervisor_termination_reason": "research_complete",
                "supervisor_messages": [
                    ToolMessage(
                        content="completed findings",
                        name="ConductResearch",
                        tool_call_id="unit-1",
                        artifact={
                            "researcher_outcome": {
                                "termination_reason": "model_turn_limit",
                                "unexecuted_tool_calls": 1,
                            }
                        },
                    )
                ],
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(_closed_unknown_extraction()),
        )
    )

    assert bundle.status is RunStatus.PARTIAL
    assert any("1 个 Researcher 研究工具未执行" in item for item in bundle.report.limitations)


def test_materialize_adds_unknown_for_scope_entity_without_any_claim() -> None:
    _, evidence = capture_sources((RAW_NOTES,), accessed_at=datetime.now(UTC))
    claim = Claim(
        text="产品 A 的标准价格为每月 10 美元",
        entity_id="entity-a",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=(evidence[0].evidence_id,),
        status=ClaimStatus.SUPPORTED,
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-coverage",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="产品 A 与产品 B 价格比较"),
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(ArtifactExtraction(claims=(claim,))),
            accessed_at=datetime.now(UTC),
        )
    )

    claims_by_entity = {item.entity_id: item for item in bundle.claims}
    assert claims_by_entity["entity-a"].status is ClaimStatus.SUPPORTED
    assert claims_by_entity["entity-b"].status is ClaimStatus.UNKNOWN
    assert set(bundle.comparisons[-1].entity_ids) == {"entity-a", "entity-b"}
    assert bundle.traceability_violations() == []


def test_successful_final_report_is_preserved_and_structured_data_stays_separate() -> None:
    _, evidence = capture_sources((RAW_NOTES,), accessed_at=datetime.now(UTC))
    claim = Claim(
        text="产品 A 的标准价格为每月 10 美元",
        entity_id="entity-a",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=(evidence[0].evidence_id,),
        status=ClaimStatus.SUPPORTED,
    )
    comparison = Comparison(
        entity_ids=("entity-a", "entity-b"),
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        claim_ids=(claim.claim_id,),
        differences=("产品 A 每月只需 1 美元",),
        commonalities=("两个产品都绝对安全",),
        gaps=("产品 B 肯定缺少关键功能",),
    )
    extractor = StaticArtifactExtractor(
        ArtifactExtraction(
            claims=(claim,),
            comparisons=(comparison,),
            cross_level_conclusions=("产品 A 在所有方面绝对领先",),
        )
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-rendered-report",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="原始报告", body="产品 A 每月 999 美元且绝对领先。"),
            },
            budget=_budget(),
            extractor=extractor,
            accessed_at=datetime.now(UTC),
        )
    )

    assert bundle.status is RunStatus.PARTIAL
    assert bundle.report.markdown == _publishable_report(title="原始报告", body="产品 A 每月 999 美元且绝对领先。").strip()
    assert "999" in bundle.report.markdown
    assert "每月 1 美元" not in bundle.report.markdown
    assert "所有方面绝对领先" not in bundle.report.markdown
    assert bundle.comparisons[0].differences == ("产品 A 每月只需 1 美元",)
    assert bundle.comparisons[0].commonalities == ("两个产品都绝对安全",)
    assert bundle.comparisons[0].gaps == ("产品 B 肯定缺少关键功能",)
    assert bundle.report.cross_level_conclusions == ()
    assert "跨层未验证自由文本已丢弃" in bundle.report.limitations
    assert claim.text not in bundle.report.markdown
    assert evidence[0].evidence_id not in bundle.report.markdown
    assert claim.claim_id not in bundle.report.markdown
    assert claim.claim_id in bundle.report.claim_ids
    assert any(item.entity_id == "entity-b" for item in bundle.claims)


def test_out_of_scope_claims_and_comparisons_are_not_published() -> None:
    claim = Claim(
        text="范围外公司的结论",
        entity_id="outside-entity",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        status=ClaimStatus.UNKNOWN,
        uncertainty="范围外对象没有可发布证据",
    )
    comparison = Comparison(
        entity_ids=("outside-entity",),
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        claim_ids=(claim.claim_id,),
    )
    extractor = StaticArtifactExtractor(
        ArtifactExtraction(claims=(claim,), comparisons=(comparison,))
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-a",
            scope=_scope(),
            upstream_state={"raw_notes": [RAW_NOTES], "final_report": _publishable_report(title="报告")},
            budget=_budget(),
            extractor=extractor,
            accessed_at=datetime.now(UTC),
        )
    )

    assert all(item.entity_id != "outside-entity" for item in bundle.claims)
    assert {item.entity_id for item in bundle.claims} == {"entity-a", "entity-b"}
    assert all(item.status is ClaimStatus.UNKNOWN for item in bundle.claims)
    assert all(
        "outside-entity" not in item.entity_ids for item in bundle.comparisons
    )
    assert any("范围外" in limitation for limitation in bundle.report.limitations)
    assert bundle.status is RunStatus.PARTIAL


def test_comparison_drops_claims_with_mismatched_entity_or_context() -> None:
    _, evidence = capture_sources((RAW_NOTES,), accessed_at=datetime.now(UTC))
    scope = ScopeSpec(
        entities=_scope().entities,
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing", "features")},
        confirmed_at=datetime.now(UTC),
        revision=1,
        frozen=True,
    )
    claim = Claim(
        text="产品 A 的标准价格为每月 10 美元",
        entity_id="entity-a",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=(evidence[0].evidence_id,),
        status=ClaimStatus.SUPPORTED,
    )
    comparison = Comparison(
        entity_ids=("entity-b",),
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="features",
        claim_ids=(claim.claim_id,),
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-mismatched-comparison",
            scope=scope,
            upstream_state={"raw_notes": [RAW_NOTES], "final_report": _publishable_report(title="报告")},
            budget=_budget(),
            extractor=StaticArtifactExtractor(
                ArtifactExtraction(claims=(claim,), comparisons=(comparison,))
            ),
        )
    )

    assert bundle.comparisons[0].claim_ids == ()
    assert bundle.status is RunStatus.PARTIAL
    assert "比较中的上下文不一致结论引用已被移除" in bundle.report.limitations


def test_comparison_free_text_is_preserved_and_missing_entity_stays_partial() -> None:
    _, evidence = capture_sources((RAW_NOTES,), accessed_at=datetime.now(UTC))
    claim = Claim(
        text="产品 A 的标准价格为每月 10 美元",
        entity_id="entity-a",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=(evidence[0].evidence_id,),
        status=ClaimStatus.SUPPORTED,
    )
    comparison = Comparison(
        entity_ids=("entity-a", "entity-b"),
        level=claim.level,
        dimension=claim.dimension,
        claim_ids=(claim.claim_id,),
        differences=("未经验证的差异",),
        commonalities=("未经验证的共性",),
        gaps=("未经验证的缺口",),
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-ignored-comparison-text",
            scope=_scope(),
            upstream_state={"raw_notes": [RAW_NOTES], "final_report": _publishable_report(title="报告")},
            budget=_budget(),
            extractor=StaticArtifactExtractor(
                ArtifactExtraction(claims=(claim,), comparisons=(comparison,))
            ),
        )
    )

    assert bundle.status is RunStatus.COMPLETED
    assert any(item.entity_id == "entity-b" for item in bundle.claims)
    assert bundle.comparisons[0].differences == ("未经验证的差异",)
    assert bundle.comparisons[0].commonalities == ("未经验证的共性",)
    assert bundle.comparisons[0].gaps == ("未经验证的缺口",)
    assert any("缺少结论覆盖的范围单元" in item for item in bundle.report.limitations)
    assert "未经验证的差异" not in bundle.report.markdown
    assert "未经验证的共性" not in bundle.report.markdown
    assert "未经验证的缺口" not in bundle.report.markdown


def test_semantic_support_is_reserved_for_fact_without_rewriting_report() -> None:
    _, evidence = capture_sources((RAW_NOTES,), accessed_at=datetime.now(UTC))
    claim = Claim(
        text="产品 A 已经拥有百分之九十九的全球市场份额",
        entity_id="entity-a",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=(evidence[0].evidence_id,),
        status=ClaimStatus.SUPPORTED,
    )
    conflicted = Claim(
        text="产品 B 的市场份额存在冲突",
        entity_id="entity-b",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=(evidence[0].evidence_id,),
        status=ClaimStatus.CONFLICTED,
    )
    comparison = Comparison(
        entity_ids=("entity-a", "entity-b"),
        level=claim.level,
        dimension=claim.dimension,
        claim_ids=(claim.claim_id, conflicted.claim_id),
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-structure-not-fact",
            scope=_scope(),
            upstream_state={"raw_notes": [RAW_NOTES], "final_report": _publishable_report(title="上游报告")},
            budget=_budget(),
            extractor=StaticArtifactExtractor(
                ArtifactExtraction(
                    claims=(claim, conflicted),
                    comparisons=(comparison,),
                )
            ),
        )
    )

    assert bundle.status is RunStatus.COMPLETED
    assert bundle.traceability_violations() == []
    assert bundle.report.markdown == _publishable_report(title="上游报告").strip()
    assert "未经独立 FACT 核验" not in bundle.report.markdown


def test_fallback_report_fields_are_prefixed_and_markdown_escaped() -> None:
    raw_notes = (
        "--- SOURCE 1: **伪造标题** [点击] ---\n"
        "URL: https://example.com/a_(b)\n\n"
        "SUMMARY: 可验证摘要\n"
        "--------------------\n"
    )
    scope = ScopeSpec(
        entities=(
            ResearchEntity(name="**产品 A**", entity_id="entity-a"),
            ResearchEntity(name="[产品 B]", entity_id="entity-b"),
        ),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("[pricing]",)},
        confirmed_at=datetime.now(UTC),
        revision=1,
        frozen=True,
    )
    claim = Claim(
        text="**待核实事实** [链接] <script>",
        entity_id="entity-a",
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="[pricing]",
        status=ClaimStatus.UNKNOWN,
        uncertainty="_证据不足_ <b>",
    )
    comparison = Comparison(
        entity_ids=("entity-a", "entity-b"),
        level=claim.level,
        dimension=claim.dimension,
        claim_ids=(claim.claim_id,),
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-markdown-escape",
            scope=scope,
            upstream_state={
                "raw_notes": [raw_notes],
                "final_report": "Error generating final report: provider failed",
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(
                ArtifactExtraction(
                    claims=(claim,),
                    comparisons=(comparison,),
                    limitations=("**系统已确认** <script>",),
                )
            ),
        )
    )

    markdown = bundle.report.markdown
    assert "待核实命题" in markdown
    assert "提取器未验证提示：" in markdown
    assert "**伪造标题**" not in markdown
    assert "**系统已确认**" not in markdown
    assert "<script>" not in markdown
    assert "<https://example.com/a_(b)>" in markdown
    assert r"\*\*产品 A\*\*" in markdown
    assert r"\[产品 B\]" in markdown
    assert r"\[pricing\]" in markdown
    assert claim.claim_id not in markdown


def test_upstream_error_report_cannot_be_marked_completed() -> None:
    extractor = StaticArtifactExtractor(ArtifactExtraction())

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-a",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": "Error generating final report: provider failed",
            },
            budget=_budget(),
            extractor=extractor,
            accessed_at=datetime.now(UTC),
        )
    )

    assert bundle.status is RunStatus.PARTIAL
    assert "Error generating" not in bundle.report.markdown


def test_reached_positive_budget_limit_alone_does_not_force_partial() -> None:
    budget = BudgetSnapshot(
        limit=BudgetEstimate(tavily_calls=1),
        spent=BudgetEstimate(tavily_calls=1),
        remaining=BudgetEstimate(),
    )

    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-budget-exhausted",
            scope=_scope(),
            upstream_state={
                "final_report": _publishable_report(title="完整文本报告"),
                "raw_notes": [
                    "--- SOURCE 1: 官方资料 ---\n"
                    "URL: https://example.com/source\n\n"
                    "SUMMARY: 可验证摘要\n"
                    "--------------------\n"
                ],
            },
            budget=budget,
            extractor=StaticArtifactExtractor(ArtifactExtraction()),
        )
    )

    assert bundle.status is RunStatus.COMPLETED
    assert "预算已耗尽，已停止新增外部调用" in bundle.report.limitations


def test_isolated_researcher_failure_forces_partial_and_keeps_other_results() -> None:
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-researcher-failure",
            scope=_scope(),
            upstream_state={
                "final_report": _publishable_report(title="已保留成功研究结果"),
                "raw_notes": [RAW_NOTES],
                "supervisor_messages": [
                    ToolMessage(
                        content=(
                            "Research unit failed (TimeoutError); "
                            "partial results were retained."
                        ),
                        tool_call_id="failed-unit",
                    )
                ],
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(ArtifactExtraction()),
        )
    )

    assert bundle.status is RunStatus.PARTIAL
    assert bundle.sources
    assert any(
        "1 个研究单元失败" in limitation
        for limitation in bundle.report.limitations
    )


def test_empty_extraction_with_evidence_records_explicit_limitation() -> None:
    """提取调用成功但零产出（输出上限自保）时不得静默用占位掩盖。."""
    record = {
        "query": "cursor pricing",
        "status": "success",
        "summary": "真实摘要内容",
        "title": "来源",
        "type": "tavily_search",
        "url": "https://example.org/real",
        "version": 1,
    }
    bundle = asyncio.run(
        materialize_bundle(
            run_id="run-empty-extraction-with-evidence",
            scope=_scope(),
            upstream_state={
                "raw_notes": [RAW_NOTES],
                "final_report": _publishable_report(title="报告"),
                "supervisor_messages": [_evidence_message([record])],
            },
            budget=_budget(),
            extractor=StaticArtifactExtractor(ArtifactExtraction()),
        )
    )

    assert bundle.evidence
    assert any(
        "提取器未返回任何结论或比较" in item
        for item in bundle.report.limitations
    )
    # 覆盖缺口由占位如实呈现；状态语义遵循既有「诚实缺口不强制 partial」设计。
