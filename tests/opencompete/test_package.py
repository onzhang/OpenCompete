"""OpenCompete 包入口的离线测试。"""

import subprocess
import sys


def test_package_exposes_version() -> None:
    """包可以导入并公开稳定版本。"""
    import opencompete

    assert opencompete.__version__ == "0.1.0"


def test_cli_help_loads_without_agent_server() -> None:
    """CLI 帮助不需要 Agent Server 或 LangGraph 配置。"""
    result = subprocess.run(
        [sys.executable, "-m", "opencompete.cli", "--help"],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "OpenCompete" in result.stdout
