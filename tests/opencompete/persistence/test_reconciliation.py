from __future__ import annotations

import pytest

from opencompete.persistence.models import (
    ArtifactSnapshot,
    CheckpointSnapshot,
    ReconcileAction,
)
from opencompete.persistence.reconciliation import reconcile


@pytest.mark.parametrize(
    ("checkpoint", "artifacts", "expected"),
    [
        (
            CheckpointSnapshot("run-1", 1, 0, "researching", True),
            None,
            ReconcileAction.CONTINUE_GRAPH,
        ),
        (
            CheckpointSnapshot("run-1", 1, 2, "completed", False),
            ArtifactSnapshot("run-1", 1, 2, "completed", True, True, True),
            ReconcileAction.NOOP,
        ),
        (
            CheckpointSnapshot("run-1", 1, 2, "completed", False),
            ArtifactSnapshot("run-1", 1, 2, "completed", False, True, False),
            ReconcileAction.REBUILD_ARTIFACTS,
        ),
        (
            None,
            ArtifactSnapshot("run-1", 1, 2, "completed", True, True, True),
            ReconcileAction.HISTORY_ONLY,
        ),
        (
            CheckpointSnapshot("run-1", 1, 1, "researching", True),
            ArtifactSnapshot("run-1", 1, 2, "completed", True, True, True),
            ReconcileAction.CONFLICT,
        ),
        (
            CheckpointSnapshot("run-1", 1, 1, "researching", True),
            ArtifactSnapshot("run-1", 2, 1, "researching", True, True, True),
            ReconcileAction.CONFLICT,
        ),
        (None, None, ReconcileAction.UNRECOVERABLE),
    ],
)
def test_reconcile_matrix(
    checkpoint: CheckpointSnapshot | None,
    artifacts: ArtifactSnapshot | None,
    expected: ReconcileAction,
) -> None:
    assert reconcile(checkpoint, artifacts).action is expected


def test_invalid_completed_artifacts_are_not_history_only() -> None:
    artifacts = ArtifactSnapshot(
        "run-1", 1, 2, "completed", False, True, False
    )

    decision = reconcile(None, artifacts)

    assert decision.action is ReconcileAction.UNRECOVERABLE
