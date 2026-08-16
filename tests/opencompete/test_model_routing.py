"""单次模型调用重试与备用降级测试。"""

from __future__ import annotations

import asyncio
from collections import Counter

import httpx
import pytest
from langchain_core.runnables import RunnableConfig, RunnableLambda

from open_deep_research.model_routing import (
    build_resilient_model,
    route_metadata,
)


def _route_config(
    provider: str,
    model: str,
    *,
    fallback: bool,
) -> RunnableConfig:
    return {
        "metadata": route_metadata(
            provider=provider,
            model=model,
            fallback=fallback,
            route_attempts=2 if fallback else 3,
            primary_attempts=3,
            fallback_attempts=2,
            retry_delays_seconds=(0, 0, 0, 0),
            fallback_provider="DeepSeek" if not fallback else None,
            fallback_model="anthropic:fallback" if not fallback else None,
        )
    }


@pytest.mark.asyncio
async def test_three_primary_then_two_fallback_attempts() -> None:
    calls: list[tuple[str, str, bool, tuple[str, ...]]] = []

    async def invoke(value: str, config: RunnableConfig) -> str:
        metadata = config.get("metadata") or {}
        provider = str(metadata["opencompete_provider"])
        model = str(metadata["opencompete_model"])
        fallback = bool(metadata["opencompete_fallback"])
        calls.append((provider, model, fallback, tuple(config.get("tags") or ())))
        if len(calls) < 5:
            raise httpx.ReadTimeout("temporary read timeout")
        return value

    model = build_resilient_model(
        RunnableLambda(invoke),
        prepare=lambda runnable: runnable,
        primary_config=_route_config("OpenAI", "openai:primary", fallback=False),
        primary_attempts=3,
        fallback_config=_route_config(
            "DeepSeek",
            "anthropic:fallback",
            fallback=True,
        ),
        fallback_attempts=2,
        retry_delays_seconds=(0, 0, 0, 0),
    )

    assert await model.ainvoke("完成") == "完成"
    assert [(provider, fallback) for provider, _, fallback, _ in calls] == [
        ("OpenAI", False),
        ("OpenAI", False),
        ("OpenAI", False),
        ("DeepSeek", True),
        ("DeepSeek", True),
    ]


@pytest.mark.asyncio
async def test_permanent_error_stops_without_retry_or_fallback() -> None:
    calls = 0

    async def invoke(_value: str, config: RunnableConfig) -> str:
        del config
        nonlocal calls
        calls += 1
        raise ValueError("invalid request")

    model = build_resilient_model(
        RunnableLambda(invoke),
        prepare=lambda runnable: runnable,
        primary_config=_route_config("OpenAI", "openai:primary", fallback=False),
        primary_attempts=3,
        fallback_config=_route_config(
            "DeepSeek",
            "anthropic:fallback",
            fallback=True,
        ),
        fallback_attempts=2,
        retry_delays_seconds=(0, 0, 0, 0),
    )

    with pytest.raises(ValueError, match="invalid request"):
        await model.ainvoke("失败")
    assert calls == 1


@pytest.mark.asyncio
async def test_parallel_successful_sibling_is_not_replayed() -> None:
    calls: Counter[str] = Counter()

    async def invoke(value: str, config: RunnableConfig) -> str:
        del config
        calls[value] += 1
        if value == "temporary" and calls[value] == 1:
            raise httpx.ConnectError("temporary connection error")
        return value

    model = build_resilient_model(
        RunnableLambda(invoke),
        prepare=lambda runnable: runnable,
        primary_config=_route_config("OpenAI", "openai:primary", fallback=False),
        primary_attempts=3,
        fallback_config=_route_config(
            "DeepSeek",
            "anthropic:fallback",
            fallback=True,
        ),
        fallback_attempts=2,
        retry_delays_seconds=(0, 0, 0, 0),
    )

    results = await asyncio.gather(
        model.ainvoke("temporary"),
        model.ainvoke("successful"),
    )

    assert results == ["temporary", "successful"]
    assert calls == Counter({"temporary": 2, "successful": 1})


def test_more_than_five_attempts_is_rejected() -> None:
    with pytest.raises(ValueError, match="不能超过五次"):
        build_resilient_model(
            RunnableLambda(lambda value: value),
            prepare=lambda runnable: runnable,
            primary_config=_route_config(
                "OpenAI",
                "openai:primary",
                fallback=False,
            ),
            primary_attempts=4,
            fallback_config=_route_config(
                "DeepSeek",
                "anthropic:fallback",
                fallback=True,
            ),
            fallback_attempts=2,
            retry_delays_seconds=(0, 0, 0, 0, 0),
        )
