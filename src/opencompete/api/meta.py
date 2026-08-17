"""前端展示所需补充元数据的 sidecar 索引。.

``RunSummary``/``RunManifest`` 不含研究问题与对象名单（它们只存在于
Checkpoint 状态里），因此 API 层在 ``<data_dir>/api-meta/<run_id>.json``
自行维护一份只读补充索引：

- 创建任务时写入 query / entities / confirmation_kind；
- 历史任务首次被访问时惰性回填（从已发布 Bundle 推导）；
- status、revision、预算永远以 manifest 为准，本索引只存不可变字段。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from opencompete.errors import PersistenceError
from opencompete.persistence.repository import RunRepository

logger = logging.getLogger(__name__)

_PLACEHOLDER: dict[str, Any] = {
    "query": None,
    "entities": [],
    "confirmation_kind": None,
    "created_at": None,
}


class RunMetaStore:
    """按 run 存取前端补充元数据的文件存储。."""

    def __init__(self, data_dir: Path) -> None:
        """在数据目录旁挂载 ``api-meta/`` 索引目录。."""
        self._root = Path(data_dir) / "api-meta"

    def _path(self, run_id: str) -> Path:
        """拒绝路径穿越后返回索引文件路径。."""
        if not run_id or Path(run_id).name != run_id:
            raise ValueError(f"非法 run_id: {run_id!r}")
        return self._root / f"{run_id}.json"

    def read(self, run_id: str) -> dict[str, Any] | None:
        """读取索引；不存在或损坏时返回 None。."""
        try:
            raw = self._path(run_id).read_text(encoding="utf-8")
            data = json.loads(raw)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            logger.warning("api-meta 损坏，将回填: %s", run_id)
            return None
        return {**_PLACEHOLDER, **data} if isinstance(data, dict) else None

    def write(self, run_id: str, meta: dict[str, Any]) -> None:
        """原子写入索引；失败仅记录日志，不阻断主流程。."""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {**_PLACEHOLDER, **meta}, ensure_ascii=False, indent=2
            )
            fd, tmp_path = tempfile.mkstemp(
                dir=self._root, suffix=".tmp", text=False
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                os.replace(tmp_path, self._path(run_id))
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        except OSError:
            logger.warning("api-meta 写入失败: %s", run_id, exc_info=True)

    def delete(self, run_id: str) -> None:
        """删除索引文件；不存在或失败只记录日志（展示层缓存数据）。."""
        try:
            self._path(run_id).unlink(missing_ok=True)
        except OSError:
            logger.warning("api-meta 删除失败: %s", run_id, exc_info=True)

    def merge(self, run_id: str, **fields: Any) -> dict[str, Any]:
        """读取、合并并写回索引，返回合并后的索引。."""
        merged = {**(self.read(run_id) or _PLACEHOLDER), **fields}
        self.write(run_id, merged)
        return merged

    def ensure(self, run_id: str, repository: RunRepository) -> dict[str, Any]:
        """读取索引，缺失时从已发布产物惰性回填。.

        已发布任务：取 ``bundle.scope.entities`` 的名称列表，报告首行
        ``# 标题`` 充当研究问题；无产物的任务（如 failed）写入空占位，
        避免每次列表访问都重复尝试。
        """
        cached = self.read(run_id)
        if cached is not None:
            return cached
        meta = dict(_PLACEHOLDER)
        try:
            bundle = repository.load_verified(run_id).bundle
        except PersistenceError:
            bundle = None
        if bundle is not None:
            meta["entities"] = [entity.name for entity in bundle.scope.entities]
            first_line = bundle.report.markdown.lstrip().splitlines()[0]
            meta["query"] = first_line.lstrip("#").strip() or None
            meta["created_at"] = bundle.created_at.isoformat()
        self.write(run_id, meta)
        return meta
