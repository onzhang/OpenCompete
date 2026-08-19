import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain_core.callbacks import AsyncCallbackManager
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult, Generation, LLMResult
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import PrivateAttr

from opencompete.budget import (
    BudgetGuard,
    BudgetReservation,
    InMemoryBudgetLedger,
)
from opencompete.callbacks import BudgetCallback
from opencompete.errors import BudgetExceededError
from opencompete.models import BudgetEstimate


def make_guard(limit: BudgetEstimate) -> BudgetGuard:
    return BudgetGuard(InMemoryBudgetLedger(), limit=limit, run_id="run-1")


class RecordingLedger(InMemoryBudgetLedger):
    def __init__(self) -> None:
        super().__init__()
        self.accepted: list[BudgetReservation] = []

    async def try_reserve(
        self,
        reservation: BudgetReservation,
        limit: BudgetEstimate,
    ) -> BudgetReservation | None:
        accepted = await super().try_reserve(reservation, limit)
        if accepted is not None:
            self.accepted.append(accepted)
        return accepted


@pytest.mark.asyncio
async def test_llm_callback_reserves_input_and_maximum_output_then_settles_actual() -> None:
    guard = make_guard(
        BudgetEstimate(input_tokens=1_000, output_tokens=100, cost_usd=1)
    )
    callback = BudgetCallback(
        guard,
        default_max_output_tokens=20,
        llm_input_cost_per_million_usd=2,
        llm_output_cost_per_million_usd=8,
    )
    run_id = uuid4()

    await callback.on_llm_start(
        {"name": "fake-model"},
        ["This prompt contains enough text to consume input tokens."],
        run_id=run_id,
        metadata={"opencompete_call_id": "model-call"},
        invocation_params={"max_tokens": 15},
    )

    reserved = await guard.snapshot()
    assert reserved.reserved.input_tokens > 0
    assert reserved.reserved.output_tokens == 15
    assert reserved.reserved.cost_usd == pytest.approx(
        reserved.reserved.input_tokens * 2 / 1_000_000 + 15 * 8 / 1_000_000
    )

    await callback.on_llm_end(
        LLMResult(
            generations=[[Generation(text="answer")]],
            llm_output={
                "token_usage": {"prompt_tokens": 7, "completion_tokens": 4}
            },
        ),
        run_id=run_id,
    )

    settled = await guard.snapshot()
    assert settled.reserved.input_tokens == 0
    assert settled.spent.input_tokens == 7
    assert settled.spent.output_tokens == 4
    assert settled.spent.cost_usd == pytest.approx(
        7 * 2 / 1_000_000 + 4 * 8 / 1_000_000
    )


@pytest.mark.asyncio
async def test_llm_callback_reads_openai_max_completion_tokens() -> None:
    guard = make_guard(BudgetEstimate(input_tokens=1_000, output_tokens=100))
    callback = BudgetCallback(guard, default_max_output_tokens=99)
    run_id = uuid4()

    await callback.on_llm_start(
        {},
        ["prompt"],
        run_id=run_id,
        invocation_params={"max_completion_tokens": 17},
    )

    assert (await guard.snapshot()).reserved.output_tokens == 17


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("usage", "reported_input", "reported_output"),
    [
        ({"prompt_tokens": 7}, 7, None),
        ({"completion_tokens": 4}, None, 4),
    ],
)
async def test_llm_callback_keeps_reserved_side_when_usage_is_partial(
    usage: dict[str, int],
    reported_input: int | None,
    reported_output: int | None,
) -> None:
    guard = make_guard(
        BudgetEstimate(input_tokens=1_000, output_tokens=100, cost_usd=1)
    )
    callback = BudgetCallback(
        guard,
        default_max_output_tokens=20,
        llm_input_cost_per_million_usd=2,
        llm_output_cost_per_million_usd=8,
    )
    run_id = uuid4()

    await callback.on_llm_start(
        {},
        ["This prompt reserves a non-zero input estimate."],
        run_id=run_id,
        invocation_params={"max_tokens": 15},
    )
    before = await guard.snapshot()

    await callback.on_llm_end(
        LLMResult(
            generations=[[Generation(text="answer")]],
            llm_output={"token_usage": usage},
        ),
        run_id=run_id,
    )

    expected_input = (
        before.reserved.input_tokens
        if reported_input is None
        else reported_input
    )
    expected_output = (
        before.reserved.output_tokens
        if reported_output is None
        else reported_output
    )
    after = await guard.snapshot()
    assert after.reserved == BudgetEstimate()
    assert after.spent.input_tokens == expected_input
    assert after.spent.output_tokens == expected_output
    assert after.spent.cost_usd == pytest.approx(
        expected_input * 2 / 1_000_000 + expected_output * 8 / 1_000_000
    )


@pytest.mark.asyncio
async def test_llm_callback_keeps_reservation_when_actual_usage_is_unknown() -> None:
    guard = make_guard(
        BudgetEstimate(input_tokens=1_000, output_tokens=100, cost_usd=1)
    )
    callback = BudgetCallback(
        guard,
        default_max_output_tokens=12,
        llm_input_cost_per_million_usd=2,
        llm_output_cost_per_million_usd=8,
    )
    run_id = uuid4()

    await callback.on_llm_start({}, ["short prompt"], run_id=run_id)
    before = await guard.snapshot()
    await callback.on_llm_end(
        LLMResult(generations=[[Generation(text="answer")]]),
        run_id=run_id,
    )

    after = await guard.snapshot()
    assert after.reserved == BudgetEstimate()
    assert after.spent == before.reserved


@pytest.mark.asyncio
async def test_tavily_tool_reserves_one_call_per_query() -> None:
    guard = make_guard(BudgetEstimate(tavily_calls=3, cost_usd=0.06))
    callback = BudgetCallback(
        guard,
        default_max_output_tokens=0,
        tavily_cost_per_call_usd=0.02,
    )
    run_id = uuid4()

    await callback.on_tool_start(
        {"name": "tavily_search"},
        '{"queries": ["first", "second"]}',
        run_id=run_id,
        inputs={"queries": ["first", "second"]},
        metadata={"opencompete_call_id": "search-call"},
    )
    assert (await guard.snapshot()).reserved.tavily_calls == 2
    assert (await guard.snapshot()).reserved.cost_usd == pytest.approx(0.04)

    await callback.on_tool_end("results", run_id=run_id)
    snapshot = await guard.snapshot()
    assert snapshot.reserved.tavily_calls == 0
    assert snapshot.spent.tavily_calls == 2
    assert snapshot.spent.cost_usd == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_extract_tool_message_artifact_settles_reported_usage() -> None:
    guard = make_guard(BudgetEstimate(tavily_calls=4))
    callback = BudgetCallback(guard, default_max_output_tokens=0)
    run_id = uuid4()
    records = [
        {
            "batch_index": 1,
            "query": "target",
            "request_id": "request-usage",
            "status": "success",
            "type": "tavily_extract",
            "url": "https://example.com/a",
            "usage": {"credits": 2},
            "version": 1,
        },
        {
            "batch_index": 1,
            "query": "target",
            "request_id": "request-usage",
            "status": "success",
            "type": "tavily_extract",
            "url": "https://example.com/b",
            "usage": {"credits": 2},
            "version": 1,
        },
    ]

    await callback.on_tool_start(
        {"name": "tavily_extract"},
        "",
        run_id=run_id,
        inputs={
            "urls": ["https://example.com/a", "https://example.com/b"],
            "query": "target",
            "extract_depth": "advanced",
        },
    )
    await callback.on_tool_end(
        ToolMessage(
            content="bounded extract output",
            name="tavily_extract",
            tool_call_id="extract-usage",
            artifact={
                "type": "opencompete_research_evidence",
                "version": 1,
                "records": records,
            },
        ),
        run_id=run_id,
    )

    snapshot = await guard.snapshot()
    assert snapshot.reserved.tavily_calls == 0
    assert snapshot.spent.tavily_calls == 2


@pytest.mark.asyncio
async def test_cost_limit_allows_only_one_concurrent_tavily_request_to_start() -> None:
    guard = make_guard(BudgetEstimate(tavily_calls=2, cost_usd=0.02))
    callback = BudgetCallback(
        guard,
        default_max_output_tokens=0,
        tavily_cost_per_call_usd=0.02,
    )
    outbound_requests: list[str] = []

    async def invoke(query: str) -> None:
        await callback.on_tool_start(
            {"name": "tavily_search"},
            "",
            run_id=uuid4(),
            inputs={"queries": [query]},
        )
        outbound_requests.append(query)

    results = await asyncio.gather(
        invoke("first"),
        invoke("second"),
        return_exceptions=True,
    )

    assert sum(isinstance(result, BudgetExceededError) for result in results) == 1
    assert len(outbound_requests) == 1
    snapshot = await guard.snapshot()
    assert snapshot.reserved.cost_usd == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_non_tavily_tool_does_not_consume_search_budget() -> None:
    guard = make_guard(BudgetEstimate(tavily_calls=1))
    callback = BudgetCallback(guard, default_max_output_tokens=0)

    await callback.on_tool_start(
        {"name": "think_tool"},
        "reflection",
        run_id=uuid4(),
    )

    assert (await guard.snapshot()).remaining.tavily_calls == 1


@pytest.mark.asyncio
async def test_budget_rejection_propagates_before_an_llm_request() -> None:
    guard = make_guard(BudgetEstimate(input_tokens=0, output_tokens=0))
    callback = BudgetCallback(guard, default_max_output_tokens=1)
    manager = AsyncCallbackManager([callback])
    outbound_requests: list[str] = []

    async def invoke() -> None:
        await manager.on_llm_start(
            {"name": "fake-model"},
            ["request"],
            run_id=uuid4(),
        )
        outbound_requests.append("sent")

    assert callback.raise_error is True
    with pytest.raises(BudgetExceededError):
        await invoke()
    assert outbound_requests == []


@pytest.mark.asyncio
async def test_cost_rejection_propagates_before_an_llm_request() -> None:
    guard = make_guard(
        BudgetEstimate(input_tokens=10_000, output_tokens=10_000, cost_usd=0.01)
    )
    callback = BudgetCallback(
        guard,
        default_max_output_tokens=2_000,
        llm_input_cost_per_million_usd=2,
        llm_output_cost_per_million_usd=8,
    )
    manager = AsyncCallbackManager([callback])
    outbound_requests: list[str] = []

    async def invoke() -> None:
        await manager.on_llm_start(
            {"name": "fake-model"},
            ["request"],
            run_id=uuid4(),
        )
        outbound_requests.append("sent")

    with pytest.raises(BudgetExceededError):
        await invoke()
    assert outbound_requests == []


@pytest.mark.asyncio
async def test_rebuilt_callback_derives_stable_ids_from_the_same_model_input() -> None:
    ledger = RecordingLedger()
    guard = BudgetGuard(
        ledger,
        BudgetEstimate(input_tokens=1_000, output_tokens=100),
        run_id="run-1",
    )
    first_callback = BudgetCallback(guard, default_max_output_tokens=10)
    second_callback = BudgetCallback(guard, default_max_output_tokens=10)
    first_run = uuid4()
    second_run = uuid4()

    await first_callback.on_llm_start({}, ["same input"], run_id=first_run)
    await first_callback.on_llm_end(
        LLMResult(generations=[[Generation(text="first")]]),
        run_id=first_run,
    )
    await second_callback.on_llm_start({}, ["same input"], run_id=second_run)

    assert ledger.accepted[0].call_id == ledger.accepted[1].call_id
    assert ledger.accepted[1].replayed is False


@pytest.mark.asyncio
async def test_model_input_fingerprint_changes_the_logical_call_id() -> None:
    ledger = RecordingLedger()
    guard = BudgetGuard(
        ledger,
        BudgetEstimate(input_tokens=1_000, output_tokens=100),
        run_id="run-1",
    )
    callback = BudgetCallback(guard, default_max_output_tokens=10)
    first_run = uuid4()
    second_run = uuid4()

    await callback.on_llm_start({}, ["first input"], run_id=first_run)
    await callback.on_llm_end(
        LLMResult(generations=[[Generation(text="first")]]),
        run_id=first_run,
    )
    await callback.on_llm_start({}, ["second input"], run_id=second_run)

    assert ledger.accepted[0].call_id != ledger.accepted[1].call_id


@pytest.mark.asyncio
async def test_rebuilt_callback_normalizes_tavily_queries_for_stable_ids() -> None:
    ledger = RecordingLedger()
    guard = BudgetGuard(
        ledger,
        BudgetEstimate(tavily_calls=5),
        run_id="run-1",
    )
    first_callback = BudgetCallback(guard, default_max_output_tokens=0)
    second_callback = BudgetCallback(guard, default_max_output_tokens=0)
    first_run = uuid4()
    second_run = uuid4()
    third_run = uuid4()

    await first_callback.on_tool_start(
        {"name": "tavily_search"},
        "",
        run_id=first_run,
        inputs={"queries": ["Second Query", "first query"]},
    )
    await first_callback.on_tool_end("results", run_id=first_run)
    await second_callback.on_tool_start(
        {"name": "tavily_search"},
        "",
        run_id=second_run,
        inputs={"queries": [" FIRST   QUERY ", "second query"]},
    )
    await second_callback.on_tool_end("results", run_id=second_run)
    await second_callback.on_tool_start(
        {"name": "tavily_search"},
        "",
        run_id=third_run,
        inputs={"queries": ["different query"]},
    )

    assert ledger.accepted[0].call_id == ledger.accepted[1].call_id
    assert ledger.accepted[1].replayed is False
    assert ledger.accepted[1].call_id != ledger.accepted[2].call_id


@pytest.mark.asyncio
async def test_rebuilt_callback_in_a_new_execution_marks_replay() -> None:
    ledger = RecordingLedger()
    limit = BudgetEstimate(input_tokens=1_000, output_tokens=100)
    first_guard = BudgetGuard(
        ledger,
        limit,
        run_id="run-1",
        execution_id="execution-1",
    )
    second_guard = BudgetGuard(
        ledger,
        limit,
        run_id="run-1",
        execution_id="execution-2",
    )
    first_callback = BudgetCallback(first_guard, default_max_output_tokens=10)
    second_callback = BudgetCallback(second_guard, default_max_output_tokens=10)
    first_run = uuid4()

    await first_callback.on_llm_start({}, ["same input"], run_id=first_run)
    await first_callback.on_llm_end(
        LLMResult(generations=[[Generation(text="first")]]),
        run_id=first_run,
    )
    await second_callback.on_llm_start({}, ["same input"], run_id=uuid4())

    assert ledger.accepted[0].execution_id == "execution-1"
    assert ledger.accepted[1].execution_id == "execution-2"
    assert ledger.accepted[1].replayed is True


@pytest.mark.asyncio
async def test_callback_maps_parent_research_topic_to_bounded_unit_summary_and_retry() -> None:
    events: list[dict[str, object]] = []
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(input_tokens=1_000, output_tokens=100),
        run_id="run-1",
        execution_id="execution-1",
        event_sink=events.append,
    )
    callback = BudgetCallback(guard, default_max_output_tokens=10)
    research_run = uuid4()
    node_run = uuid4()
    model_run = uuid4()
    topic = "比较 Cursor 与 GitHub Copilot 的代码库上下文能力和定价差异"

    await callback.on_chain_start(
        {},
        {"research_topic": topic},
        run_id=research_run,
    )
    await callback.on_chain_start(
        {},
        {"messages": []},
        run_id=node_run,
        parent_run_id=research_run,
    )
    await callback.on_llm_start(
        {},
        ["model input"],
        run_id=model_run,
        parent_run_id=node_run,
        tags=["retry:attempt:2"],
        metadata={"langgraph_node": "researcher"},
    )
    await callback.on_llm_error(
        RuntimeError("provider secret body"),
        run_id=model_run,
    )

    assert [event["event"] for event in events] == [
        "call_attempt_started",
        "call_attempt_failed",
    ]
    started = events[0]
    assert started["node"] == "researcher"
    assert started["retry_number"] == 2
    assert isinstance(started["unit_id"], str)
    assert len(started["unit_id"]) == 64
    assert started["unit_summary"] == topic[:-1] + "…"
    assert started["unit_summary"] != topic
    assert "provider secret body" not in repr(events)


@pytest.mark.asyncio
async def test_locked_langgraph_retry_metadata_keeps_retry_in_one_execution() -> None:
    class FlakyChatModel(BaseChatModel):
        _calls: int = PrivateAttr(default=0)

        @property
        def _llm_type(self) -> str:
            return "flaky-test"

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            del messages, stop, run_manager, kwargs
            self._calls += 1
            if self._calls == 1:
                raise TimeoutError("first attempt fails")
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="ok"))]
            )

    events: list[dict[str, object]] = []
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(input_tokens=1_000, output_tokens=100),
        run_id="run-1",
        execution_id="execution-1",
        event_sink=events.append,
    )
    callback = BudgetCallback(guard, default_max_output_tokens=10)
    model = FlakyChatModel().with_retry(
        retry_if_exception_type=(TimeoutError,),
        stop_after_attempt=2,
    )

    async def researcher(
        state: dict[str, Any],
        config: RunnableConfig,
    ) -> dict[str, str]:
        message = await model.ainvoke(
            [HumanMessage(content=str(state["research_topic"]))],
            config,
        )
        return {"answer": str(message.content)}

    builder = StateGraph(dict)
    builder.add_node("researcher", researcher)
    builder.add_edge(START, "researcher")
    builder.add_edge("researcher", END)

    result = await builder.compile().ainvoke(
        {"research_topic": "topic that remains hashed"},
        {"callbacks": [callback]},
    )

    assert result["answer"] == "ok"
    assert [event["event"] for event in events] == [
        "call_attempt_started",
        "call_attempt_failed",
        "call_attempt_started",
        "call_attempt_succeeded",
    ]
    started = [event for event in events if event["event"] == "call_attempt_started"]
    assert [event["retry_number"] for event in started] == [1, 2]
    assert [event["attempt_number"] for event in started] == [1, 2]
    assert [event["replayed"] for event in started] == [False, False]
    assert started[0]["call_id"] == started[1]["call_id"]
    assert started[0]["node"] == started[1]["node"] == "researcher"
    assert started[0]["unit_id"] == started[1]["unit_id"]
    assert started[0]["unit_summary"] == started[1]["unit_summary"] == "topic that remains hashe…"


@pytest.mark.asyncio
async def test_model_event_provider_follows_actual_connection_format() -> None:
    events: list[dict[str, object]] = []
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(input_tokens=100, output_tokens=100),
        run_id="run-format",
        event_sink=events.append,
    )
    callback = BudgetCallback(guard, default_max_output_tokens=10)
    await callback.on_llm_start(
        {},
        ["model input"],
        run_id=UUID("00000000-0000-0000-0000-000000000010"),
        metadata={
            "langgraph_node": "materialize",
            "opencompete_provider": "DeepSeek",
            "opencompete_model": "openai:gpt-5.6-terra",
            "opencompete_connection_role": "primary",
        },
    )
    started = events[0]
    assert started["provider"] == "OpenAI Chat Completions"
    assert started["connection_format"] == "openai_chat_completions"
    assert started["connection_role"] == "primary"
