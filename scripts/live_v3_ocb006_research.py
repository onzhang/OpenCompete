"""Run one real fixed-scope live research for v3 ocb-006."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from opencompete.application import ApplicationRunner
from opencompete.evaluation.competitive_v3_dataset import load_competitive_v3_dataset
from opencompete.evaluation.competitive_v3_models import normalize_v3_task_text
from opencompete.models import utc_now
from opencompete.settings import load_settings


async def main() -> int:
    settings = load_settings(config_path=ROOT / "opencompete.toml")
    dataset = load_competitive_v3_dataset(ROOT / "benchmarks" / "opencompete" / "v3")
    case = dataset.get("ocb-006-spotify-vs-apple-music")
    frozen = case.expected_scope.model_copy(
        update={"confirmed_at": utc_now(), "revision": 1, "frozen": True}
    )
    # match create_fixed_scope_run task_input_sha256 = sha256(request.query)
    query = normalize_v3_task_text(case.prompt)
    request = case.research_request.model_copy(update={"query": query})
    runner = ApplicationRunner(settings)
    print("START_LIVE_RESEARCH", flush=True)
    print("case_id", case.case_id, flush=True)
    print("task_input_sha256", case.normalized_task_sha256, flush=True)
    handle = await runner.create_fixed_scope_run(
        request,
        frozen,
        benchmark_case_id=case.case_id,
    )
    print("RUN_ID", handle.run_id, flush=True)
    print("STATUS", handle.status.value if hasattr(handle.status, "value") else handle.status, flush=True)
    summary = {
        "run_id": handle.run_id,
        "status": getattr(handle.status, "value", str(handle.status)),
        "case_id": case.case_id,
        "task_input_sha256": case.normalized_task_sha256,
    }
    out = ROOT / ".opencompete-data" / "live-v3-ocb006-last.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("WROTE", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
