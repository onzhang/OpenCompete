"""Application Runner 跨 Python 进程恢复的系统验收。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from opencompete.models import RunStatus
from opencompete.persistence import RunRepository


def _worker_path() -> Path:
    return Path(__file__).parent / "helpers" / "acceptance_worker.py"


def _run_worker(
    mode: str,
    data_root: Path,
    run_id: str | None = None,
    *,
    environment: dict[str, str] | None = None,
    check: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(_worker_path()), mode, str(data_root)]
    if run_id is not None:
        command.append(run_id)
    worker_environment = dict(environment or os.environ)
    worker_environment.update(
        {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    )
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env=worker_environment,
        cwd=cwd,
    )


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(completed.stdout)


def test_confirmation_interrupt_survives_two_fresh_application_processes(
    tmp_path: Path,
) -> None:
    """创建进程退出后，两个新 Runner 仍看到并完成同一 durable 确认点。"""
    data_root = tmp_path / "data"

    created = _payload(_run_worker("create", data_root))
    run_id = str(created["run_id"])
    inspected = _payload(_run_worker("inspect", data_root, run_id))
    completed = _payload(_run_worker("confirm", data_root, run_id))

    assert created == {
        "run_id": run_id,
        "status": "awaiting_confirmation",
        "pending_confirmation": True,
        "scope_revision": 0,
    }
    assert inspected == created
    assert completed["run_id"] == run_id
    assert completed["status"] == "completed"
    assert completed["artifact_revision"] == 1
    assert "Alpha 与 Beta 竞品分析" in completed["report"]
    assert RunRepository(data_root).load_verified(run_id).bundle.status is (
        RunStatus.COMPLETED
    )


def test_research_state_resumes_after_process_dies_between_child_nodes(
    tmp_path: Path,
) -> None:
    """研究中硬退出后，新进程恢复同一任务并按语义审计节点重放。"""
    data_root = tmp_path / "data"
    crash_marker = tmp_path / "research-crashed.txt"
    visit_log = tmp_path / "research-visits.txt"
    environment = os.environ.copy()
    environment["OPENCOMPETE_ACCEPTANCE_CRASH_MARKER"] = str(crash_marker)
    environment["OPENCOMPETE_ACCEPTANCE_VISIT_LOG"] = str(visit_log)

    created = _payload(
        _run_worker("create", data_root, environment=environment)
    )
    run_id = str(created["run_id"])
    crashed = _run_worker(
        "confirm-crash",
        data_root,
        run_id,
        environment=environment,
        check=False,
    )

    assert crashed.returncode == 73, crashed.stderr
    assert crash_marker.is_file()
    assert (data_root / "checkpoints.sqlite3").is_file()
    assert RunRepository(data_root).load_manifest(run_id).status is (
        RunStatus.AWAITING_CONFIRMATION
    )

    resumed = _payload(
        _run_worker(
            "resume-research",
            data_root,
            run_id,
            environment=environment,
        )
    )

    assert resumed["status"] == "completed"
    assert resumed["run_id"] == run_id
    assert resumed["artifact_revision"] == 1
    assert "Alpha 与 Beta 竞品分析" in resumed["report"]
    assert visit_log.read_text(encoding="utf-8").splitlines() == [
        "已捕获研究资料",
        "已捕获研究资料",
    ]
