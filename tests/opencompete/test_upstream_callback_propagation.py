"""上游 Tavily 子调用与 OpenCompete durable 预算的特征测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableConfig, RunnableLambda

import open_deep_research.utils as upstream_utils
from open_deep_research.deep_researcher import execute_tool_safely
from opencompete.budget import BudgetGuard
from opencompete.callbacks import BudgetCallback
from opencompete.errors import BudgetExceededError
from opencompete.extractors import ArtifactExtraction
from opencompete.materialize import materialize_bundle
from opencompete.models import (
    AnalysisLevel,
    BudgetEstimate,
    Claim,
    ClaimStatus,
    Comparison,
    ResearchEntity,
    RunStatus,
    ScopeSpec,
)
from opencompete.persistence.ledger import SqliteExecutionLedger


class _StructuredSummaryModel:
    """让真实 LangChain ChatModel 事件经过结构化输出链。"""

    def __init__(self, route_config: dict[str, Any] | None = None) -> None:
        self._model = FakeListChatModel(responses=["ignored"])
        self._route_config = route_config

    def with_structured_output(self, schema: type[Any]) -> Any:
        def parse(_: Any, config: RunnableConfig) -> Any:
            if self._route_config is not None:
                self._route_config.update(config.get("configurable") or {})
            return schema(
                summary="Alpha 的公开价格是每月 10 美元。",
                key_excerpts="Standard plan: $10/month.",
            )

        return self._model | RunnableLambda(parse)


class _SupportedArtifactExtractor:
    """生成一个原本足以发布 completed 的受支持结论。"""

    async def extract(
        self,
        *,
        scope: ScopeSpec,
        sources: tuple[Any, ...],
        evidence: tuple[Any, ...],
        report_markdown: str,
        config: RunnableConfig | None = None,
    ) -> ArtifactExtraction:
        del sources, report_markdown, config
        claim = Claim(
            text="Alpha 的公开价格是每月 10 美元。",
            entity_id="entity-alpha",
            level=AnalysisLevel.PRODUCT_SERVICE,
            dimension="pricing",
            evidence_ids=(evidence[0].evidence_id,),
            status=ClaimStatus.SUPPORTED,
        )
        comparison = Comparison(
            entity_ids=tuple(entity.entity_id for entity in scope.entities),
            level=AnalysisLevel.PRODUCT_SERVICE,
            dimension="pricing",
            claim_ids=(claim.claim_id,),
            differences=("Alpha 公布了可定位的标准价格。",),
        )
        return ArtifactExtraction(claims=(claim,), comparisons=(comparison,))


def _config(callback: BudgetCallback) -> RunnableConfig:
    return {
        "callbacks": [callback],
        "configurable": {
            "thread_id": "run-callback-propagation",
            "summarization_model": "openai:fake-summary",
            "summarization_model_max_tokens": 8,
            "llm_request_timeout_seconds": 240,
            "llm_sdk_max_retries": 0,
            "tavily_max_results_per_query": 2,
            "max_content_length": 5_000,
            "max_structured_output_retries": 1,
        },
    }


def _scope() -> ScopeSpec:
    return ScopeSpec(
        entities=(
            ResearchEntity(name="Alpha", entity_id="entity-alpha"),
            ResearchEntity(name="Beta", entity_id="entity-beta"),
        ),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing",)},
        confirmed_at=datetime.now(UTC),
        revision=1,
        frozen=True,
    )


@pytest.mark.asyncio
async def test_tavily_summary_model_inherits_runner_budget_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """顶层 Callback 必须同时计入 Tavily 和网页摘要模型调用。"""
    database = tmp_path / "checkpoints.sqlite3"
    ledger = SqliteExecutionLedger(database)
    await ledger.initialize()
    limit = BudgetEstimate(
        input_tokens=100_000,
        output_tokens=100,
        tavily_calls=2,
        cost_usd=1,
    )
    guard = BudgetGuard(ledger, limit, run_id="run-callback-propagation")
    callback = BudgetCallback(guard, default_max_output_tokens=8)

    async def fake_search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return [
            {
                "query": "Alpha pricing",
                "results": [
                    {
                        "url": "https://example.com/pricing",
                        "title": "Alpha official pricing",
                        "content": "Alpha pricing overview",
                        "raw_content": "Standard plan: $10/month.",
                    }
                ],
            }
        ]

    monkeypatch.setattr(upstream_utils, "tavily_search_async", fake_search)
    model_kwargs: dict[str, Any] = {}
    route_config: dict[str, Any] = {}
    search_kwargs: dict[str, Any] = {}

    def fake_init_chat_model(**kwargs: Any) -> _StructuredSummaryModel:
        model_kwargs.update(kwargs)
        return _StructuredSummaryModel(route_config)

    monkeypatch.setattr(upstream_utils, "init_chat_model", fake_init_chat_model)

    original_search = upstream_utils.tavily_search_async

    async def recording_search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        search_kwargs.update(kwargs)
        return await original_search(*args, **kwargs)

    monkeypatch.setattr(upstream_utils, "tavily_search_async", recording_search)

    result = await upstream_utils.tavily_search.ainvoke(
        {"queries": ["Alpha pricing"]},
        config=_config(callback),
    )

    snapshot = await ledger.snapshot("run-callback-propagation", limit)
    assert "Alpha 的公开价格" in result
    assert snapshot.spent.tavily_calls == 1
    assert snapshot.spent.input_tokens > 0
    assert snapshot.spent.output_tokens == 8
    assert snapshot.reserved == BudgetEstimate()
    assert "timeout" in model_kwargs["configurable_fields"]
    assert "max_retries" in model_kwargs["configurable_fields"]
    assert route_config["timeout"] == 240
    assert route_config["max_retries"] == 0
    assert search_kwargs["max_results"] == 2


@pytest.mark.asyncio
async def test_budget_rejection_propagates_before_second_outbound_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发送前预算拒绝必须上抛给正式 Runner，且不能伪装为工具结果。"""
    database = tmp_path / "checkpoints.sqlite3"
    limit = BudgetEstimate(tavily_calls=1)
    ledger = SqliteExecutionLedger(database)
    await ledger.initialize()
    guard = BudgetGuard(ledger, limit, run_id="run-callback-propagation")
    callback = BudgetCallback(guard, default_max_output_tokens=0)
    outbound_queries: list[str] = []

    async def fake_search(
        search_queries: list[str],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del kwargs
        outbound_queries.extend(search_queries)
        return [
            {
                "query": search_queries[0],
                "results": [
                    {
                        "url": "https://example.com/pricing",
                        "title": "Alpha official pricing",
                        "content": "Alpha 的公开价格是每月 10 美元。",
                        "raw_content": None,
                    }
                ],
            }
        ]

    monkeypatch.setattr(upstream_utils, "tavily_search_async", fake_search)
    monkeypatch.setattr(
        upstream_utils,
        "init_chat_model",
        lambda **kwargs: _StructuredSummaryModel(),
    )
    config = _config(callback)

    successful_note = await execute_tool_safely(
        upstream_utils.tavily_search,
        {"queries": ["Alpha pricing"]},
        config,
    )
    with pytest.raises(BudgetExceededError):
        await execute_tool_safely(
            upstream_utils.tavily_search,
            {"queries": ["Beta pricing"]},
            config,
        )

    reopened_ledger = SqliteExecutionLedger(database)
    await reopened_ledger.initialize()
    snapshot = await reopened_ledger.snapshot("run-callback-propagation", limit)
    assert outbound_queries == ["Alpha pricing"]
    assert "--- SOURCE 1" in successful_note, successful_note
    assert snapshot.spent.tavily_calls == 1
    assert snapshot.reserved == BudgetEstimate()
    assert snapshot.remaining.tavily_calls == 0

    bundle = await materialize_bundle(
        run_id="run-callback-propagation",
        scope=_scope(),
        upstream_state={
            "raw_notes": [successful_note],
            # 该测试要隔离“预算耗尽”是否会错误降级，夹具本身必须满足
            # 已有发布门禁的来源与开篇决策要求。
            "final_report": (
                "# Alpha 与 Beta 竞品分析\n\n"
                "## 结论摘要\n\n"
                "建议：当前可核验资料只支持 Alpha 的公开价格，"
                "选择前应继续核实 Beta 的对应套餐。\n\n"
                "Alpha 的公开价格是每月 10 美元。"
                "来源：<https://example.com/pricing>"
            ),
        },
        budget=snapshot,
        extractor=_SupportedArtifactExtractor(),
        accessed_at=datetime.now(UTC),
    )

    # 第二次调用在发送前被拒绝，但首个结果已经闭合当前关键覆盖；
    # REQ-R02 不允许仅因额度耗尽把覆盖完整的报告误标为 partial。
    assert bundle.status is RunStatus.COMPLETED
