"""进程内研究任务注册表。.

追踪两类"本进程正在执行"的工作：

- **后台任务**：范围确认（accepted=True）触发的完整研究，
  由 ``asyncio.create_task`` 执行并受容量上限约束；
- **同步在途**：create / 候选确认等在 handler 内直接 await 的调用，
  通过 :meth:`ResearchTaskRegistry.inflight` 标记。

两者统一由 :meth:`is_active` 查询，供事件直播的终止判断与
确认点安全回读门禁使用。任务异常的落盘由 ``_record_failure``
（application 层）保证，注册表只负责清理与日志。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from opencompete.api.errors import (
    CapacityExhaustedError,
    RunAlreadyActiveError,
)

logger = logging.getLogger(__name__)


class ResearchTaskRegistry:
    """run_id → 执行状态的进程内注册表。."""

    def __init__(self, capacity: int = 1) -> None:
        """限制同时执行的后台研究数量（SQLite 并发写保护）。."""
        self._capacity = capacity
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._inflight: set[str] = set()
        self._reservations: set[str] = set()

    def is_active(self, run_id: str) -> bool:
        """本进程是否正在为该 run 执行任何工作。."""
        return (
            run_id in self._inflight
            or run_id in self._tasks
            or run_id in self._reservations
        )

    def reserve(self, run_id: str) -> None:
        """原子预留后台研究槽，供耐久冻结与启动之间使用。."""
        if self.is_active(run_id):
            raise RunAlreadyActiveError(f"任务已在执行中: {run_id}")
        if len(self._tasks) + len(self._reservations) >= self._capacity:
            raise CapacityExhaustedError("研究执行容量已满")
        self._reservations.add(run_id)

    def release(self, run_id: str) -> None:
        """释放尚未变成后台任务的预留槽。."""
        self._reservations.discard(run_id)

    def start_reserved(
        self, run_id: str, coro: Coroutine[Any, Any, Any]
    ) -> asyncio.Task[Any]:
        """把已预留的执行槽转换为后台任务。."""
        if run_id not in self._reservations:
            coro.close()
            raise RuntimeError(f"任务没有可用预留槽: {run_id}")
        self._reservations.remove(run_id)
        task = asyncio.create_task(coro, name=f"research:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda done: self._on_done(run_id, done))
        return task

    def submit(
        self, run_id: str, coro: Coroutine[Any, Any, Any]
    ) -> asyncio.Task[Any]:
        """登记并启动一个后台研究任务。.

        容量已满或同一 run 已在执行时抛 :class:`CapacityExhaustedError` /
        :class:`RuntimeError`，并关闭传入的协程避免 ``never awaited`` 警告。
        """
        try:
            self.reserve(run_id)
        except Exception:
            coro.close()
            raise
        return self.start_reserved(run_id, coro)

    def _on_done(self, run_id: str, task: asyncio.Task[Any]) -> None:
        """任务收尾：移除登记并消费异常（落盘已由应用层完成）。."""
        self._tasks.pop(run_id, None)
        if task.cancelled():
            logger.info("研究任务已按用户请求取消: %s", run_id)
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "研究任务失败: %s (%s)", run_id, type(exc).__name__
            )

    def request_cancel(self, run_id: str) -> asyncio.Task[Any] | None:
        """请求取消运行中的后台任务；返回该任务供调用方等待收尾。.

        终态落盘由应用层 :meth:`guard_cancellation` 包装完成；不存在
        运行中的任务时返回 ``None``。
        """
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return None
        task.cancel()
        return task

    async def wait_idle(self, run_id: str, timeout: float = 10.0) -> None:
        """等待该 run 的后台任务收尾（到达确认点的执行即将结束）。.

        确认事件先于协程收尾落盘是正常时序：确认端点必须在预留执行
        槽前等完这段窗口，否则用户的确认请求会与收尾竞态并收到 409。
        任务自身的失败已由应用层落盘，这里吞掉传播。
        """
        task = self._tasks.get(run_id)
        if task is None:
            return
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), timeout)

    @asynccontextmanager
    async def inflight(self, run_id: str) -> AsyncIterator[None]:
        """把一次同步 await 的调用标记为在途。."""
        if self.is_active(run_id):
            raise RunAlreadyActiveError(f"任务已在执行中: {run_id}")
        self._inflight.add(run_id)
        try:
            yield
        finally:
            self._inflight.discard(run_id)

    async def shutdown(self, grace_seconds: float = 5.0) -> None:
        """进程关闭前给活动任务一个宽限期，超时则放弃等待。.

        不主动取消任务：``CancelledError`` 会绕过应用层的失败落盘，
        留下无法恢复的中间态 manifest。
        """
        pending = [task for task in self._tasks.values() if not task.done()]
        if not pending:
            return
        _done, still_pending = await asyncio.wait(
            pending, timeout=grace_seconds
        )
        if still_pending:
            logger.warning(
                "关闭时仍有 %d 个研究任务未完成，受影响 run: %s",
                len(still_pending),
                ", ".join(sorted(self._tasks)),
            )
