from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

import open_deep_research.utils as upstream_utils
from opencompete.budget import BudgetGuard, InMemoryBudgetLedger
from opencompete.callbacks import BudgetCallback
from opencompete.errors import BudgetExceededError
from opencompete.models import BudgetEstimate
from opencompete.settings import OpenCompeteSettings


def _records(output: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in output.splitlines()
        if line.startswith("{")
    ]


def _disable_unused_summary_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upstream_utils, "init_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(
        upstream_utils,
        "build_resilient_model",
        lambda *args, **kwargs: object(),
    )


@pytest.mark.asyncio
async def test_formal_tavily_search_tool_call_returns_bounded_evidence_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_content = "private full webpage body " + "X" * 5000

    class FakeSearchClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key

        async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "query": query,
                "results": [
                    {
                        "url": "https://example.com/pricing",
                        "title": "公开定价",
                        "content": "公开短摘要",
                        "raw_content": raw_content,
                    }
                ],
            }

    class SummaryRunnable:
        async def abatch(
            self,
            inputs: list[str],
            config: dict[str, Any],
        ) -> list[str]:
            del inputs, config
            return ["模型生成的有界摘要"]

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeSearchClient)
    monkeypatch.setattr(upstream_utils, "RunnableLambda", lambda _fn: SummaryRunnable())
    _disable_unused_summary_model(monkeypatch)

    output = await upstream_utils.tavily_search.ainvoke(
        {
            "name": "tavily_search",
            "args": {"queries": ["public pricing"]},
            "id": "search-artifact",
            "type": "tool_call",
        }
    )

    assert isinstance(output, ToolMessage)
    assert output.tool_call_id == "search-artifact"
    assert output.artifact["type"] == "opencompete_research_evidence"
    assert output.artifact["version"] == 1
    assert output.artifact["records"] == [
        {
            "query": "public pricing",
            "status": "success",
            "summary": "模型生成的有界摘要",
            "title": "公开定价",
            "type": "tavily_search",
            "url": "https://example.com/pricing",
            "version": 1,
        }
    ]
    assert raw_content not in repr(output.artifact)


@pytest.mark.asyncio
async def test_formal_tavily_extract_tool_call_returns_artifact_without_raw_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_content = "private full webpage body " + "X" * 500 + " target fact"

    class FakeExtractClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key

        async def extract(self, *, urls: list[str], **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "results": [{"url": urls[0], "raw_content": raw_content}],
                "failed_results": [],
                "request_id": "request-artifact",
                "usage": {"credits": 1},
            }

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeExtractClient)
    output = await upstream_utils.tavily_extract.ainvoke(
        {
            "name": "tavily_extract",
            "args": {
                "urls": ["https://example.com/fact"],
                "query": "target fact",
            },
            "id": "extract-artifact",
            "type": "tool_call",
        },
        config={"configurable": {"tavily_extract_excerpt_max_chars": 200}},
    )

    assert isinstance(output, ToolMessage)
    assert output.tool_call_id == "extract-artifact"
    assert output.artifact["type"] == "opencompete_research_evidence"
    assert output.artifact["version"] == 1
    assert output.artifact["records"][0]["type"] == "tavily_extract"
    assert raw_content not in repr(output.artifact)


@pytest.mark.asyncio
async def test_plain_tavily_arguments_remain_string_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeExtractClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key

        async def extract(self, *, urls: list[str], **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "results": [{"url": urls[0], "raw_content": "目标事实"}],
                "failed_results": [],
                "request_id": "request-plain",
                "usage": {"credits": 1},
            }

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeExtractClient)

    output = await upstream_utils.tavily_extract.ainvoke(
        {"urls": ["https://example.com/plain"], "query": "目标事实"}
    )

    assert isinstance(output, str)
    assert "TAVILY_EXTRACT_JSONL_V1" in output


@pytest.mark.asyncio
async def test_tavily_search_hard_limits_ten_queries_to_three_before_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_queries: list[str] = []
    events: list[dict[str, object]] = []

    class FakeSearchClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key

        async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            outbound_queries.append(query)
            return {
                "query": query,
                "results": [
                    {
                        "url": f"https://example.com/{len(outbound_queries)}",
                        "title": "公开结果",
                        "content": f"{query} 的短搜索摘要",
                        "raw_content": None,
                    }
                ],
            }

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeSearchClient)
    _disable_unused_summary_model(monkeypatch)
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=3),
        run_id="run-query-hard-limit",
        event_sink=events.append,
    )
    callback = BudgetCallback(
        guard,
        default_max_output_tokens=0,
        tavily_max_queries_per_call=9,
    )
    config = OpenCompeteSettings(
        tavily_max_queries_per_call=3,
    ).to_runnable_config("run-query-hard-limit")
    config["callbacks"] = [callback]
    queries = [f"query-{index}" for index in range(10)]

    output = await upstream_utils.tavily_search.ainvoke(
        {"queries": queries},
        config=config,
    )

    assert outbound_queries == queries[:3]
    assert "QUERY_ADMISSION: requested=10; sent=3; truncated=7" in output
    assert "QUERY: query-0" in output
    assert "QUERY: query-2" in output
    assert "QUERY: query-3" not in output
    snapshot = await guard.snapshot()
    assert snapshot.spent.tavily_calls == 3
    assert snapshot.reserved.tavily_calls == 0
    assert [event["tavily_calls"] for event in events] == [3, 3]
    assert [event["event"] for event in events] == [
        "call_attempt_started",
        "call_attempt_succeeded",
    ]


@pytest.mark.asyncio
async def test_tavily_extract_batches_twenty_plus_one_and_keeps_mixed_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = [f"https://example.com/page-{index}" for index in range(21)]
    failed_url = urls[7]
    sentinel = "LATE_EXTRACT_SENTINEL"
    long_content = "A" * 2200 + sentinel + "B" * 500
    calls: list[dict[str, Any]] = []

    class FakeExtractClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key

        async def extract(self, *, urls: list[str], **kwargs: Any) -> dict[str, Any]:
            calls.append({"urls": list(urls), **kwargs})
            return {
                "results": [
                    {
                        "url": url,
                        "raw_content": (
                            long_content
                            if url == "https://example.com/page-0"
                            else f"页面 {url} 包含 {sentinel}。"
                        ),
                    }
                    for url in urls
                    if url != failed_url
                ],
                "failed_results": [
                    {
                        "url": failed_url,
                        "error": "provider secret failure body",
                    }
                ]
                if failed_url in urls
                else [],
                "request_id": f"request-{len(calls)}",
                "usage": {"credits": len(urls)},
            }

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeExtractClient)
    output = await upstream_utils.tavily_extract.ainvoke(
        {
            "urls": urls,
            "query": sentinel,
        },
        config={"configurable": {"tavily_extract_excerpt_max_chars": 400}},
    )

    assert [len(call["urls"]) for call in calls] == [20, 1]
    assert all(call["extract_depth"] == "basic" for call in calls)
    assert all(call["include_images"] is False for call in calls)
    assert all(call["include_usage"] is True for call in calls)
    records = _records(output)
    assert len(records) == 21
    failed = next(record for record in records if record["url"] == failed_url)
    assert failed["status"] == "failed"
    assert failed["error_type"] == "extract_failed"
    assert failed["request_id"] == "request-1"
    assert "provider secret failure body" not in output

    located = next(
        record
        for record in records
        if record["url"] == "https://example.com/page-0"
    )
    assert located["status"] == "success"
    assert sentinel in located["excerpt"]
    assert located["char_start"] > 2000
    assert located["char_end"] - located["char_start"] <= 400
    assert (
        long_content[located["char_start"] : located["char_end"]]
        == located["excerpt"]
    )
    assert located["request_id"] == "request-1"
    assert located["usage"] == {"credits": 20}
    assert long_content not in output


@pytest.mark.asyncio
async def test_extract_twenty_one_urls_cannot_bypass_a_twenty_credit_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_creations: list[str] = []

    class ForbiddenClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key
            client_creations.append("created")

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", ForbiddenClient)
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=20),
        run_id="run-extract-hard-budget",
    )
    callback = BudgetCallback(guard, default_max_output_tokens=0)
    urls = [f"https://example.com/page-{index}" for index in range(21)]

    with pytest.raises(BudgetExceededError):
        await upstream_utils.tavily_extract.ainvoke(
            {"urls": urls, "query": "late fact"},
            config={"callbacks": [callback]},
        )

    assert client_creations == []
    snapshot = await guard.snapshot()
    assert snapshot.spent.tavily_calls == 0
    assert snapshot.reserved.tavily_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extract_depth", "reserved_credits", "actual_credits"),
    (("basic", 2, 1), ("advanced", 4, 2)),
)
async def test_extract_budget_maps_urls_to_conservative_credits_then_uses_usage(
    extract_depth: str,
    reserved_credits: int,
    actual_credits: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, object]] = []
    source_query = "private research query"
    urls = ["https://example.com/a", "https://example.com/b"]
    raw_content = "private raw webpage body"

    class FakeExtractClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key

        async def extract(self, *, urls: list[str], **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "results": [
                    {"url": url, "raw_content": raw_content} for url in urls
                ],
                "failed_results": [],
                "request_id": "request-budget-usage",
                "usage": {"credits": actual_credits},
            }

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeExtractClient)
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=10),
        run_id=f"run-extract-{extract_depth}",
        event_sink=events.append,
    )
    callback = BudgetCallback(guard, default_max_output_tokens=0)

    await upstream_utils.tavily_extract.ainvoke(
        {
            "name": "tavily_extract",
            "args": {
                "urls": urls,
                "query": source_query,
                "extract_depth": extract_depth,
            },
            "id": f"extract-budget-{extract_depth}",
            "type": "tool_call",
        },
        config={"callbacks": [callback]},
    )

    snapshot = await guard.snapshot()
    assert snapshot.reserved.tavily_calls == 0
    assert snapshot.spent.tavily_calls == actual_credits
    assert [event["tavily_calls"] for event in events] == [
        reserved_credits,
        actual_credits,
    ]
    assert source_query not in repr(events)
    assert raw_content not in repr(events)
    assert all(url not in repr(events) for url in urls)


@pytest.mark.asyncio
async def test_extract_missing_usage_keeps_conservative_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeExtractClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key

        async def extract(self, *, urls: list[str], **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "results": [{"url": urls[0], "raw_content": "目标事实"}],
                "failed_results": [],
                "request_id": "request-no-usage",
            }

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeExtractClient)
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=2),
        run_id="run-extract-no-usage",
    )
    callback = BudgetCallback(guard, default_max_output_tokens=0)

    await upstream_utils.tavily_extract.ainvoke(
        {
            "urls": ["https://example.com/no-usage"],
            "query": "目标事实",
            "extract_depth": "advanced",
        },
        config={"callbacks": [callback]},
    )

    snapshot = await guard.snapshot()
    assert snapshot.spent.tavily_calls == 2
    assert snapshot.reserved.tavily_calls == 0


@pytest.mark.asyncio
async def test_tavily_extract_advanced_depth_requires_explicit_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    depths: list[str] = []

    class FakeExtractClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key

        async def extract(self, *, urls: list[str], **kwargs: Any) -> dict[str, Any]:
            depths.append(kwargs["extract_depth"])
            return {
                "results": [{"url": urls[0], "raw_content": "目标事实"}],
                "failed_results": [],
                "request_id": "request-advanced",
                "usage": {"credits": 2},
            }

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeExtractClient)
    await upstream_utils.tavily_extract.ainvoke(
        {
            "urls": ["https://example.com/advanced"],
            "query": "目标事实",
            "extract_depth": "advanced",
        }
    )

    assert depths == ["advanced"]


@pytest.mark.asyncio
async def test_search_only_cannot_see_late_sentinel_but_extract_can_locate_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "ONLY_EXTRACT_CAN_SEE_THIS_SENTINEL"
    full_content = "search-visible " + "X" * 2200 + sentinel + " tail"

    class FakeTavilyClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key

        async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "query": query,
                "results": [
                    {
                        "url": "https://example.com/late-fact",
                        "title": "后段事实",
                        "content": full_content,
                        "raw_content": None,
                    }
                ],
            }

        async def extract(self, *, urls: list[str], **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "results": [{"url": urls[0], "raw_content": full_content}],
                "failed_results": [],
                "request_id": "request-late-fact",
                "usage": {"credits": 1},
            }

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeTavilyClient)
    _disable_unused_summary_model(monkeypatch)
    search_output = await upstream_utils.tavily_search.ainvoke(
        {"queries": ["late fact"]},
    )
    extract_output = await upstream_utils.tavily_extract.ainvoke(
        {
            "urls": ["https://example.com/late-fact"],
            "query": sentinel,
        },
        config={"configurable": {"tavily_extract_excerpt_max_chars": 400}},
    )

    assert sentinel not in search_output
    assert full_content[: upstream_utils.TAVILY_SEARCH_SNIPPET_MAX_CHARS] in search_output
    extract_record = _records(extract_output)[0]
    assert sentinel in extract_record["excerpt"]
    assert extract_record["char_end"] > 2000
    assert (
        full_content[
            extract_record["char_start"] : extract_record["char_end"]
        ]
        == extract_record["excerpt"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    (
        "file:///etc/passwd",
        "http://127.0.0.1/private",
        "http://localhost/private",
        "https://user:password@example.com/private",
        "https://example.com/private?access_token=secret",
    ),
)
async def test_tavily_extract_rejects_non_public_or_secret_urls_before_client(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class ForbiddenClient:
        def __init__(self, api_key: str | None = None) -> None:
            del api_key
            calls.append("created")

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", ForbiddenClient)

    with pytest.raises(ValueError):
        await upstream_utils.tavily_extract.ainvoke(
            {"urls": [url], "query": "target"}
        )

    assert calls == []


@pytest.mark.asyncio
async def test_tavily_extract_redacts_process_secrets_and_never_returns_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "tvly-secret-sentinel-value"
    monkeypatch.setenv("TAVILY_API_KEY", secret)

    class FakeExtractClient:
        def __init__(self, api_key: str | None = None) -> None:
            assert api_key == secret

        async def extract(self, *, urls: list[str], **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "results": [
                    {
                        "url": urls[0],
                        "raw_content": f"目标事实 API key: {secret}",
                    }
                ],
                "failed_results": [],
                "request_id": secret,
                "usage": {"credits": 1, "raw": secret},
            }

    monkeypatch.setattr(upstream_utils, "AsyncTavilyClient", FakeExtractClient)
    output = await upstream_utils.tavily_extract.ainvoke(
        {
            "urls": ["https://example.com/secret-redaction"],
            "query": "目标事实",
        }
    )

    assert secret not in output
    record = _records(output)[0]
    assert record["request_id"] is None
    assert record["usage"] == {"credits": 1}
    assert record["excerpt_redacted"] is True


def test_tavily_limits_enter_upstream_config_and_callback_metadata() -> None:
    settings = OpenCompeteSettings(
        tavily_max_queries_per_call=4,
        tavily_extract_excerpt_max_chars=900,
    )

    config = settings.to_runnable_config("run-tavily-config")

    assert config["configurable"]["tavily_max_queries_per_call"] == 4
    assert config["configurable"]["tavily_extract_excerpt_max_chars"] == 900
    assert config["metadata"]["tavily_max_queries_per_call"] == 4


def test_search_query_line_is_single_line_bounded_and_secret_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "query-secret-sentinel"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    query = f"pricing\napi_key={secret} " + "x" * 2000

    safe_query = upstream_utils._safe_output_query(query)

    assert "\n" not in safe_query
    assert secret not in safe_query
    assert "api_key=[REDACTED]" in safe_query
    assert len(safe_query) <= 1000
