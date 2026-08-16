"""正式流程不依赖 Agent Server、langgraph.json 或联网的验收。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _offline_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(
            marker in key.casefold()
            for marker in (
                "api_key",
                "apikey",
                "authorization",
                "access_token",
                "secret",
            )
        )
    }
    environment.update(
        {
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_TRACING": "false",
            "OPENCOMPETE_BLOCK_AGENT_SERVER": "1",
            "OPENCOMPETE_BLOCK_NETWORK": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    return environment


def test_full_application_lifecycle_runs_in_empty_cwd_without_remote_runtime(
    tmp_path: Path,
) -> None:
    """空工作目录、无密钥、禁止网络和 Server 导入时仍能完成正式用例。"""
    worker = Path(__file__).parent / "helpers" / "acceptance_worker.py"
    data_root = tmp_path / "local-data"
    assert not (tmp_path / "langgraph.json").exists()

    completed = subprocess.run(
        [sys.executable, str(worker), "full", str(data_root)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        cwd=tmp_path,
        env=_offline_environment(),
    )
    result = json.loads(completed.stdout)

    assert result["status"] == "completed"
    assert result["loaded_status"] == "completed"
    assert result["listed_run_ids"] == [result["run_id"]]
    assert result["evaluated_cases"] == ["ocb-acceptance-alpha-beta"]
    assert result["langgraph_json_exists"] is False
    assert not (tmp_path / "langgraph.json").exists()


def test_upstream_report_evaluators_still_import_without_network(
    tmp_path: Path,
) -> None:
    """OpenCompete 新入口没有破坏上游保留的可选评测模块导入。"""
    repository_root = Path(__file__).parents[2]
    environment = _offline_environment()
    environment["OPENAI_API_KEY"] = "仅供离线构造模型对象的测试值"
    script = """
import sys

def reject_network(event, args):
    del args
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError(f"导入测试禁止网络访问：{event}")

sys.addaudithook(reject_network)
import tests.evaluators as evaluators
print(evaluators.__name__)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        cwd=repository_root,
        env=environment,
    )

    assert completed.stdout.strip() == "tests.evaluators"
    assert not (tmp_path / "langgraph.json").exists()
