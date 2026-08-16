"""正式生命周期中 API Key 与认证头零落盘验收。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from opencompete.application import ApplicationRunner
from opencompete.evaluation import (
    EvaluationRequest,
    EvaluationService,
    EvaluationTarget,
)
from opencompete.models import RunStatus, ScopeConfirmation
from opencompete.persistence import RunRepository
from opencompete.settings import OpenCompeteSettings

sys.path.insert(0, str(Path(__file__).parent / "helpers"))

from acceptance_support import (  # noqa: E402
    CASE_ID,
    AcceptanceExtractor,
    AcceptanceJudge,
    acceptance_request,
    build_acceptance_research_graph,
    write_acceptance_dataset,
)

SECRET_SENTINEL = "sk-opencompete-persistence-sentinel-7B3A91D5"
AUTHORIZATION_SENTINEL = f"Authorization: Bearer {SECRET_SENTINEL}"


@pytest.mark.asyncio
async def test_secret_sentinel_is_absent_from_sqlite_and_every_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描 Checkpoint、业务产物、事件和评测结果，固定哨兵必须为零。"""
    monkeypatch.setenv("OPENAI_API_KEY", SECRET_SENTINEL)
    monkeypatch.setenv("TAVILY_API_KEY", SECRET_SENTINEL)
    data_root = tmp_path / "data"
    repository = RunRepository(data_root)
    extractor = AcceptanceExtractor()
    runner = ApplicationRunner(
        OpenCompeteSettings(data_dir=data_root),
        repository=repository,
        research_graph=build_acceptance_research_graph(),
        extractor=extractor,
        evaluation_service=EvaluationService(
            repository=repository,
            judge=AcceptanceJudge(),
        ),
    )

    pending = await runner.create_run(acceptance_request())
    assert pending.scope is not None
    completed = await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )
    assert completed.status is RunStatus.COMPLETED
    repository.append_event(
        pending.run_id,
        {
            "event": "脱敏验收",
            "authorization": f"Bearer {SECRET_SENTINEL}",
            "detail": AUTHORIZATION_SENTINEL,
        },
    )
    dataset = write_acceptance_dataset(tmp_path / "benchmark", pending.scope)
    await runner.evaluate_dataset(
        EvaluationRequest(
            dataset=dataset,
            targets=(
                EvaluationTarget(case_id=CASE_ID, run_id=pending.run_id),
            ),
            allow_draft=True,
        )
    )

    assert extractor.configurations
    assert all(
        SECRET_SENTINEL not in repr(config)
        and AUTHORIZATION_SENTINEL not in repr(config)
        for config in extractor.configurations
    )

    persisted_files = sorted(path for path in data_root.rglob("*") if path.is_file())
    persisted_names = {path.name for path in persisted_files}
    assert {
        "checkpoints.sqlite3",
        "manifest.json",
        "bundle.json",
        "report.md",
        "events.jsonl",
    } <= persisted_names
    assert any(
        path.parent.name == "evaluations" and path.suffix == ".json"
        for path in persisted_files
    )
    for path in persisted_files:
        payload = path.read_bytes()
        assert SECRET_SENTINEL.encode() not in payload, path
        assert AUTHORIZATION_SENTINEL.encode() not in payload, path

    events = (
        data_root / "runs" / pending.run_id / "events.jsonl"
    ).read_text(encoding="utf-8")
    assert "[REDACTED]" in events
