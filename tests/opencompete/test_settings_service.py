from __future__ import annotations

import os
from pathlib import Path

import pytest

from opencompete.errors import DataValidationError
from opencompete.models import RunStatus
from opencompete.settings_service import SettingsService


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SettingsService:
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "GOOGLE_API_KEY",
        "TAVILY_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "opencompete.toml"
    config_path.write_text(
        "# 必须保留的用户注释\nresearch_model = \"openai:baseline\"\n",
        encoding="utf-8",
    )
    return SettingsService(config_path, startup_environment={})


@pytest.mark.asyncio
async def test_public_view_excludes_credentials_and_marks_toml_source(
    service: SettingsService,
) -> None:
    view = await service.public_view()

    assert view["fields"]["research_model"]["value"] == "openai:baseline"
    assert view["fields"]["research_model"]["source"] == "TOML"
    assert view["effective_for"] == (
        "仅新建任务；已创建任务继续使用创建时的非秘密设置快照"
    )
    assert "credential-sentinel-value" not in str(view)
    assert all("value" not in item for item in view["credentials"])


@pytest.mark.asyncio
async def test_public_view_excludes_internal_and_evaluation_fields(
    service: SettingsService,
) -> None:
    view = await service.public_view()
    hidden = {
        "judge_model",
        "primary_disable_thinking",
        "fallback_disable_thinking",
        "long_form_streaming",
        "llm_input_cost_per_million_usd",
        "llm_output_cost_per_million_usd",
        "tavily_cost_per_call_usd",
        "price_config_version",
        "max_supervisor_model_turns",
        "max_content_length",
    }

    assert not hidden & set(view["fields"])
    assert set().union(*view["groups"].values()) == set(view["fields"])


@pytest.mark.asyncio
async def test_update_settings_preserves_comments_and_rejects_stale_revision(
    service: SettingsService,
) -> None:
    current = await service.public_view()
    updated = await service.update_settings(
        {"max_input_tokens": 345678},
        expected_revision=current["revision"],
    )

    content = service.config_path.read_text(encoding="utf-8")
    assert "# 必须保留的用户注释" in content
    assert "max_input_tokens = 345678" in content
    assert updated["fields"]["max_input_tokens"]["value"] == 345678

    with pytest.raises(DataValidationError, match="版本已变化"):
        await service.update_settings(
            {"max_output_tokens": 200000},
            expected_revision=current["revision"],
        )


@pytest.mark.asyncio
async def test_settings_page_rejects_internal_field_updates(
    service: SettingsService,
) -> None:
    current = await service.public_view()

    for name, value in (
        ("judge_model", "openai:judge"),
        ("primary_disable_thinking", True),
        ("long_form_streaming", False),
        ("llm_input_cost_per_million_usd", 1.0),
        ("max_supervisor_model_turns", 11),
    ):
        with pytest.raises(DataValidationError, match="不允许由页面修改"):
            await service.update_settings(
                {name: value}, expected_revision=current["revision"]
            )


@pytest.mark.asyncio
async def test_settings_page_rejects_unsupported_model_provider(
    service: SettingsService,
) -> None:
    current = await service.public_view()

    with pytest.raises(DataValidationError, match="外部环境管理"):
        await service.update_settings(
            {"research_model": "unsupported:model"},
            expected_revision=current["revision"],
        )


@pytest.mark.asyncio
async def test_external_environment_setting_cannot_be_overwritten(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "opencompete.toml"
    config_path.write_text("", encoding="utf-8")
    service = SettingsService(
        config_path,
        startup_environment={"OPENCOMPETE_MAX_INPUT_TOKENS": "999"},
    )
    view = await service.public_view()

    assert view["fields"]["max_input_tokens"]["source"] == "外部环境"
    with pytest.raises(DataValidationError, match="外部环境"):
        await service.update_settings(
            {"max_input_tokens": 123}, expected_revision=view["revision"]
        )


@pytest.mark.asyncio
async def test_credential_set_clear_and_zero_echo(
    service: SettingsService,
) -> None:
    secret = "credential-sentinel-value"
    current = await service.public_view()
    updated = await service.update_credential(
        "OPENAI_API_KEY",
        operation="set",
        value=secret,
        expected_revision=current["revision"],
        active_run_statuses={},
    )

    assert os.environ["OPENAI_API_KEY"] == secret
    assert secret in service.dotenv_path.read_text(encoding="utf-8")
    assert secret not in str(updated)
    state = next(
        item for item in updated["credentials"] if item["name"] == "OPENAI_API_KEY"
    )
    assert state == {
        "name": "OPENAI_API_KEY",
        "configured": True,
        "source": "本地 .env",
        "manageable": True,
    }

    cleared = await service.update_credential(
        "OPENAI_API_KEY",
        operation="clear",
        value=None,
        expected_revision=updated["revision"],
        active_run_statuses={},
    )
    assert "OPENAI_API_KEY" not in os.environ
    assert "OPENAI_API_KEY" not in service.dotenv_path.read_text(encoding="utf-8")
    assert secret not in str(cleared)


@pytest.mark.asyncio
async def test_credential_rotation_is_blocked_by_nonterminal_run(
    service: SettingsService,
) -> None:
    current = await service.public_view()

    with pytest.raises(DataValidationError, match="未终态任务"):
        await service.update_credential(
            "TAVILY_API_KEY",
            operation="set",
            value="credential-sentinel-value",
            expected_revision=current["revision"],
            active_run_statuses={"run-a": RunStatus.AWAITING_CONFIRMATION},
        )


@pytest.mark.asyncio
async def test_base_url_requires_public_https_without_credentials(
    service: SettingsService,
) -> None:
    current = await service.public_view()
    with pytest.raises(DataValidationError, match="HTTPS"):
        await service.update_credential(
            "OPENAI_BASE_URL",
            operation="set",
            value="http://api.example.com/v1",
            expected_revision=current["revision"],
            active_run_statuses={},
        )
    with pytest.raises(DataValidationError, match="用户名或密码"):
        await service.update_credential(
            "OPENAI_BASE_URL",
            operation="set",
            value="https://user:pass@api.example.com/v1",
            expected_revision=current["revision"],
            active_run_statuses={},
        )


@pytest.mark.asyncio
async def test_dotenv_duplicate_managed_key_must_be_cleaned_before_write(
    service: SettingsService,
) -> None:
    service.dotenv_path.write_text(
        "TAVILY_API_KEY=one\nTAVILY_API_KEY=two\n", encoding="utf-8"
    )
    current = await service.public_view()

    with pytest.raises(DataValidationError, match="重复"):
        await service.update_credential(
            "TAVILY_API_KEY",
            operation="set",
            value="credential-sentinel-value",
            expected_revision=current["revision"],
            active_run_statuses={},
        )
