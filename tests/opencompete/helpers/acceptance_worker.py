"""在独立 Python 进程中运行 OpenCompete 应用层验收流程。"""

from __future__ import annotations

import argparse
import asyncio
import importlib.abc
import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import Any


class _BlockedAgentServerFinder(importlib.abc.MetaPathFinder):
    """一旦正式流程尝试导入远程运行层就立即失败。"""

    _PREFIXES = ("langgraph_api", "langgraph_cli")

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: object = None,
    ) -> None:
        del path, target
        if any(
            fullname == prefix or fullname.startswith(f"{prefix}.")
            for prefix in self._PREFIXES
        ):
            raise ImportError(f"离线验收禁止导入远程运行层：{fullname}")
        return None


def _install_guards() -> None:
    if os.environ.get("OPENCOMPETE_BLOCK_AGENT_SERVER") == "1":
        sys.meta_path.insert(0, _BlockedAgentServerFinder())
    if os.environ.get("OPENCOMPETE_BLOCK_NETWORK") == "1":

        def reject_network(event: str, args: tuple[object, ...]) -> None:
            if event not in {"socket.connect", "socket.getaddrinfo"}:
                return
            address = args[1] if event == "socket.connect" else args[0]
            host = address[0] if isinstance(address, tuple) else address
            try:
                if ipaddress.ip_address(str(host)).is_loopback:
                    return
            except ValueError:
                if str(host).casefold() == "localhost":
                    return
            raise RuntimeError(f"离线验收禁止外部网络访问：{event}/{host}")

        sys.addaudithook(reject_network)


_install_guards()

from acceptance_support import (  # noqa: E402
    CASE_ID,
    AcceptanceExtractor,
    AcceptanceJudge,
    acceptance_request,
    build_acceptance_research_graph,
    write_acceptance_dataset,
)

from opencompete.analysis import build_draft_scope  # noqa: E402
from opencompete.application import ApplicationRunner  # noqa: E402
from opencompete.evaluation import (  # noqa: E402
    EvaluationRequest,
    EvaluationService,
    EvaluationTarget,
)
from opencompete.models import RunStatus, ScopeConfirmation  # noqa: E402
from opencompete.persistence import RunRepository  # noqa: E402
from opencompete.settings import OpenCompeteSettings  # noqa: E402


def _runner(data_root: Path, *, partial: bool = False) -> ApplicationRunner:
    repository = RunRepository(data_root)
    return ApplicationRunner(
        OpenCompeteSettings(data_dir=data_root),
        repository=repository,
        research_graph=build_acceptance_research_graph(),
        extractor=AcceptanceExtractor(partial=partial),
        evaluation_service=EvaluationService(
            repository=repository,
            judge=AcceptanceJudge(),
        ),
    )


async def _run(mode: str, data_root: Path, run_id: str | None) -> dict[str, Any]:
    partial = os.environ.get("OPENCOMPETE_ACCEPTANCE_PARTIAL") == "1"
    runner = _runner(data_root, partial=partial)
    if mode == "create":
        handle = await runner.create_run(acceptance_request())
        return {
            "run_id": handle.run_id,
            "status": handle.status.value,
            "pending_confirmation": handle.pending_confirmation,
            "scope_revision": handle.scope.revision if handle.scope else None,
        }
    if mode == "full":
        pending = await runner.create_run(acceptance_request())
        if pending.scope is None:
            raise RuntimeError("新任务没有进入范围确认点")
        handle = await runner.resume_run(
            pending.run_id,
            ScopeConfirmation(
                accepted=True,
                expected_revision=pending.scope.revision,
            ),
        )
        loaded = await runner.load_run(pending.run_id)
        summaries = await runner.list_runs()
        dataset = write_acceptance_dataset(
            data_root.parent / "benchmark-fixture",
            build_draft_scope(acceptance_request()),
        )
        batch = await runner.evaluate_dataset(
            EvaluationRequest(
                dataset=dataset,
                targets=(
                    EvaluationTarget(case_id=CASE_ID, run_id=pending.run_id),
                ),
                allow_draft=True,
            )
        )
        return {
            **_published_result(runner, handle),
            "loaded_status": loaded.status.value,
            "listed_run_ids": [summary.run_id for summary in summaries],
            "evaluated_cases": [result.case_id for result in batch.results],
            "langgraph_json_exists": (Path.cwd() / "langgraph.json").exists(),
        }
    if run_id is None:
        raise ValueError(f"{mode} 模式必须提供 run_id")
    if mode == "inspect":
        handle = await runner.resume_run(run_id)
        return {
            "run_id": handle.run_id,
            "status": handle.status.value,
            "pending_confirmation": handle.pending_confirmation,
            "scope_revision": handle.scope.revision if handle.scope else None,
        }
    if mode in {"confirm", "confirm-crash"}:
        pending = await runner.resume_run(run_id)
        if pending.scope is None:
            raise RuntimeError("恢复的确认点没有 Scope")
        handle = await runner.resume_run(
            run_id,
            ScopeConfirmation(
                accepted=True,
                expected_revision=pending.scope.revision,
            ),
        )
        if mode == "confirm-crash":
            raise RuntimeError("预期的研究节点崩溃没有发生")
        return _published_result(runner, handle)
    if mode == "resume-research":
        handle = await runner.resume_run(run_id)
        return _published_result(runner, handle)
    raise ValueError(f"不支持的验收模式：{mode}")


def _published_result(
    runner: ApplicationRunner,
    handle: object,
) -> dict[str, Any]:
    del runner
    status = getattr(handle, "status")
    bundle = getattr(handle, "bundle")
    return {
        "run_id": getattr(handle, "run_id"),
        "status": status.value if isinstance(status, RunStatus) else str(status),
        "artifact_revision": bundle.artifact_revision if bundle else None,
        "report": bundle.report.markdown if bundle else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "create",
            "inspect",
            "confirm",
            "confirm-crash",
            "resume-research",
            "full",
        ),
    )
    parser.add_argument("data_root", type=Path)
    parser.add_argument("run_id", nargs="?")
    args = parser.parse_args()
    result = asyncio.run(_run(args.mode, args.data_root, args.run_id))
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
