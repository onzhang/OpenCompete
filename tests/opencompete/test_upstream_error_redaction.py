from __future__ import annotations

import logging

import httpx
import pytest
from langchain_core.runnables import RunnableLambda

from open_deep_research import deep_researcher as deep_module
from open_deep_research.deep_researcher import (
    execute_tool_safely,
    final_report_generation,
)
from open_deep_research.prompts import final_report_generation_prompt
from open_deep_research.utils import (
    TAVILY_SUMMARY_FAILURE_MARKER,
    summarize_webpage,
)

_SECRET = "sk-upstream-secret-sentinel"
_AUTH_ERROR = f"Authorization: Bearer {_SECRET}"


class _FailingRunnable:
    def with_config(self, config):
        del config
        return self

    def with_retry(self, **kwargs):
        del kwargs
        return self

    async def ainvoke(self, value, config=None):
        del value, config
        raise RuntimeError(_AUTH_ERROR)


def test_final_report_findings_keep_head_and_sources_tail_per_unit() -> None:
    first = "FIRST-HEAD" + ("a" * 200) + "FIRST-SOURCES"
    second = "SECOND-HEAD" + ("b" * 200) + "SECOND-SOURCES"

    bounded = deep_module._bounded_findings([first, second], 160)

    assert len(bounded) <= 160
    assert "FIRST-HEAD" in bounded
    assert "FIRST-SOURCES" in bounded
    assert "SECOND-HEAD" in bounded
    assert "SECOND-SOURCES" in bounded


def test_final_report_character_limit_tracks_configured_tokens() -> None:
    assert deep_module._final_report_character_limit(1200) == 3600
    assert deep_module._final_report_character_limit(100) == 1000


def test_final_report_prompt_requires_one_consistent_heading_hierarchy() -> None:
    assert "Use exactly one H1" in final_report_generation_prompt
    assert "2.1, 2.2, and 2.3 must all use H3" in final_report_generation_prompt
    assert "Do not repeat the report title" in final_report_generation_prompt


class _TransientThenSuccessfulRunnable:
    def __init__(self) -> None:
        self.calls = 0

    def with_config(self, config):
        del config
        return self

    def with_retry(self, **kwargs):
        return RunnableLambda(self.ainvoke).with_retry(**kwargs)

    async def ainvoke(self, value, config=None):
        del value, config
        self.calls += 1
        if self.calls == 1:
            raise httpx.ReadTimeout("response details must stay private")
        return type("Response", (), {"content": "完整报告"})()


@pytest.mark.asyncio
async def test_tool_error_does_not_persist_provider_exception_text() -> None:
    result = await execute_tool_safely(_FailingRunnable(), {}, {})

    assert result == "Error executing tool (RuntimeError)."
    assert _SECRET not in result
    assert "Authorization" not in result


@pytest.mark.asyncio
async def test_final_report_error_propagates_without_creating_graph_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deep_module, "configurable_model", _FailingRunnable())

    with pytest.raises(RuntimeError):
        await final_report_generation(
            {"notes": ["已有资料"], "messages": []},
            {"configurable": {"final_report_model": "openai:gpt-4.1"}},
        )


@pytest.mark.asyncio
async def test_final_report_retries_transient_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _TransientThenSuccessfulRunnable()
    monkeypatch.setattr(deep_module, "configurable_model", model)

    result = await final_report_generation(
        {"notes": ["已有资料"], "messages": []},
        {
            "configurable": {
                "final_report_model": "openai:gpt-4.1",
                "model_retry_delays_seconds": (0, 0, 0, 0),
            }
        },
    )

    assert result["final_report"] == "完整报告"
    assert model.calls == 2


@pytest.mark.asyncio
async def test_summary_log_does_not_emit_provider_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        result = await summarize_webpage(_FailingRunnable(), "网页正文")

    assert result == TAVILY_SUMMARY_FAILURE_MARKER
    assert "网页正文" not in result
    assert "RuntimeError" in caplog.text
    assert _SECRET not in caplog.text
    assert "Authorization" not in caplog.text
