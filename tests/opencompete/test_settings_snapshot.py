from __future__ import annotations

from pathlib import Path

import pytest
from test_application import _runner

from opencompete.models import ResearchEntity, ResearchRequest
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
