"""单进程任务注册表的冲突语义测试。"""

from __future__ import annotations

import pytest

from opencompete.api.errors import CapacityExhaustedError, RunAlreadyActiveError
from opencompete.api.tasks import ResearchTaskRegistry


def test_same_run_reservation_is_conflict_but_other_run_is_capacity_exhausted() -> None:
    registry = ResearchTaskRegistry(capacity=1)

    registry.reserve("run-a")

    with pytest.raises(RunAlreadyActiveError):
        registry.reserve("run-a")
    with pytest.raises(CapacityExhaustedError):
        registry.reserve("run-b")

    registry.release("run-a")
    registry.reserve("run-b")
    assert registry.is_active("run-b") is True
