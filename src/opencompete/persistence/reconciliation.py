"""Checkpoint 与正式业务产物之间的确定性对账规则."""

from __future__ import annotations

from opencompete.persistence.models import (
    ArtifactSnapshot,
    CheckpointSnapshot,
    ReconcileAction,
    ReconcileDecision,
)

_TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}


def _decision(action: ReconcileAction, reason: str) -> ReconcileDecision:
    return ReconcileDecision(action=action, reason=reason)


def reconcile(
    checkpoint: CheckpointSnapshot | None,
    artifacts: ArtifactSnapshot | None,
) -> ReconcileDecision:
    """Compare both stores without performing IO or silently resolving conflicts."""
    if checkpoint is None:
        if artifacts is None:
            return _decision(
                ReconcileAction.UNRECOVERABLE,
                "Checkpoint 和正式业务产物均不存在。",
            )
        if artifacts.is_valid and artifacts.status in _TERMINAL_STATUSES:
            return _decision(
                ReconcileAction.HISTORY_ONLY,
                "Checkpoint 不存在，但终态业务产物完整，只允许历史查看。",
            )
        return _decision(
            ReconcileAction.UNRECOVERABLE,
            "Checkpoint 不存在，且业务产物不是可验证的终态版本。",
        )

    if artifacts is not None:
        if checkpoint.run_id != artifacts.run_id:
            return _decision(
                ReconcileAction.CONFLICT,
                "Checkpoint 与业务产物的 run_id 不一致。",
            )
        if checkpoint.scope_revision != artifacts.scope_revision:
            return _decision(
                ReconcileAction.CONFLICT,
                "Checkpoint 与业务产物的 Scope revision 冲突。",
            )
        if artifacts.artifact_revision > checkpoint.artifact_revision:
            return _decision(
                ReconcileAction.CONFLICT,
                "业务产物 revision 超前于 Checkpoint，拒绝猜测覆盖。",
            )

    checkpoint_is_terminal = checkpoint.status in _TERMINAL_STATUSES
    if not checkpoint_is_terminal:
        if checkpoint.recoverable:
            return _decision(
                ReconcileAction.CONTINUE_GRAPH,
                "Checkpoint 仍处于可恢复的运行状态。",
            )
        return _decision(
            ReconcileAction.UNRECOVERABLE,
            "Checkpoint 不是终态且无法恢复。",
        )

    if artifacts is None:
        return _decision(
            ReconcileAction.REBUILD_ARTIFACTS,
            "终态 Checkpoint 尚未发布业务产物。",
        )
    if artifacts.artifact_revision < checkpoint.artifact_revision:
        return _decision(
            ReconcileAction.REBUILD_ARTIFACTS,
            "业务产物 revision 落后于终态 Checkpoint。",
        )
    if not artifacts.is_valid or artifacts.status != checkpoint.status:
        return _decision(
            ReconcileAction.REBUILD_ARTIFACTS,
            "正式业务产物与终态 Checkpoint 不一致。",
        )
    return _decision(
        ReconcileAction.NOOP,
        "Checkpoint 与正式业务产物一致。",
    )
