from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_application import _runner

from opencompete.models import (
    ResearchEntity,
    ResearchRequest,
    ScopeConfirmation,
)
from opencompete.settings import OpenCompeteSettings


@pytest.mark.asyncio
async def test_reconfigured_defaults_only_apply_to_new_run_snapshots(
    tmp_path: Path,
) -> None:
    runner, _runtime, repository = _runner(tmp_path)
    request = ResearchRequest(
        query="比较 Alpha 与 Beta",
        entities=(ResearchEntity(name="Alpha"), ResearchEntity(name="Beta")),
    )

    initial_settings = runner.settings
    first = await runner.create_run(request)
    runner.reconfigure_default_settings(
        OpenCompeteSettings(
            data_dir=tmp_path,
            research_model="openai:updated-model",
            summarization_model="openai:updated-model",
            compression_model="openai:updated-model",
            final_report_model="openai:updated-model",
            max_input_tokens=123456,
        ),
        revision=7,
    )
    second = await runner.create_run(request)

    first_manifest = repository.load_manifest(first.run_id)
    second_manifest = repository.load_manifest(second.run_id)

    assert first_manifest.config_snapshot["settings_revision"] == 0
    assert first_manifest.config_snapshot["settings"]["research_model"] == (
        initial_settings.research_model
    )
    assert first_manifest.config_snapshot["settings"]["max_input_tokens"] == (
        initial_settings.max_input_tokens
    )
    assert second_manifest.config_snapshot["settings_revision"] == 7
    assert second_manifest.config_snapshot["settings"]["research_model"] == (
        "openai:updated-model"
    )
    assert second_manifest.config_snapshot["settings"]["max_input_tokens"] == 123456
    assert runner._settings_for_run(first.run_id).research_model == initial_settings.research_model  # noqa: SLF001
    assert runner._settings_for_run(first.run_id).max_input_tokens == initial_settings.max_input_tokens  # noqa: SLF001


@pytest.mark.asyncio
async def test_explicit_resume_uses_current_model_runtime_and_original_budget(
    tmp_path: Path,
) -> None:
    runner, runtime, repository = _runner(tmp_path)
    request = ResearchRequest(
        query="比较 Alpha 与 Beta",
        entities=(ResearchEntity(name="Alpha"), ResearchEntity(name="Beta")),
    )
    pending = await runner.create_run(request)
    assert pending.scope is not None
    original_manifest = repository.load_manifest(pending.run_id)

    runner.reconfigure_default_settings(
        OpenCompeteSettings(
            data_dir=tmp_path,
            research_model="openai:replacement-model",
            summarization_model="openai:replacement-summary",
            compression_model="openai:replacement-compression",
            final_report_model="openai:replacement-report",
            fallback_model="openai:replacement-fallback",
            fallback_connection_format="openai_chat_completions",
            max_input_tokens=987654,
        ),
        revision=12,
    )
    completed = await runner.resume_run(
        pending.run_id,
        ScopeConfirmation(
            accepted=True,
            expected_revision=pending.scope.revision,
        ),
    )

    assert completed.status.value == "completed"
    assert runtime.last_configurable is not None
    assert runtime.last_configurable["research_model"] == "openai:replacement-model"
    assert runtime.last_configurable["fallback_model"] == "openai:replacement-fallback"
    assert runtime.budget_guard is not None
    assert runtime.budget_guard._limit.input_tokens == (  # noqa: SLF001
        original_manifest.budget_summary.limit.input_tokens
    )
    assert repository.load_manifest(pending.run_id).config_snapshot == (
        original_manifest.config_snapshot
    )

    events_path = tmp_path / "runs" / pending.run_id / "events.jsonl"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    audit = [event for event in events if event["event"] == "resume_runtime_selected"]
    assert audit == [
        {
            "event": "resume_runtime_selected",
            "run_id": pending.run_id,
            "execution_id": audit[0]["execution_id"],
            "settings_revision": 12,
            "primary_connection_role": "primary",
            "primary_model": "openai:replacement-model",
            "fallback_connection_role": "fallback",
            "fallback_model": "openai:replacement-fallback",
            "occurred_at": audit[0]["occurred_at"],
        }
    ]
    assert "API_KEY" not in str(audit)
    assert "BASE_URL" not in str(audit)
