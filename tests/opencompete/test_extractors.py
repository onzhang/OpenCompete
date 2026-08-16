"""结构化 Artifact Extractor 契约测试。"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

import opencompete.extractors as extractor_module
from opencompete.extractors import (
    ArtifactExtraction,
    LangChainArtifactExtractor,
    StaticArtifactExtractor,
)
from opencompete.models import (
    AnalysisLevel,
    Claim,
    ClaimStatus,
    Comparison,
    Evidence,
    ResearchEntity,
    ScopeSpec,
    Source,
)


def test_static_extractor_returns_frozen_structured_result() -> None:
    entity = ResearchEntity(name="产品 A")
    source = Source(
        url="https://example.com/a",
        title="产品 A",
        accessed_at=datetime.now(UTC),
    )
    evidence = Evidence(source_id=source.source_id, excerpt="价格为 10 美元")
    claim = Claim(
        text="产品 A 的价格为 10 美元",
        entity_id=entity.entity_id,
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        evidence_ids=(evidence.evidence_id,),
        status=ClaimStatus.SUPPORTED,
    )
    comparison = Comparison(
        entity_ids=(entity.entity_id,),
        level=AnalysisLevel.PRODUCT_SERVICE,
        dimension="pricing",
        claim_ids=(claim.claim_id,),
    )
    expected = ArtifactExtraction(claims=(claim,), comparisons=(comparison,))
    extractor = StaticArtifactExtractor(expected)
    scope = ScopeSpec(
        entities=(entity,),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing",)},
        confirmed_at=datetime.now(UTC),
        revision=1,
        frozen=True,
    )

    result = asyncio.run(
        extractor.extract(
            scope=scope,
            sources=(source,),
            evidence=(evidence,),
            report_markdown="# 报告",
        )
    )

    assert result == expected


@pytest.mark.asyncio
async def test_langchain_extractor_disables_sdk_retries_and_sets_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, Any] = {}

    class FakeStructuredModel:
        def with_config(self, config: dict[str, Any]) -> "FakeStructuredModel":
            recorded["model_config"] = config
            return self

        def with_retry(self, **kwargs: Any) -> "FakeStructuredModel":
            recorded["retry_options"] = kwargs
            return self

        async def ainvoke(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {}

    class FakeModel:
        def with_structured_output(self, schema: type[Any]) -> FakeStructuredModel:
            recorded["schema"] = schema
            return FakeStructuredModel()

    def fake_init_chat_model(**kwargs: Any) -> FakeModel:
        recorded["init_kwargs"] = kwargs
        return FakeModel()

    monkeypatch.setattr(extractor_module, "init_chat_model", fake_init_chat_model)
    entity = ResearchEntity(name="产品 A")
    scope = ScopeSpec(
        entities=(entity,),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing",)},
        confirmed_at=datetime.now(UTC),
        revision=1,
        frozen=True,
    )
    extractor = LangChainArtifactExtractor(
        "openai:test-model",
        max_tokens=1234,
        max_retries=2,
        request_timeout_seconds=240,
        sdk_max_retries=0,
    )

    await extractor.extract(
        scope=scope,
        sources=(),
        evidence=(),
        report_markdown="# 报告",
    )

    assert recorded["model_config"]["model"] == "openai:test-model"
    assert recorded["model_config"]["max_tokens"] == 1234
    assert recorded["model_config"]["timeout"] == 240.0
    assert recorded["model_config"]["max_retries"] == 0
    assert recorded["retry_options"]["stop_after_attempt"] == 2


@pytest.mark.asyncio
async def test_langchain_extractor_normalizes_invalid_unknown_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity = ResearchEntity(name="产品 A")
    source = Source(
        url="https://example.com/a",
        title="产品 A",
        accessed_at=datetime.now(UTC),
    )
    evidence = Evidence(source_id=source.source_id, excerpt="暂无公开价格")

    class FakeStructuredModel:
        def with_config(self, _config: dict[str, Any]) -> "FakeStructuredModel":
            return self

        def with_retry(self, **_kwargs: Any) -> "FakeStructuredModel":
            return self

        async def ainvoke(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "claims": [
                    {
                        "text": "产品 A 的企业价格未知",
                        "entity_id": entity.entity_id,
                        "level": "product_service",
                        "dimension": "pricing",
                        "evidence_ids": [evidence.evidence_id],
                        "status": "unknown",
                        "uncertainty": "官网要求联系销售",
                    }
                ]
            }

    class FakeModel:
        def with_structured_output(self, _schema: type[Any]) -> FakeStructuredModel:
            return FakeStructuredModel()

    monkeypatch.setattr(
        extractor_module,
        "init_chat_model",
        lambda **_kwargs: FakeModel(),
    )
    scope = ScopeSpec(
        entities=(entity,),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing",)},
        confirmed_at=datetime.now(UTC),
        revision=1,
        frozen=True,
    )

    result = await LangChainArtifactExtractor("openai:test").extract(
        scope=scope,
        sources=(source,),
        evidence=(evidence,),
        report_markdown="# 报告",
    )

    assert result.claims[0].status is ClaimStatus.UNKNOWN
    assert result.claims[0].evidence_ids == ()
    assert "模型候选中的 Unknown 证据引用已清除" in result.limitations


@pytest.mark.asyncio
async def test_langchain_extractor_drops_unknown_comparison_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型在 Comparison 上发明 Schema 之外字段（如 commonality_gap_note）
    时应丢弃该字段继续提取，而不是让整个任务失败。."""
    entity = ResearchEntity(name="产品 A")
    source = Source(
        url="https://example.com/a",
        title="产品 A",
        accessed_at=datetime.now(UTC),
    )
    evidence = Evidence(source_id=source.source_id, excerpt="产品 A 支持离线模式")

    class FakeStructuredModel:
        def with_config(self, _config: dict[str, Any]) -> "FakeStructuredModel":
            return self

        def with_retry(self, **_kwargs: Any) -> "FakeStructuredModel":
            return self

        async def ainvoke(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "claims": [
                    {
                        "text": "产品 A 支持离线模式",
                        "entity_id": entity.entity_id,
                        "level": "product_service",
                        "dimension": "features",
                        "evidence_ids": [evidence.evidence_id],
                        "status": "supported",
                    }
                ],
                "comparisons": [
                    {
                        "entity_ids": [entity.entity_id],
                        "level": "product_service",
                        "dimension": "features",
                        "claim_ids": [],
                        "differences": ["离线能力不同"],
                        # 模型幻觉字段：Schema 未定义，修复前会让整单校验失败
                        "commonality_gap_note": "",
                    }
                ],
            }

    class FakeModel:
        def with_structured_output(self, _schema: type[Any]) -> FakeStructuredModel:
            return FakeStructuredModel()

    monkeypatch.setattr(
        extractor_module,
        "init_chat_model",
        lambda **_kwargs: FakeModel(),
    )
    scope = ScopeSpec(
        entities=(entity,),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("features",)},
        confirmed_at=datetime.now(UTC),
        revision=1,
        frozen=True,
    )

    result = await LangChainArtifactExtractor("openai:test").extract(
        scope=scope,
        sources=(source,),
        evidence=(evidence,),
        report_markdown="# 报告",
    )

    assert len(result.comparisons) == 1
    assert result.comparisons[0].differences == ("离线能力不同",)
    assert not hasattr(result.comparisons[0], "commonality_gap_note")
    assert result.comparisons[0].comparison_id.startswith("cmp")
