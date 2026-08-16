"""事件直播：以 events.jsonl 为权威源的 SSE 桥。.

``progress_sink`` 是 Runner 级同步回调（收所有 run 的事件、无序号、
异常被吞），不适合直接透传；``events.jsonl`` 则是 fsync 实时追加、
行号天然有序。因此：

- :class:`EventHub` 把 sink 转成按 run 分组的唤醒信号（非阻塞 ``set``）；
- :func:`follow_events` 从文件按行号游标回放 + 续播，
  行号即 SSE ``id``，浏览器断线重连凭 ``Last-Event-ID`` 无缝续传。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from opencompete.api.tasks import ResearchTaskRegistry
from opencompete.errors import PersistenceError
from opencompete.persistence.repository import RunRepository

logger = logging.getLogger(__name__)

# 这些状态下本进程不会再追加事件，直播可以收尾。
_SETTLED_STATUSES = {
    "awaiting_confirmation",
    "completed",
    "partial",
    "failed",
    "cancelled",
}

_POLL_INTERVAL_SECONDS = 2.0


class EventHub:
    """把 Runner 级 progress_sink 扇出为按 run 分组的唤醒信号。."""

    def __init__(self) -> None:
        """初始化空的等待者表。."""
        self._waiters: dict[str, set[asyncio.Event]] = {}

    def sink(self, event: dict[str, object]) -> None:
        """progress_sink 回调：只做非阻塞唤醒，绝不做任何可能阻塞的事。."""
        run_id = event.get("run_id")
        if not isinstance(run_id, str):
            return
        for waiter in self._waiters.get(run_id, ()):
            waiter.set()

    def register(self, run_id: str) -> asyncio.Event:
        """为一个直播连接注册唤醒信号。."""
        waiter = asyncio.Event()
        self._waiters.setdefault(run_id, set()).add(waiter)
        return waiter

    def unregister(self, run_id: str, waiter: asyncio.Event) -> None:
        """连接结束时移除唤醒信号。."""
        waiters = self._waiters.get(run_id)
        if waiters is None:
            return
        waiters.discard(waiter)
        if not waiters:
            self._waiters.pop(run_id, None)


def _read_complete_lines(
    path: Path, offset: int
) -> tuple[list[str], int]:
    """从字节偏移读取新增的完整行（以换行结尾），返回行与新偏移。.

    写端先攒后 fsync，防御性地不消费未以换行结尾的半行——
    偏移停在半行行首，等下一轮补齐。
    """
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
    except FileNotFoundError:
        return [], offset
    if not chunk:
        return [], offset
    last_newline = chunk.rfind(b"\n")
    if last_newline < 0:
        return [], offset
    complete = chunk[: last_newline + 1]
    lines = [
        line.decode("utf-8", errors="replace")
        for line in complete.splitlines()
        if line.strip()
    ]
    return lines, offset + last_newline + 1


def _run_settled(repository: RunRepository, run_id: str) -> bool:
    """该 run 是否处于本进程不会再写事件的状态。."""
    try:
        manifest = repository.load_manifest(run_id)
    except PersistenceError:
        return True
    return manifest.status.value in _SETTLED_STATUSES


async def follow_events(
    run_id: str,
    after: int,
    *,
    events_path: Path,
    hub: EventHub,
    registry: ResearchTaskRegistry,
    repository: RunRepository,
) -> AsyncIterator[dict[str, Any]]:
    """按行号游标产出 SSE 帧：先回放历史，再实时续播，收尾发 end 帧。.

    ``after`` 为客户端已收到的最大行号（0 表示全量回放）。
    终止条件：本进程无活动任务且 manifest 处于稳定状态，
    补读一轮确认无新行后发 ``event: end``。
    """
    waiter = hub.register(run_id)
    line_no = 0
    offset = 0
    try:
        while True:
            lines, offset = _read_complete_lines(events_path, offset)
            for line in lines:
                line_no += 1
                if line_no > after:
                    yield {"id": str(line_no), "data": line}
            if lines:
                continue
            if not registry.is_active(run_id) and _run_settled(
                repository, run_id
            ):
                # 补读一轮，覆盖"状态落盘与最后几行事件之间"的竞态。
                lines, offset = _read_complete_lines(events_path, offset)
                for line in lines:
                    line_no += 1
                    if line_no > after:
                        yield {"id": str(line_no), "data": line}
                yield {"event": "end", "data": "{}"}
                return
            waiter.clear()
            try:
                await asyncio.wait_for(
                    waiter.wait(), timeout=_POLL_INTERVAL_SECONDS
                )
            except TimeoutError:
                pass
    finally:
        hub.unregister(run_id, waiter)
