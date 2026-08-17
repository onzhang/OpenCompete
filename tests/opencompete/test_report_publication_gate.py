"""发布门禁与 partial/fallback 发布路径。."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from opencompete.extractors import ArtifactExtraction, StaticArtifactExtractor
from opencompete.materialize import materialize_bundle
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
from opencompete.report_publication_gate import (
    DECISION_SUMMARY_MISSING,
    EXCESSIVE_REPETITION,
    INTERNAL_SCHEMA_LEAK,
    LANGUAGE_CONSISTENCY_FAILED,
    LITERAL_UNKNOWN_LEAK,
    MISSING_RESOLVABLE_SOURCES,
    evaluate_report_publication_gate,
    resolve_published_report,
)

BAD_REPORT = """
# product_service 审计报告

capabilities 与 user_experience 逐格覆盖如下。

冻结单元 clm_abcdef123456 状态 ClaimStatus=Unknown。

Unknown
Unknown
Unknown

本节重复。
本节重复。
本节重复。本节重复。本节重复。本节重复。

## 覆盖
### 覆盖
### 覆盖
### 覆盖

无任何外部链接。
Only English internal enums capabilities user_experience technology_integrations service_support product_service without Chinese body text enough.
""".strip()


def test_publication_gate_flags_known_bad_fixture_codes() -> None:
    result = evaluate_report_publication_gate(BAD_REPORT, output_language="zh-CN")
    assert result.ok is False
    codes = set(result.error_codes)
    assert INTERNAL_SCHEMA_LEAK in codes
    assert LITERAL_UNKNOWN_LEAK in codes
    assert MISSING_RESOLVABLE_SOURCES in codes
    assert DECISION_SUMMARY_MISSING in codes
    assert EXCESSIVE_REPETITION in codes
    assert LANGUAGE_CONSISTENCY_FAILED in codes or INTERNAL_SCHEMA_LEAK in codes


def test_missing_sources_only_forces_partial_fallback() -> None:
    """仅缺可点击来源也必须硬失败并走 fallback。."""
    candidate = (
        "# 流媒体比较\n\n"
        "## 结论摘要\n\n"
        "建议：单人用户可优先第一家服务，家庭用户再看设备绑定。\n\n"
        "文中没有给出任何可点击官方链接。\n"
    )
    fallback = (
        "# 保底\n\n建议：请自行核验价格。\n\n"
        "来源：<https://example.com/pricing>\n"
    )
    gate = evaluate_report_publication_gate(candidate)
    assert gate.ok is False
    assert MISSING_RESOLVABLE_SOURCES in gate.error_codes
    resolution = resolve_published_report(
        candidate=candidate,
        fallback_markdown=fallback,
    )
    assert resolution.partial is True
    assert resolution.used_fallback is True
    assert MISSING_RESOLVABLE_SOURCES in resolution.error_codes
    assert resolution.markdown.strip() == fallback.strip()


def test_missing_decision_summary_only_forces_partial_fallback() -> None:
    """仅缺开篇决策摘要也必须硬失败并走 fallback。."""
    candidate = (
        "# 背景资料\n\n"
        "本文罗列了两家服务的大量历史资料、页面摘录和产品年表，"
        "正文只堆叠事实条目与时间线，开篇没有给出谁更值得、"
        "何种用户场景下应偏向哪一方的判断。\n\n"
        "详情：<https://example.com/docs>\n"
    )
    fallback = (
        "# 保底\n\n建议：证据不足时不要切换。\n\n"
        "来源：<https://example.com/docs>\n"
    )
    gate = evaluate_report_publication_gate(candidate)
    assert gate.ok is False
    assert DECISION_SUMMARY_MISSING in gate.error_codes
    resolution = resolve_published_report(
        candidate=candidate,
        fallback_markdown=fallback,
    )
    assert resolution.partial is True
    assert resolution.used_fallback is True
    assert DECISION_SUMMARY_MISSING in resolution.error_codes


def test_resolve_published_report_uses_fallback_after_failed_repair() -> None:
    fallback = (
        "# 选择建议\n\n"
        "建议普通用户优先考虑服务 A。\n\n"
        "价格见官网 <https://example.com/pricing>。\n"
    )
    resolution = resolve_published_report(
        candidate=BAD_REPORT,
        fallback_markdown=fallback,
        repaired_candidate=BAD_REPORT,
    )
    assert resolution.partial is True
    assert resolution.used_fallback is True
    assert resolution.repair_attempted is True
    assert resolution.markdown.strip() == fallback.strip()
    assert INTERNAL_SCHEMA_LEAK in resolution.error_codes
    assert "product_service" not in resolution.markdown


def test_resolve_accepts_clean_candidate() -> None:
    clean = (
        "# 流媒体服务比较\n\n"
        "## 结论摘要\n\n"
        "建议：单人用户若重视个性化推荐历史可优先第一家服务；"
        "已经深度绑定某品牌设备与家庭共享的用户可优先第二家服务。"
        "不建议在未完成歌单抽样迁移测试前立即取消原订阅。\n\n"
        "价格与资格请核验官方说明："
        "<https://www.spotify.com/us/premium/>。\n"
    )
    result = evaluate_report_publication_gate(clean)
    assert result.ok, result.error_codes
    resolution = resolve_published_report(
        candidate=clean,
        fallback_markdown="fallback",
    )
    assert resolution.partial is False
    assert resolution.used_fallback is False
    assert resolution.markdown == clean.strip()


@pytest.mark.asyncio
async def test_materialize_publishes_fallback_when_candidate_fails_gate() -> None:
    """真实 materialize_bundle：坏候选不得作为发布正文。."""
    entities = (
        ResearchEntity(name="甲服务"),
        ResearchEntity(name="乙服务"),
    )
    scope = ScopeSpec(
        entities=entities,
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("定价",)},
        confirmed_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        revision=1,
        frozen=True,
    )
    claim = Claim(
        claim_id="clm-pub",
        text="甲服务个人套餐月费 10 美元",
        entity_id=scope.entities[0].entity_id,
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="定价",
        evidence_ids=(),
        status=ClaimStatus.UNKNOWN,
        uncertainty="需核验官网现价",
    )
    comparison = Comparison(
        comparison_id="cmp-pub",
        entity_ids=tuple(e.entity_id for e in scope.entities),
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="定价",
        claim_ids=(claim.claim_id,),
        differences=("价格口径待核实",),
    )
    spent = BudgetEstimate(
        input_tokens=10, output_tokens=10, tavily_calls=0, cost_usd=0.01
    )
    limit = BudgetEstimate(
        input_tokens=100, output_tokens=100, tavily_calls=5, cost_usd=1.0
    )
    budget = BudgetSnapshot(
        limit=limit,
        spent=spent,
        remaining=BudgetEstimate(
            input_tokens=90, output_tokens=90, tavily_calls=5, cost_usd=0.99
        ),
    )
    note = (
        '{"type":"tavily_search","url":"https://example.com/pricing",'
        '"title":"定价页","content":"月费 10 美元"}\n'
    )
    bundle = await materialize_bundle(
        run_id="run-pub-gate",
        scope=scope,
        upstream_state={
            "final_report": BAD_REPORT,
            "notes": [note],
            "raw_notes": [note],
        },
        budget=budget,
        extractor=StaticArtifactExtractor(
            ArtifactExtraction(claims=(claim,), comparisons=(comparison,))
        ),
        config={"configurable": {"thread_id": "run-pub-gate"}},
    )
    assert bundle.status is RunStatus.PARTIAL
    assert "product_service" not in bundle.report.markdown
    assert "clm_abcdef" not in bundle.report.markdown
    assert BAD_REPORT not in bundle.report.markdown
    assert "建议" in bundle.report.markdown
    # 源码接线存在
    materialize_src = Path("src/opencompete/materialize.py").read_text(encoding="utf-8")
    assert "resolve_published_report" in materialize_src
