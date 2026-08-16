from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from opencompete.budget import BudgetReservation, ReservationStatus
from opencompete.models import BudgetEstimate
from opencompete.persistence.ledger import SqliteExecutionLedger


def _reservation(
    run_id: str,
    suffix: str,
    *,
    call_id: str | None = None,
    tavily_calls: int = 1,
    execution_id: str = "execution-1",
) -> BudgetReservation:
    return BudgetReservation(
        run_id=run_id,
        reservation_id=f"reservation-{suffix}",
        call_id=call_id or f"call-{suffix}",
        attempt_id=f"attempt-{suffix}",
        estimate=BudgetEstimate(tavily_calls=tavily_calls, cost_usd=0.5),
        status="reserved",
        replayed=False,
        execution_id=execution_id,
    )


def test_concurrent_reservations_cannot_overspend(tmp_path: Path) -> None:
    async def scenario() -> None:
        ledger = SqliteExecutionLedger(tmp_path / "checkpoints.sqlite3")
        limit = BudgetEstimate(tavily_calls=1, cost_usd=0.5)
        results = await asyncio.gather(
            ledger.try_reserve(_reservation("run-1", "a"), limit),
            ledger.try_reserve(_reservation("run-1", "b"), limit),
        )

        assert sum(result is not None for result in results) == 1
        snapshot = await ledger.snapshot("run-1", limit)
        assert snapshot.reserved == BudgetEstimate(
            tavily_calls=1, cost_usd=0.5
        )
        assert snapshot.remaining == BudgetEstimate()

    asyncio.run(scenario())


def test_budget_is_isolated_by_run_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        ledger = SqliteExecutionLedger(tmp_path / "checkpoints.sqlite3")
        limit = BudgetEstimate(tavily_calls=1, cost_usd=0.5)

        first = await ledger.try_reserve(_reservation("run-1", "one"), limit)
        second = await ledger.try_reserve(_reservation("run-2", "two"), limit)

        assert first is not None
        assert second is not None
        assert (await ledger.snapshot("run-1", limit)).reserved.tavily_calls == 1
        assert (await ledger.snapshot("run-2", limit)).reserved.tavily_calls == 1

    asyncio.run(scenario())


def test_settle_and_cancel_use_short_durable_records(tmp_path: Path) -> None:
    async def scenario() -> None:
        ledger = SqliteExecutionLedger(tmp_path / "checkpoints.sqlite3")
        limit = BudgetEstimate(tavily_calls=3, cost_usd=3.0)
        first = _reservation("run-1", "settle")
        second = _reservation("run-1", "cancel")
        await ledger.try_reserve(first, limit)
        await ledger.try_reserve(second, limit)

        settled = await ledger.settle(
            first.reservation_id,
            BudgetEstimate(tavily_calls=1, cost_usd=0.25),
        )
        cancelled = await ledger.cancel(second.reservation_id)
        snapshot = await ledger.snapshot("run-1", limit)

        assert settled.status is ReservationStatus.SETTLED
        assert settled.actual == BudgetEstimate(
            tavily_calls=1, cost_usd=0.25
        )
        assert cancelled.status is ReservationStatus.CANCELLED
        assert snapshot.spent == BudgetEstimate(
            tavily_calls=1, cost_usd=0.25
        )
        assert snapshot.reserved == BudgetEstimate()

    asyncio.run(scenario())


def test_cancelled_attempt_does_not_mark_a_later_unsent_call_as_replayed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        ledger = SqliteExecutionLedger(tmp_path / "checkpoints.sqlite3")
        limit = BudgetEstimate(tavily_calls=1, cost_usd=0.5)
        first = _reservation("run-1", "cancelled", call_id="same-call")
        await ledger.try_reserve(first, limit)
        await ledger.cancel(first.reservation_id)

        second = _reservation("run-1", "later", call_id="same-call")
        accepted = await ledger.try_reserve(second, limit)

        assert accepted is not None
        assert accepted.replayed is False

    asyncio.run(scenario())


def test_same_execution_retry_is_not_replayed_but_new_execution_is(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        ledger = SqliteExecutionLedger(tmp_path / "checkpoints.sqlite3")
        limit = BudgetEstimate(tavily_calls=4, cost_usd=2.0)
        first = await ledger.try_reserve(
            _reservation("run-1", "one", call_id="same-call"),
            limit,
        )
        retry = await ledger.try_reserve(
            _reservation("run-1", "two", call_id="same-call"),
            limit,
        )
        replay = await ledger.try_reserve(
            _reservation(
                "run-1",
                "three",
                call_id="same-call",
                execution_id="execution-2",
            ),
            limit,
        )
        resumed_retry = await ledger.try_reserve(
            _reservation(
                "run-1",
                "four",
                call_id="same-call",
                execution_id="execution-2",
            ),
            limit,
        )

        assert first is not None and first.replayed is False
        assert retry is not None and retry.replayed is False
        assert replay is not None and replay.replayed is True
        assert resumed_retry is not None and resumed_retry.replayed is False
        assert [
            first.attempt_number,
            retry.attempt_number,
            replay.attempt_number,
            resumed_retry.attempt_number,
        ] == [1, 2, 3, 4]

    asyncio.run(scenario())


def test_initialize_migrates_legacy_tables_without_losing_budget(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpoints.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE oc_budget_reservations (
                reservation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                call_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL UNIQUE,
                estimate_input_tokens INTEGER NOT NULL,
                estimate_output_tokens INTEGER NOT NULL,
                estimate_tavily_calls INTEGER NOT NULL,
                estimate_cost_usd REAL NOT NULL,
                actual_input_tokens INTEGER,
                actual_output_tokens INTEGER,
                actual_tavily_calls INTEGER,
                actual_cost_usd REAL,
                status TEXT NOT NULL,
                replayed INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE oc_call_attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                call_id TEXT NOT NULL,
                reservation_id TEXT NOT NULL UNIQUE,
                replayed INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT
            );
            INSERT INTO oc_budget_reservations VALUES (
                'legacy-reservation', 'run-1', 'same-call', 'legacy-attempt',
                0, 0, 1, 0.5, 0, 0, 1, 0.5,
                'settled', 0, '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:01+00:00'
            );
            INSERT INTO oc_call_attempts VALUES (
                'legacy-attempt', 'run-1', 'same-call', 'legacy-reservation',
                0, 'settled', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:01+00:00'
            );
            """
        )

    async def scenario() -> None:
        ledger = SqliteExecutionLedger(database)
        await ledger.initialize()
        limit = BudgetEstimate(tavily_calls=3, cost_usd=1.5)
        before = await ledger.snapshot("run-1", limit)
        accepted = await ledger.try_reserve(
            _reservation(
                "run-1",
                "new",
                call_id="same-call",
                execution_id="execution-new",
            ),
            limit,
        )
        assert accepted is not None
        await ledger.settle(
            accepted.reservation_id,
            BudgetEstimate(tavily_calls=1, cost_usd=0.5),
        )
        retry = await ledger.try_reserve(
            _reservation(
                "run-1",
                "retry",
                call_id="same-call",
                execution_id="execution-new",
            ),
            limit,
        )

        assert before.spent == BudgetEstimate(tavily_calls=1, cost_usd=0.5)
        assert accepted.replayed is True
        assert accepted.attempt_number == 2
        assert retry is not None
        assert retry.replayed is False
        assert retry.attempt_number == 3

    asyncio.run(scenario())
    with sqlite3.connect(database) as connection:
        reservation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(oc_budget_reservations)")
        }
        attempt_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(oc_call_attempts)")
        }
    assert {"execution_id", "attempt_number"} <= reservation_columns
    assert {"execution_id", "attempt_number"} <= attempt_columns


def _run_worker(worker: Path, mode: str, database: Path) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(worker), mode, str(database)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_unsettled_attempt_is_charged_and_replayed_across_processes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpoints.sqlite3"
    worker = Path(__file__).parent / "helpers" / "ledger_worker.py"

    created = _run_worker(worker, "create", database)
    resumed = _run_worker(worker, "resume", database)

    assert created == {
        "accepted": True,
        "replayed": False,
        "attempt_number": 1,
        "reserved_calls": 1,
        "remaining_calls": 1,
    }
    assert resumed == {
        "accepted": True,
        "replayed": True,
        "attempt_number": 2,
        "reserved_calls": 2,
        "remaining_calls": 0,
    }
