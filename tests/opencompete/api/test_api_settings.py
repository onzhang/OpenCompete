from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from opencompete.api.server import create_app
from opencompete.models import RunManifest, RunStatus
from opencompete.settings import OpenCompeteSettings
from opencompete.settings_service import SettingsService


def _app(tmp_path: Path, monkeypatch: Any | None = None) -> Any:
    if monkeypatch is not None:
        for name in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "GOOGLE_API_KEY",
            "TAVILY_API_KEY",
            "OPENCOMPETE_PRIMARY_API_KEY",
            "OPENCOMPETE_PRIMARY_BASE_URL",
            "OPENCOMPETE_FALLBACK_API_KEY",
            "OPENCOMPETE_FALLBACK_BASE_URL",
        ):
            monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "opencompete.toml"
    config_path.write_text("research_model = \"openai:test-model\"\n", encoding="utf-8")
    settings = OpenCompeteSettings(
        data_dir=tmp_path,
        research_model="openai:test-model",
        summarization_model="openai:test-model",
        compression_model="openai:test-model",
        final_report_model="openai:test-model",
    )
    service = SettingsService(
        config_path,
        startup_environment={},
        initial_settings=settings,
    )
    return create_app(settings, config_path=config_path, settings_service=service)


def test_settings_get_put_and_legacy_budget_summary(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        initial = client.get("/api/settings")
        assert initial.status_code == 200
        body = initial.json()
        assert body["fields"]["research_model"]["value"] == "test-model"
        assert "api_key" not in body["fields"]
        assert not {
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
        } & set(body["fields"])

        saved = client.put(
            "/api/settings",
            json={
                "expected_revision": body["revision"],
                "values": {"max_input_tokens": 123456},
            },
        )
        assert saved.status_code == 200
        assert saved.json()["fields"]["max_input_tokens"]["value"] == 123456

        legacy = client.get("/api/config")
        assert legacy.status_code == 200
        assert legacy.json()["budget_limit"]["input_tokens"] == 123456


def test_settings_update_rejects_internal_fields(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        current = client.get("/api/settings").json()
        for name, value in (
            ("judge_model", "openai:judge"),
            ("primary_disable_thinking", True),
            ("long_form_streaming", False),
            ("llm_input_cost_per_million_usd", 1.0),
            ("max_supervisor_model_turns", 11),
        ):
            response = client.put(
                "/api/settings",
                json={
                    "expected_revision": current["revision"],
                    "values": {name: value},
                },
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "data_validation_error"



def test_settings_revision_conflict_and_external_management(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        current = client.get("/api/settings").json()
        conflict = client.put(
            "/api/settings",
            json={
                "expected_revision": current["revision"] + 1,
                "values": {"max_input_tokens": 123456},
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "data_validation_error"


def test_credential_never_echoes_value_and_active_run_blocks_rotation(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    secret = "API-SETTINGS-SECRET-SENTINEL"
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        current = client.get("/api/settings").json()
        saved = client.put(
            "/api/settings/credentials",
            json={
                "expected_revision": current["revision"],
                "name": "TAVILY_API_KEY",
                "operation": "set",
                "value": secret,
            },
        )
        assert saved.status_code == 200
        assert secret not in saved.text
        state = next(
            item
            for item in saved.json()["credentials"]
            if item["name"] == "TAVILY_API_KEY"
        )
        assert state["configured"] is True

        client.app.state.repository.initialize(
            RunManifest(
                run_id="draft-run",
                status=RunStatus.DRAFT,
                scope_revision=0,
                artifact_revision=0,
            )
        )
        blocked = client.put(
            "/api/settings/credentials",
            json={
                "expected_revision": saved.json()["revision"],
                "name": "TAVILY_API_KEY",
                "operation": "clear",
            },
        )
        assert blocked.status_code == 409
        assert secret not in blocked.text
        assert "未终态任务" in blocked.json()["error"]["message"]


def test_connection_test_rejects_development_or_unknown_roles_without_provider_call(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path)) as client:
        for target in ("judge", "unknown"):
            response = client.post(f"/api/settings/connections/{target}/test")
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "data_validation_error"


def test_connection_key_reveal_is_explicit_and_not_cached(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    secret = "PRIMARY-REVEAL-SECRET-SENTINEL"
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        monkeypatch.setenv("OPENCOMPETE_PRIMARY_API_KEY", secret)
        public = client.get("/api/settings")
        assert public.status_code == 200
        assert secret not in public.text

        revealed = client.get("/api/settings/connections/primary/api-key")
        assert revealed.status_code == 200
        assert revealed.headers["cache-control"] == "no-store"
        assert revealed.json() == {
            "role": "primary",
            "configured": True,
            "api_key": secret,
        }

        hidden_again = client.get("/api/settings")
        assert secret not in hidden_again.text


def test_primary_and_fallback_connection_tests_use_role_credentials(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    captured: list[dict[str, Any]] = []

    class FakeRunnable:
        async def ainvoke(self, message: Any) -> object:
            captured.append({"message": message})
            return object()

    def fake_init_chat_model(**kwargs: Any) -> FakeRunnable:
        captured.append(dict(kwargs))
        return FakeRunnable()

    monkeypatch.setattr("opencompete.api.routes.init_chat_model", fake_init_chat_model)
    monkeypatch.setenv("OPENCOMPETE_PRIMARY_API_KEY", "PRIMARY-CONNECTION-SECRET")
    monkeypatch.setenv("OPENCOMPETE_PRIMARY_BASE_URL", "https://primary.example/v1")
    monkeypatch.setenv("OPENCOMPETE_FALLBACK_API_KEY", "FALLBACK-CONNECTION-SECRET")
    monkeypatch.setenv("OPENCOMPETE_FALLBACK_BASE_URL", "https://fallback.example/v1")
    with TestClient(_app(tmp_path)) as client:
        current = client.get("/api/settings").json()
        updated = client.put(
            "/api/settings",
            json={
                "expected_revision": current["revision"],
                "values": {
                    "fallback_model": "backup-model",
                    "fallback_connection_format": "openai_chat_completions",
                },
            },
        )
        assert updated.status_code == 200

        assert client.post("/api/settings/connections/primary/test").json() == {
            "target": "primary",
            "status": "ok",
            "provider": "openai",
            "model": "test-model",
        }
        assert client.post("/api/settings/connections/fallback/test").json() == {
            "target": "fallback",
            "status": "ok",
            "provider": "openai",
            "model": "backup-model",
        }

    model_calls = [item for item in captured if "model" in item]
    assert [item["model"] for item in model_calls] == [
        "openai:test-model",
        "openai:backup-model",
    ]
    assert [item["api_key"] for item in model_calls] == [
        "PRIMARY-CONNECTION-SECRET",
        "FALLBACK-CONNECTION-SECRET",
    ]
    assert [item["base_url"] for item in model_calls] == [
        "https://primary.example/v1",
        "https://fallback.example/v1",
    ]
    assert all(item["max_retries"] == 0 for item in model_calls)
