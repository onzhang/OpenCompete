"""与 LangGraph Checkpoint 共库但分表的 durable 执行账本."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from opencompete.budget import BudgetReservation, ReservationStatus
from opencompete.errors import PersistenceError
from opencompete.models import BudgetEstimate, BudgetSnapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oc_budget_reservations (
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
    status TEXT NOT NULL CHECK (status IN ('reserved', 'settled', 'cancelled')),
    replayed INTEGER NOT NULL CHECK (replayed IN (0, 1)),
    execution_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS oc_reservations_run_status_idx
    ON oc_budget_reservations (run_id, status);
CREATE INDEX IF NOT EXISTS oc_reservations_run_call_idx
    ON oc_budget_reservations (run_id, call_id);
CREATE TABLE IF NOT EXISTS oc_call_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    reservation_id TEXT NOT NULL UNIQUE,
    replayed INTEGER NOT NULL CHECK (replayed IN (0, 1)),
    execution_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    status TEXT NOT NULL CHECK (status IN ('started', 'settled', 'cancelled')),
    created_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY (reservation_id)
        REFERENCES oc_budget_reservations (reservation_id)
);
CREATE INDEX IF NOT EXISTS oc_attempts_run_call_idx
    ON oc_call_attempts (run_id, call_id, created_at);
"""

_USAGE_SQL = """
SELECT
    COALESCE(SUM(CASE WHEN status = 'settled'
        THEN COALESCE(actual_input_tokens, estimate_input_tokens) ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = 'settled'
        THEN COALESCE(actual_output_tokens, estimate_output_tokens) ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = 'settled'
        THEN COALESCE(actual_tavily_calls, estimate_tavily_calls) ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = 'settled'
        THEN COALESCE(actual_cost_usd, estimate_cost_usd) ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = 'reserved'
        THEN estimate_input_tokens ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = 'reserved'
        THEN estimate_output_tokens ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = 'reserved'
        THEN estimate_tavily_calls ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN status = 'reserved'
        THEN estimate_cost_usd ELSE 0 END), 0)
FROM oc_budget_reservations
WHERE run_id = ?
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _status_value(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value)


def _estimate_values(estimate: BudgetEstimate) -> tuple[int, int, int, float]:
    return (
        estimate.input_tokens,
        estimate.output_tokens,
        estimate.tavily_calls,
        estimate.cost_usd,
    )


def _estimate_from(values: Sequence[int | float]) -> BudgetEstimate:
    return BudgetEstimate(
        input_tokens=int(values[0]),
        output_tokens=int(values[1]),
        tavily_calls=int(values[2]),
        cost_usd=float(values[3]),
    )


def _subtract(
    limit: BudgetEstimate,
    spent: BudgetEstimate,
    reserved: BudgetEstimate,
) -> BudgetEstimate:
    return BudgetEstimate(
        input_tokens=max(
            0, limit.input_tokens - spent.input_tokens - reserved.input_tokens
        ),
        output_tokens=max(
            0, limit.output_tokens - spent.output_tokens - reserved.output_tokens
        ),
        tavily_calls=max(
            0, limit.tavily_calls - spent.tavily_calls - reserved.tavily_calls
        ),
        cost_usd=max(
            0.0, limit.cost_usd - spent.cost_usd - reserved.cost_usd
        ),
    )


def _fits(available: BudgetEstimate, estimate: BudgetEstimate) -> bool:
    return (
        estimate.input_tokens <= available.input_tokens
        and estimate.output_tokens <= available.output_tokens
        and estimate.tavily_calls <= available.tavily_calls
        and estimate.cost_usd <= available.cost_usd + 1e-12
    )


class SqliteExecutionLedger:
    """Use short SQLite transactions so no connection crosses an event loop."""

    def __init__(self, database_path: str | Path) -> None:
        """绑定与 Checkpoint 共用的 SQLite 文件路径."""
        self.database_path = Path(database_path)

    async def initialize(self) -> None:
        """Create only OpenCompete-owned tables in the shared database file."""
        try:
            await asyncio.to_thread(self._initialize_sync)
        except sqlite3.Error as exc:
            raise PersistenceError("初始化 SQLite 执行账本失败。") from exc

    async def try_reserve(
        self,
        reservation: BudgetReservation,
        limit: BudgetEstimate,
    ) -> BudgetReservation | None:
        """Atomically reserve capacity for one concrete external attempt."""
        try:
            return await asyncio.to_thread(
                self._try_reserve_sync, reservation, limit
            )
        except sqlite3.Error as exc:
            raise PersistenceError("写入 SQLite 预算预留失败。") from exc

    async def settle(
        self,
        reservation_id: str,
        actual: BudgetEstimate | None,
    ) -> BudgetReservation:
        """Settle a reservation; unknown actual usage keeps the estimate charged."""
        try:
            return await asyncio.to_thread(
                self._settle_sync, reservation_id, actual
            )
        except sqlite3.Error as exc:
            raise PersistenceError("结算 SQLite 预算预留失败。") from exc

    async def cancel(self, reservation_id: str) -> BudgetReservation:
        """Cancel a request known not to have reached the external provider."""
        try:
            return await asyncio.to_thread(self._cancel_sync, reservation_id)
        except sqlite3.Error as exc:
            raise PersistenceError("取消 SQLite 预算预留失败。") from exc

    async def snapshot(
        self,
        run_id: str,
        limit: BudgetEstimate,
    ) -> BudgetSnapshot:
        """Read one run's spent, reserved, and remaining budget."""
        try:
            return await asyncio.to_thread(self._snapshot_sync, run_id, limit)
        except sqlite3.Error as exc:
            raise PersistenceError("读取 SQLite 预算快照失败。") from exc

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            self._ensure_schema(connection)

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_SCHEMA)
        self._add_missing_columns(
            connection,
            "oc_budget_reservations",
            {"execution_id": "TEXT", "attempt_number": "INTEGER"},
        )
        self._add_missing_columns(
            connection,
            "oc_call_attempts",
            {"execution_id": "TEXT", "attempt_number": "INTEGER"},
        )

    def _add_missing_columns(
        self,
        connection: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        """Migrate only OpenCompete tables without claiming shared DB versions."""
        existing = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                )

    def _usage(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> tuple[BudgetEstimate, BudgetEstimate]:
        row = connection.execute(_USAGE_SQL, (run_id,)).fetchone()
        if row is None:
            return BudgetEstimate(), BudgetEstimate()
        return _estimate_from(row[0:4]), _estimate_from(row[4:8])

    def _try_reserve_sync(
        self,
        reservation: BudgetReservation,
        limit: BudgetEstimate,
    ) -> BudgetReservation | None:
        if _status_value(reservation.status) != "reserved":
            raise ValueError("新预留的状态必须是 reserved。")
        if reservation.actual is not None:
            raise ValueError("新预留不能包含实际用量。")
        if reservation.execution_id is None or not reservation.execution_id.strip():
            raise ValueError("新预留必须包含非空 execution_id。")

        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                spent, reserved = self._usage(connection, reservation.run_id)
                available = _subtract(limit, spent, reserved)
                if not _fits(available, reservation.estimate):
                    connection.rollback()
                    return None

                same_execution_prior = (
                    connection.execute(
                        """
                        SELECT 1 FROM oc_call_attempts
                        WHERE run_id = ? AND call_id = ?
                            AND status != 'cancelled'
                            AND execution_id = ?
                        LIMIT 1
                        """,
                        (
                            reservation.run_id,
                            reservation.call_id,
                            reservation.execution_id,
                        ),
                    ).fetchone()
                    is not None
                )
                different_execution_prior = (
                    connection.execute(
                        """
                        SELECT 1 FROM oc_call_attempts
                        WHERE run_id = ? AND call_id = ?
                            AND status != 'cancelled'
                            AND (execution_id IS NULL OR execution_id != ?)
                        LIMIT 1
                        """,
                        (
                            reservation.run_id,
                            reservation.call_id,
                            reservation.execution_id,
                        ),
                    ).fetchone()
                    is not None
                )
                attempt_row = connection.execute(
                    """
                    SELECT COUNT(*) FROM oc_call_attempts
                    WHERE run_id = ? AND call_id = ?
                    """,
                    (reservation.run_id, reservation.call_id),
                ).fetchone()
                attempt_number = int(attempt_row[0]) + 1
                persisted = replace(
                    reservation,
                    replayed=(
                        different_execution_prior and not same_execution_prior
                    ),
                    attempt_number=attempt_number,
                )
                created_at = _now()
                connection.execute(
                    """
                    INSERT INTO oc_budget_reservations (
                        reservation_id, run_id, call_id, attempt_id,
                        estimate_input_tokens, estimate_output_tokens,
                        estimate_tavily_calls, estimate_cost_usd,
                        status, replayed, execution_id, attempt_number, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?)
                    """,
                    (
                        persisted.reservation_id,
                        persisted.run_id,
                        persisted.call_id,
                        persisted.attempt_id,
                        *_estimate_values(persisted.estimate),
                        int(persisted.replayed),
                        persisted.execution_id,
                        persisted.attempt_number,
                        created_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO oc_call_attempts (
                        attempt_id, run_id, call_id, reservation_id,
                        replayed, execution_id, attempt_number, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'started', ?)
                    """,
                    (
                        persisted.attempt_id,
                        persisted.run_id,
                        persisted.call_id,
                        persisted.reservation_id,
                        int(persisted.replayed),
                        persisted.execution_id,
                        persisted.attempt_number,
                        created_at,
                    ),
                )
                connection.commit()
                return persisted
            except BaseException:
                connection.rollback()
                raise

    def _load_reservation(
        self,
        connection: sqlite3.Connection,
        reservation_id: str,
    ) -> BudgetReservation:
        row = connection.execute(
            "SELECT * FROM oc_budget_reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"预算预留不存在：{reservation_id}")
        actual = None
        if row["actual_input_tokens"] is not None:
            actual = BudgetEstimate(
                input_tokens=row["actual_input_tokens"],
                output_tokens=row["actual_output_tokens"],
                tavily_calls=row["actual_tavily_calls"],
                cost_usd=row["actual_cost_usd"],
            )
        return BudgetReservation(
            run_id=row["run_id"],
            reservation_id=row["reservation_id"],
            call_id=row["call_id"],
            attempt_id=row["attempt_id"],
            estimate=BudgetEstimate(
                input_tokens=row["estimate_input_tokens"],
                output_tokens=row["estimate_output_tokens"],
                tavily_calls=row["estimate_tavily_calls"],
                cost_usd=row["estimate_cost_usd"],
            ),
            actual=actual,
            status=ReservationStatus(row["status"]),
            replayed=bool(row["replayed"]),
            execution_id=row["execution_id"],
            attempt_number=int(row["attempt_number"] or 0),
        )

    def _settle_sync(
        self,
        reservation_id: str,
        actual: BudgetEstimate | None,
    ) -> BudgetReservation:
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._load_reservation(connection, reservation_id)
                status = _status_value(current.status)
                if status == "cancelled":
                    raise ValueError("已取消的预算预留不能结算。")
                if status == "settled":
                    connection.commit()
                    return current

                values: tuple[int | float | None, ...]
                if actual is None:
                    values = (None, None, None, None)
                else:
                    values = _estimate_values(actual)
                finished_at = _now()
                connection.execute(
                    """
                    UPDATE oc_budget_reservations
                    SET actual_input_tokens = ?, actual_output_tokens = ?,
                        actual_tavily_calls = ?, actual_cost_usd = ?,
                        status = 'settled', finished_at = ?
                    WHERE reservation_id = ?
                    """,
                    (*values, finished_at, reservation_id),
                )
                connection.execute(
                    """
                    UPDATE oc_call_attempts
                    SET status = 'settled', finished_at = ?
                    WHERE reservation_id = ?
                    """,
                    (finished_at, reservation_id),
                )
                connection.commit()
                return self._load_reservation(connection, reservation_id)
            except BaseException:
                connection.rollback()
                raise

    def _cancel_sync(self, reservation_id: str) -> BudgetReservation:
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._load_reservation(connection, reservation_id)
                status = _status_value(current.status)
                if status == "settled":
                    raise ValueError("已结算的预算预留不能取消。")
                if status == "cancelled":
                    connection.commit()
                    return current

                finished_at = _now()
                connection.execute(
                    """
                    UPDATE oc_budget_reservations
                    SET status = 'cancelled', finished_at = ?
                    WHERE reservation_id = ?
                    """,
                    (finished_at, reservation_id),
                )
                connection.execute(
                    """
                    UPDATE oc_call_attempts
                    SET status = 'cancelled', finished_at = ?
                    WHERE reservation_id = ?
                    """,
                    (finished_at, reservation_id),
                )
                connection.commit()
                return self._load_reservation(connection, reservation_id)
            except BaseException:
                connection.rollback()
                raise

    def _snapshot_sync(
        self,
        run_id: str,
        limit: BudgetEstimate,
    ) -> BudgetSnapshot:
        with self._connect() as connection:
            self._ensure_schema(connection)
            spent, reserved = self._usage(connection, run_id)
        return BudgetSnapshot(
            limit=limit,
            spent=spent,
            reserved=reserved,
            remaining=_subtract(limit, spent, reserved),
        )
