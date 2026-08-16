import asyncio

import httpx
import pytest

from opencompete.budget import (
    AttemptEventContext,
    AttemptOutcome,
    BudgetGuard,
    InMemoryBudgetLedger,
    ReservationStatus,
    stable_call_id,
)
from opencompete.errors import BudgetExceededError
from opencompete.models import BudgetEstimate


@pytest.mark.asyncio
async def test_parallel_reservations_cannot_overspend_the_same_run() -> None:
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        limit=BudgetEstimate(tavily_calls=1),
        run_id="run-1",
    )

    results = await asyncio.gather(
        guard.reserve("call-a", BudgetEstimate(tavily_calls=1)),
        guard.reserve("call-b", BudgetEstimate(tavily_calls=1)),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, BudgetExceededError) for result in results) == 1
    snapshot = await guard.snapshot()
    assert snapshot.reserved.tavily_calls == 1
    assert snapshot.remaining.tavily_calls == 0


@pytest.mark.asyncio
async def test_budget_is_isolated_by_run_id() -> None:
    ledger = InMemoryBudgetLedger()
    first = BudgetGuard(ledger, BudgetEstimate(tavily_calls=1), run_id="run-1")
    second = BudgetGuard(ledger, BudgetEstimate(tavily_calls=1), run_id="run-2")

    await first.reserve("call", BudgetEstimate(tavily_calls=1))
    await second.reserve("call", BudgetEstimate(tavily_calls=1))

    assert (await first.snapshot()).reserved.tavily_calls == 1
    assert (await second.snapshot()).reserved.tavily_calls == 1


@pytest.mark.asyncio
async def test_settle_uses_actual_usage_and_unknown_usage_keeps_estimate() -> None:
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        limit=BudgetEstimate(input_tokens=100, output_tokens=100),
        run_id="run-1",
    )
    known = await guard.reserve(
        "known", BudgetEstimate(input_tokens=30, output_tokens=20)
    )
    unknown = await guard.reserve(
        "unknown", BudgetEstimate(input_tokens=20, output_tokens=10)
    )

    settled_known = await guard.settle(
        known.reservation_id,
        BudgetEstimate(input_tokens=12, output_tokens=7),
    )
    settled_unknown = await guard.settle(unknown.reservation_id, None)

    assert settled_known.status is ReservationStatus.SETTLED
    assert settled_unknown.status is ReservationStatus.SETTLED
    snapshot = await guard.snapshot()
    assert snapshot.reserved.input_tokens == 0
    assert snapshot.spent.input_tokens == 32
    assert snapshot.spent.output_tokens == 17


@pytest.mark.asyncio
async def test_cancel_releases_a_reservation_without_spending_it() -> None:
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        limit=BudgetEstimate(tavily_calls=1),
        run_id="run-1",
    )
    reservation = await guard.reserve("call", BudgetEstimate(tavily_calls=1))

    cancelled = await guard.cancel(reservation.reservation_id)

    assert cancelled.status is ReservationStatus.CANCELLED
    snapshot = await guard.snapshot()
    assert snapshot.reserved.tavily_calls == 0
    assert snapshot.spent.tavily_calls == 0
    assert snapshot.remaining.tavily_calls == 1


@pytest.mark.asyncio
async def test_repeated_logical_call_gets_a_new_attempt_and_is_marked_replayed() -> None:
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        limit=BudgetEstimate(tavily_calls=2),
        run_id="run-1",
    )
    first = await guard.reserve("logical-call", BudgetEstimate(tavily_calls=1))
    await guard.settle(first.reservation_id, BudgetEstimate(tavily_calls=1))

    second = await guard.reserve("logical-call", BudgetEstimate(tavily_calls=1))

    assert first.replayed is False
    assert second.replayed is False
    assert first.execution_id == second.execution_id == guard.execution_id
    assert first.attempt_number == 1
    assert second.attempt_number == 2
    assert second.attempt_id != first.attempt_id
    assert second.reservation_id != first.reservation_id


@pytest.mark.asyncio
async def test_cancelled_call_does_not_mark_an_unsent_retry_as_replayed() -> None:
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        limit=BudgetEstimate(tavily_calls=1),
        run_id="run-1",
    )
    first = await guard.reserve("logical-call", BudgetEstimate(tavily_calls=1))
    await guard.cancel(first.reservation_id)

    retry = await guard.reserve("logical-call", BudgetEstimate(tavily_calls=1))

    assert retry.replayed is False


@pytest.mark.asyncio
async def test_new_execution_marks_only_its_first_repeated_call_as_replayed() -> None:
    ledger = InMemoryBudgetLedger()
    limit = BudgetEstimate(tavily_calls=3)
    first_execution = BudgetGuard(
        ledger,
        limit,
        run_id="run-1",
        execution_id="execution-1",
    )
    second_execution = BudgetGuard(
        ledger,
        limit,
        run_id="run-1",
        execution_id="execution-2",
    )

    first = await first_execution.reserve(
        "logical-call", BudgetEstimate(tavily_calls=1)
    )
    await first_execution.settle(
        first.reservation_id,
        BudgetEstimate(tavily_calls=1),
    )
    replay = await second_execution.reserve(
        "logical-call", BudgetEstimate(tavily_calls=1)
    )
    await second_execution.settle(
        replay.reservation_id,
        BudgetEstimate(tavily_calls=1),
    )
    retry = await second_execution.reserve(
        "logical-call", BudgetEstimate(tavily_calls=1)
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert retry.replayed is False
    assert [first.attempt_number, replay.attempt_number, retry.attempt_number] == [
        1,
        2,
        3,
    ]


@pytest.mark.asyncio
async def test_attempt_events_are_minimal_redacted_and_do_not_break_accounting() -> None:
    events: list[dict[str, object]] = []
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(input_tokens=20, output_tokens=10),
        run_id="run-1",
        execution_id="execution-1",
        event_sink=events.append,
    )
    context = AttemptEventContext(
        node="researcher",
        unit_id="unit-hash",
        provider_kind="llm",
        retry_number=2,
    )
    reservation = await guard.reserve(
        "logical-call",
        BudgetEstimate(input_tokens=10, output_tokens=5),
        context=context,
    )
    secret = "sk-secret-that-must-not-appear"

    await guard.settle(
        reservation.reservation_id,
        None,
        outcome=AttemptOutcome.FAILED,
        error=RuntimeError(secret),
    )

    assert [event["event"] for event in events] == [
        "call_attempt_started",
        "call_attempt_failed",
    ]
    terminal = events[-1]
    assert terminal["run_id"] == "run-1"
    assert terminal["execution_id"] == "execution-1"
    assert terminal["node"] == "researcher"
    assert terminal["unit_id"] == "unit-hash"
    assert terminal["provider_kind"] == "llm"
    assert terminal["retry_number"] == 2
    assert terminal["attempt_number"] == 1
    assert terminal["error_code"] == "unexpected_error"
    assert terminal["error_type"] == "RuntimeError"
    assert terminal["usage_source"] == "estimated"
    assert secret not in repr(events)
    assert (await guard.snapshot()).spent.input_tokens == 10


@pytest.mark.asyncio
async def test_cancel_and_failing_event_sink_do_not_corrupt_the_ledger() -> None:
    def broken_sink(event: dict[str, object]) -> None:
        del event
        raise RuntimeError("event sink failed")

    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=1),
        run_id="run-1",
        event_sink=broken_sink,
    )
    reservation = await guard.reserve("call", BudgetEstimate(tavily_calls=1))

    cancelled = await guard.cancel(reservation.reservation_id)

    assert guard.execution_id
    assert cancelled.status is ReservationStatus.CANCELLED
    assert (await guard.snapshot()).remaining.tavily_calls == 1


@pytest.mark.asyncio
async def test_transient_model_failure_emits_retry_and_fallback_plan() -> None:
    events: list[dict[str, object]] = []
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(input_tokens=100, output_tokens=100),
        run_id="run-retry",
        event_sink=events.append,
    )
    context = AttemptEventContext(
        node="researcher",
        provider_kind="llm",
        retry_number=3,
        provider="OpenAI",
        model="openai:primary",
        route_attempts=3,
        primary_attempts=3,
        fallback_attempts=2,
        total_attempts=5,
        retry_initial_seconds=2,
        retry_max_seconds=20,
        fallback_provider="DeepSeek",
        fallback_model="anthropic:fallback",
    )
    reservation = await guard.reserve(
        "logical-model-call",
        BudgetEstimate(input_tokens=10, output_tokens=10),
        context=context,
    )

    await guard.settle(
        reservation.reservation_id,
        None,
        outcome=AttemptOutcome.FAILED,
        error=httpx.ReadTimeout("temporary read timeout"),
    )

    assert [event["event"] for event in events] == [
        "call_attempt_started",
        "call_attempt_failed",
        "model_retry_scheduled",
    ]
    scheduled = events[-1]
    assert scheduled["attempt"] == 3
    assert scheduled["next_attempt"] == 4
    assert scheduled["total_attempts"] == 5
    assert scheduled["next_provider"] == "DeepSeek"
    assert scheduled["next_model"] == "anthropic:fallback"
    assert scheduled["next_fallback"] is True
    assert scheduled["delay_seconds"] == 0


@pytest.mark.asyncio
async def test_cancel_before_send_emits_a_terminal_attempt_event() -> None:
    events: list[dict[str, object]] = []
    guard = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=1),
        run_id="run-1",
        event_sink=events.append,
    )
    reservation = await guard.reserve("call", BudgetEstimate(tavily_calls=1))

    await guard.cancel(reservation.reservation_id)

    assert [event["event"] for event in events] == [
        "call_attempt_started",
        "call_attempt_cancelled_before_send",
    ]
    assert events[-1]["attempt_id"] == reservation.attempt_id
    assert events[-1]["attempt_number"] == 1
    assert events[-1]["usage_source"] == "estimated"


def test_stable_call_id_is_deterministic_and_context_sensitive() -> None:
    first = stable_call_id("run-1", "research", "unit-1", 0)

    assert first == stable_call_id("run-1", "research", "unit-1", 0)
    assert first != stable_call_id("run-1", "research", "unit-1", 1)
    assert first != stable_call_id("run-2", "research", "unit-1", 0)
