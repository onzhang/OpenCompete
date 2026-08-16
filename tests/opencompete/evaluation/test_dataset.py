"""固定评测数据集加载契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opencompete.evaluation.dataset import (
    DraftReferenceError,
    load_dataset,
    load_targets,
)

DATASET = Path("benchmarks/opencompete/v1")


def test_dataset_defaults_to_rejecting_unreviewed_references() -> None:
    with pytest.raises(DraftReferenceError, match="人工审核"):
        load_dataset(DATASET)


def test_dataset_loads_all_eight_draft_cases_only_when_explicitly_allowed() -> None:
    dataset = load_dataset(DATASET / "dataset.json", allow_draft=True)

    assert dataset.dataset_id == "opencompete-bench-v1"
    assert dataset.version == "1.0.0"
    assert len(dataset.cases) == 8
    assert len({case.case_id for case in dataset.cases}) == 8
    assert all(case.reference_review.status.value == "draft" for case in dataset.cases)
    assert all(case.prompt.strip() and case.reference_report.strip() for case in dataset.cases)


def test_targets_loader_validates_batch_binding_contract(tmp_path: Path) -> None:
    path = tmp_path / "targets.json"
    path.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "case_id": "ocb-001-aws-lambda-vs-azure-functions",
                        "run_id": "run-001",
                        "artifact_revision": 3,
                    },
                    {
                        "case_id": "ocb-002-m365-vs-google-workspace",
                        "run_id": "run-002",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    targets = load_targets(path)

    assert len(targets) == 2
    assert targets[0].artifact_revision == 3
    assert targets[1].artifact_revision is None


def test_targets_loader_rejects_duplicate_bindings(tmp_path: Path) -> None:
    path = tmp_path / "targets.json"
    target = {"case_id": "ocb-001", "run_id": "run-001"}
    path.write_text(
        json.dumps({"targets": [target, target]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="重复"):
        load_targets(path)
