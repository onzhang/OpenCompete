"""在独立进程中验证 LangGraph SQLite Checkpoint 恢复。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from opencompete.persistence.checkpoints import SqliteCheckpointStore


class WorkerState(TypedDict, total=False):
    prompt: str
    confirmation: str


def _wait_for_confirmation(state: WorkerState) -> WorkerState:
    confirmation = interrupt({"prompt": state["prompt"]})
    return {"confirmation": str(confirmation)}


def _build_graph(checkpointer: object) -> object:
    builder = StateGraph(WorkerState)
    builder.add_node("wait_for_confirmation", _wait_for_confirmation)
    builder.add_edge(START, "wait_for_confirmation")
    builder.add_edge("wait_for_confirmation", END)
    return builder.compile(checkpointer=checkpointer)


async def _run(mode: str, database: Path, run_id: str) -> dict[str, object]:
    store = SqliteCheckpointStore(database)
    config = {"configurable": {"thread_id": run_id}}
    async with store.open_saver() as saver:
        graph = _build_graph(saver)
        if mode == "create":
            result = await graph.ainvoke({"prompt": "确认研究范围"}, config)
            snapshot = await graph.aget_state(config)
            return {
                "interrupted": "__interrupt__" in result,
                "next": list(snapshot.next),
            }

        result = await graph.ainvoke(Command(resume="confirmed"), config)
        snapshot = await graph.aget_state(config)
        return {
            "confirmation": result["confirmation"],
            "next": list(snapshot.next),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "resume"))
    parser.add_argument("database", type=Path)
    parser.add_argument("run_id")
    args = parser.parse_args()
    sys.stdout.write(
        json.dumps(asyncio.run(_run(args.mode, args.database, args.run_id)))
    )


if __name__ == "__main__":
    main()
