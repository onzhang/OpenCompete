from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opencompete.errors import PersistenceError
from opencompete.models import (
    AnalysisLevel,
    BudgetEstimate,
    BudgetSnapshot,
    ReportArtifact,
    ResearchEntity,
    RunBundle,
    RunManifest,
    RunStatus,
    ScopeSpec,
)
from opencompete.persistence.repository import RunRepository


def _scope(revision: int = 1) -> ScopeSpec:
    return ScopeSpec(
        entities=(ResearchEntity(name="对象 A"),),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("pricing",)},
        confirmed_at=datetime.now(UTC),
        revision=revision,
        frozen=True,
    )


def _budget() -> BudgetSnapshot:
    estimate = BudgetEstimate(tavily_calls=1, cost_usd=0.5)
    return BudgetSnapshot(
        limit=estimate,
        spent=estimate,
        reserved=BudgetEstimate(),
        remaining=BudgetEstimate(),
    )


def _bundle(run_id: str = "run-1", revision: int = 1) -> RunBundle:
    return RunBundle(
        run_id=run_id,
        scope=_scope(),
        report=ReportArtifact(markdown="# 竞品分析\n\n已验证内容。"),
        budget=_budget(),
        status=RunStatus.COMPLETED,
        artifact_revision=revision,
    )


def _manifest(
    status: RunStatus,
    *,
    run_id: str = "run-1",
    scope_revision: int = 1,
    artifact_revision: int = 1,
) -> RunManifest:
    now = datetime.now(UTC)
    return RunManifest(
        run_id=run_id,
        status=status,
        scope_revision=scope_revision,
        artifact_revision=artifact_revision,
        budget_summary=_budget(),
        created_at=now,
        updated_at=now,
    )


def test_publish_writes_bundle_and_report_before_verified_manifest(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(_manifest(RunStatus.RESEARCHING))

    published = repository.publish(
        _bundle(),
        _manifest(RunStatus.COMPLETED),
    )

    run_dir = tmp_path / ".opencompete-data" / "runs" / "run-1"
    assert (run_dir / "evaluations").is_dir()
    assert published.report == "# 竞品分析\n\n已验证内容。"
    assert published.manifest.file_hashes == {
        "bundle.json": hashlib.sha256(
            (run_dir / "bundle.json").read_bytes()
        ).hexdigest(),
        "report.md": hashlib.sha256(
            (run_dir / "report.md").read_bytes()
        ).hexdigest(),
    }
    assert repository.load_verified("run-1") == published


def test_published_artifact_revision_cannot_be_overwritten(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(_manifest(RunStatus.RESEARCHING))
    repository.publish(_bundle(), _manifest(RunStatus.COMPLETED))
    changed = _bundle().model_copy(
        update={
            "report": ReportArtifact(markdown="# 被替换的报告"),
        }
    )

    with pytest.raises(PersistenceError, match="不能被覆盖"):
        repository.publish(changed, _manifest(RunStatus.COMPLETED))

    assert repository.load_verified("run-1").report == "# 竞品分析\n\n已验证内容。"


def test_unpublished_manifest_cannot_roll_back_artifact_revision(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(
        _manifest(RunStatus.FAILED, artifact_revision=2)
    )

    with pytest.raises(PersistenceError, match="不能回退"):
        repository.publish(
            _bundle(revision=1),
            _manifest(RunStatus.COMPLETED, artifact_revision=1),
        )

    assert repository.load_manifest("run-1").artifact_revision == 2


def test_unpublished_manifest_allows_same_revision_crash_rebuild(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(
        _manifest(RunStatus.FAILED, artifact_revision=2)
    )

    rebuilt = repository.publish(
        _bundle(revision=2),
        _manifest(RunStatus.COMPLETED, artifact_revision=2),
    )

    assert rebuilt.manifest.artifact_revision == 2
    assert repository.load_verified("run-1") == rebuilt


def test_update_manifest_records_only_non_published_statuses(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(
        _manifest(
            RunStatus.DRAFT,
            scope_revision=0,
            artifact_revision=0,
        )
    )

    summary = repository.update_manifest(
        _manifest(
            RunStatus.AWAITING_CONFIRMATION,
            scope_revision=0,
            artifact_revision=0,
        )
    )

    assert summary.status is RunStatus.AWAITING_CONFIRMATION
    assert repository.list_runs()[0].status is RunStatus.AWAITING_CONFIRMATION
    with pytest.raises(PersistenceError, match="publish"):
        repository.update_manifest(_manifest(RunStatus.PARTIAL))


def test_checkpoint_manifest_rebuild_is_non_published_and_revision_monotonic(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(
        _manifest(
            RunStatus.DRAFT,
            scope_revision=0,
            artifact_revision=0,
        )
    )
    recovered = _manifest(
        RunStatus.RESEARCHING,
        scope_revision=3,
        artifact_revision=2,
    )

    with pytest.raises(PersistenceError, match="仍可读取"):
        repository.rebuild_manifest_index(recovered)

    manifest_path = (
        tmp_path
        / ".opencompete-data"
        / "runs"
        / "run-1"
        / "manifest.json"
    )
    manifest_path.write_text("不是合法 JSON", encoding="utf-8")
    with pytest.raises(PersistenceError, match="不能冒充"):
        repository.rebuild_manifest_index(
            _manifest(
                RunStatus.COMPLETED,
                scope_revision=3,
                artifact_revision=2,
            )
        )

    summary = repository.rebuild_manifest_index(recovered)

    assert summary.status is RunStatus.RESEARCHING
    assert summary.scope_revision == 3
    assert summary.artifact_revision == 2
    with pytest.raises(PersistenceError, match="不能回退"):
        repository.update_manifest(
            _manifest(
                RunStatus.RESEARCHING,
                scope_revision=2,
                artifact_revision=1,
            )
        )


def test_manifest_failure_never_exposes_new_revision_as_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(
        _manifest(RunStatus.RESEARCHING, artifact_revision=0)
    )
    original = repository._atomic_write

    def fail_manifest(path: Path, payload: bytes) -> None:
        if path.name == "manifest.json":
            raise OSError("simulated manifest crash")
        original(path, payload)

    monkeypatch.setattr(repository, "_atomic_write", fail_manifest)

    with pytest.raises(PersistenceError):
        repository.publish(_bundle(), _manifest(RunStatus.COMPLETED))

    summaries = repository.list_runs()
    assert len(summaries) == 1
    assert summaries[0].status is RunStatus.RESEARCHING
    assert repository.inspect("run-1").manifest_valid is False


def test_hash_mismatch_is_rejected_then_rebuilt_from_bundle(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(_manifest(RunStatus.RESEARCHING))
    repository.publish(_bundle(), _manifest(RunStatus.COMPLETED))
    report_path = (
        tmp_path
        / ".opencompete-data"
        / "runs"
        / "run-1"
        / "report.md"
    )
    report_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(PersistenceError, match="哈希"):
        repository.load_verified("run-1")
    assert repository.inspect("run-1").report_valid is False
    assert repository.list_runs()[0].status is RunStatus.FAILED

    repaired = repository.repair_from_bundle("run-1")

    assert repaired.report == "# 竞品分析\n\n已验证内容。"
    assert repository.load_verified("run-1") == repaired


def test_revision_mismatch_is_rejected(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(_manifest(RunStatus.RESEARCHING))

    with pytest.raises(PersistenceError, match="revision"):
        repository.publish(
            _bundle(revision=2),
            _manifest(RunStatus.COMPLETED, artifact_revision=1),
        )


def test_nested_secret_configuration_key_is_rejected(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / ".opencompete-data")
    manifest = _manifest(RunStatus.RESEARCHING).model_copy(
        update={"config_snapshot": {"provider": {"tavily_api_key": "secret"}}}
    )

    with pytest.raises(PersistenceError, match="密钥字段"):
        repository.initialize(manifest)


def test_events_are_append_only_and_secret_sentinel_never_reaches_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "SECRET-SENTINEL-DO-NOT-PERSIST"
    monkeypatch.setenv("TAVILY_API_KEY", secret)
    repository = RunRepository(tmp_path / ".opencompete-data")
    repository.initialize(_manifest(RunStatus.RESEARCHING))
    repository.append_event(
        "run-1",
        {
            "run_id": "run-1",
            "node": "research",
            "message": f"Authorization: Bearer {secret}",
            "api_key": secret,
        },
    )
    repository.append_event(
        "run-1",
        {"run_id": "run-1", "node": "synthesize", "duration": 0.5},
    )
    repository.publish(_bundle(), _manifest(RunStatus.COMPLETED))

    data_root = tmp_path / ".opencompete-data"
    checkpoint_worker = (
        Path(__file__).parent / "helpers" / "checkpoint_worker.py"
    )
    subprocess.run(
        [
            sys.executable,
            str(checkpoint_worker),
            "create",
            str(data_root / "checkpoints.sqlite3"),
            "secret-scan-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    events = (
        data_root / "runs" / "run-1" / "events.jsonl"
    ).read_text(encoding="utf-8")
    assert len(events.splitlines()) == 2
    for path in data_root.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
