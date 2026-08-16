"""LangGraph SQLite Checkpointer 的应用级生命周期."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class SqliteCheckpointStore:
    """每次执行在同一事件循环内打开并关闭官方 Saver."""

    def __init__(self, database_path: str | Path) -> None:
        """绑定长期复用的 SQLite 文件路径."""
        self.database_path = Path(database_path)

    @asynccontextmanager
    async def open_saver(self) -> AsyncIterator[AsyncSqliteSaver]:
        """Open one Saver whose lifetime encloses graph compile and execution."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(
            str(self.database_path)
        ) as saver:
            yield saver
