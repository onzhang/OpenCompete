"""Run one real fixed-scope live research for v3 ocb-006."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _emit(*parts: object) -> None:
    """向标准输出写入单行运行诊断。."""
    sys.stdout.write(" ".join(str(part) for part in parts) + "\n")
    sys.stdout.flush()


async def main() -> int:
    """执行一个固定范围的 v3 真实研究并写入最后一次运行摘要。."""
    from opencompete.application import ApplicationRunner
    from opencompete.evaluation.competitive_v3_dataset import (
        load_competitive_v3_dataset,
    )
    from opencompete.evaluation.competitive_v3_models import normalize_v3_task_text
    from opencompete.models import utc_now
    from opencompete.settings import load_settings

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
    _emit("START_LIVE_RESEARCH")
    _emit("case_id", case.case_id)
    _emit("task_input_sha256", case.normalized_task_sha256)
    handle = await runner.create_fixed_scope_run(
        request,
        frozen,
        benchmark_case_id=case.case_id,
    )
    _emit("RUN_ID", handle.run_id)
    _emit("STATUS", handle.status.value if hasattr(handle.status, "value") else handle.status)
    summary = {
        "run_id": handle.run_id,
        "status": getattr(handle.status, "value", str(handle.status)),
        "case_id": case.case_id,
        "task_input_sha256": case.normalized_task_sha256,
    }
    out = ROOT / ".opencompete-data" / "live-v3-ocb006-last.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _emit("WROTE", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
