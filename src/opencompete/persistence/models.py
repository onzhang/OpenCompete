"""持久化边界内部使用的不可变快照和对账决策."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from opencompete.models import RunBundle, RunManifest


class ReconcileAction(str, Enum):
    """Checkpoint 与业务产物比较后的唯一后续动作."""

    CONTINUE_GRAPH = "continue_graph"
    REBUILD_ARTIFACTS = "rebuild_artifacts"
    NOOP = "noop"
    HISTORY_ONLY = "history_only"
    CONFLICT = "conflict"
    UNRECOVERABLE = "unrecoverable"


@dataclass(frozen=True, slots=True)
class CheckpointSnapshot:
    """对账所需的最小 Checkpoint 元数据."""

    run_id: str
    scope_revision: int
    artifact_revision: int
    status: str
    recoverable: bool


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    """对账所需的正式业务产物校验结果."""

    run_id: str
    scope_revision: int
    artifact_revision: int
    status: str
    manifest_valid: bool
    bundle_valid: bool
    report_valid: bool

    @property
    def is_valid(self) -> bool:
        """Return whether all files agree with the published manifest."""
        return self.manifest_valid and self.bundle_valid and self.report_valid


@dataclass(frozen=True, slots=True)
class ReconcileDecision:
    """纯对账函数的结果，包含稳定动作和可诊断原因."""

    action: ReconcileAction
    reason: str


@dataclass(frozen=True, slots=True)
class PublishedRun:
    """通过 revision 与内容哈希校验的一组正式业务产物."""

    manifest: RunManifest
    bundle: RunBundle
    report: str
