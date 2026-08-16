"""在独立进程中制造和恢复未结算的外部调用。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from opencompete.budget import BudgetReservation
from opencompete.models import BudgetEstimate
from opencompete.persistence.ledger import SqliteExecutionLedger


def _reservation(mode: str) -> BudgetReservation:
    suffix = "1" if mode == "create" else "2"
    return BudgetReservation(
        run_id="run-replay",
        reservation_id=f"reservation-{suffix}",
        call_id="stable-call",
        attempt_id=f"attempt-{suffix}",
        estimate=BudgetEstimate(tavily_calls=1, cost_usd=0.5),
        status="reserved",
        replayed=False,
        execution_id=f"execution-{mode}",
    )


async def _run(mode: str, database: Path) -> dict[str, object]:
    ledger = SqliteExecutionLedger(database)
    limit = BudgetEstimate(tavily_calls=2, cost_usd=1.0)
    reserved = await ledger.try_reserve(_reservation(mode), limit)
    snapshot = await ledger.snapshot("run-replay", limit)
    return {
        "accepted": reserved is not None,
        "replayed": reserved.replayed if reserved is not None else None,
        "attempt_number": reserved.attempt_number if reserved is not None else None,
        "reserved_calls": snapshot.reserved.tavily_calls,
        "remaining_calls": snapshot.remaining.tavily_calls,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "resume"))
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    sys.stdout.write(json.dumps(asyncio.run(_run(args.mode, args.database))))


if __name__ == "__main__":
    main()
