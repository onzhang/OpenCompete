from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from opencompete.budget import BudgetReservation
from opencompete.models import BudgetEstimate
from opencompete.persistence.checkpoints import SqliteCheckpointStore
from opencompete.persistence.ledger import SqliteExecutionLedger


def _run_worker(worker: Path, mode: str, database: Path, run_id: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(worker), mode, str(database), run_id],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_checkpoint_store_uses_parent_directory(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "checkpoints.sqlite3"

    store = SqliteCheckpointStore(database)

    assert store.database_path == database


def test_interrupt_resumes_in_a_new_process(tmp_path: Path) -> None:
    database = tmp_path / "checkpoints.sqlite3"
    worker = Path(__file__).parent / "helpers" / "checkpoint_worker.py"
    run_id = "cross-process-run"

    created = _run_worker(worker, "create", database, run_id)
    resumed = _run_worker(worker, "resume", database, run_id)

    assert created == {
        "interrupted": True,
        "next": ["wait_for_confirmation"],
    }
    assert resumed == {"confirmation": "confirmed", "next": []}


def test_checkpoint_and_execution_ledger_share_one_database_file(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpoints.sqlite3"
    worker = Path(__file__).parent / "helpers" / "checkpoint_worker.py"
    _run_worker(worker, "create", database, "shared-database-run")

    async def reserve() -> None:
        ledger = SqliteExecutionLedger(database)
        result = await ledger.try_reserve(
            BudgetReservation(
                run_id="shared-database-run",
                reservation_id="reservation-shared",
                call_id="call-shared",
                attempt_id="attempt-shared",
                estimate=BudgetEstimate(tavily_calls=1),
            ),
            BudgetEstimate(tavily_calls=1),
        )
        assert result is not None

    asyncio.run(reserve())
