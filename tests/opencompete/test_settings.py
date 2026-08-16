from pathlib import Path

import pytest

from opencompete.errors import ConfigurationError
from opencompete.settings import OpenCompeteSettings, load_settings


def test_settings_precedence_is_cli_then_environment_then_toml_then_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "opencompete.toml"
    config_path.write_text(
        'research_model = "toml:model"\n'
        'compression_model = "toml:compression"\n'
        "max_concurrent_research_units = 2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCOMPETE_RESEARCH_MODEL", "env:model")
    monkeypatch.setenv("OPENCOMPETE_MAX_CONCURRENT_RESEARCH_UNITS", "4")

    settings = load_settings(
        config_path,
        {"research_model": "cli:model"},
    )

    assert settings.research_model == "cli:model"
    assert settings.max_concurrent_research_units == 4
    assert settings.compression_model == "toml:compression"
    assert settings.langsmith_tracing is False


def test_secrets_are_not_settings_fields_or_runnable_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "secret-sentinel-value"
    monkeypatch.setenv("TAVILY_API_KEY", secret)
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    settings = load_settings(config_path=None)
    serialized = settings.model_dump_json()
    runnable = str(settings.to_runnable_config(run_id="run-a"))

    assert secret not in serialized
    assert secret not in runnable
    assert "api_key" not in serialized.lower()
    assert "authorization" not in runnable.lower()
    explicit_config = settings.to_runnable_config(run_id="run-a")
    assert explicit_config["configurable"]["search_api"] == "tavily"
    assert explicit_config["configurable"]["mcp_config"] is None


def test_cli_cannot_supply_secret_or_unknown_fields() -> None:
    with pytest.raises(ConfigurationError):
        load_settings(None, {"tavily_api_key": "must-not-enter-settings"})


def test_settings_reject_invalid_limits() -> None:
    with pytest.raises(ValueError):
        OpenCompeteSettings(max_concurrent_research_units=0)


def test_agent_control_limits_enter_runnable_config() -> None:
    settings = OpenCompeteSettings(
        max_react_tool_calls=8,
        max_research_tool_calls=4,
        max_researcher_iterations=3,
        max_supervisor_model_turns=7,
    )

    configurable = settings.to_runnable_config("run-agent-control")["configurable"]

    assert configurable["max_react_tool_calls"] == 8
    assert configurable["max_research_tool_calls"] == 4
    assert configurable["max_researcher_iterations"] == 3
    assert configurable["max_supervisor_model_turns"] == 7


def test_model_turn_limit_must_allow_search_reflection_pairs() -> None:
    with pytest.raises(ValueError, match="搜索与反思"):
        OpenCompeteSettings(
            max_react_tool_calls=5,
            max_research_tool_calls=3,
        )


def test_supervisor_model_turn_limit_must_allow_delegation_reflection_and_close() -> None:
    with pytest.raises(ValueError, match="Supervisor"):
        OpenCompeteSettings(
            max_researcher_iterations=3,
            max_supervisor_model_turns=6,
        )


def test_model_transport_limits_are_explicit_and_enter_runnable_config() -> None:
    settings = OpenCompeteSettings(
        llm_request_timeout_seconds=240,
        llm_sdk_max_retries=0,
        tavily_max_results_per_query=2,
        discovery_search_results_per_query=8,
        final_report_input_max_chars=24000,
        artifact_extraction_model_max_tokens=9000,
        long_form_streaming=True,
        max_concurrent_summarizations=2,
    )

    configurable = settings.to_runnable_config(run_id="run-transport")[
        "configurable"
    ]

    assert configurable["llm_request_timeout_seconds"] == 240
    assert configurable["llm_sdk_max_retries"] == 0
    assert configurable["tavily_max_results_per_query"] == 2
    # 候选发现页大小由 ApplicationRunner 单独注入 Scope Graph，不能改写上游
    # Researcher 的 RunnableConfig。
    assert settings.discovery_search_results_per_query == 8
    assert configurable["final_report_input_max_chars"] == 24000
    assert configurable["long_form_streaming"] is True
    assert settings.artifact_extraction_model_max_tokens == 9000
    assert configurable["max_concurrent_summarizations"] == 2


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("llm_request_timeout_seconds", 0),
        ("llm_sdk_max_retries", -1),
        ("tavily_max_results_per_query", 0),
        ("discovery_search_results_per_query", 0),
        ("final_report_input_max_chars", 999),
        ("artifact_extraction_model_max_tokens", 0),
        ("max_concurrent_summarizations", 0),
    ),
)
def test_settings_reject_invalid_model_transport_limits(
    field_name: str,
    value: int,
) -> None:
    with pytest.raises(ValueError):
        OpenCompeteSettings(**{field_name: value})


def test_settings_expose_versioned_local_cost_estimates(tmp_path: Path) -> None:
    # 仓库根目录的 opencompete.toml 会覆盖默认费率；用空 tempdir TOML 验证默认值。
    config_path = tmp_path / "opencompete.toml"
    config_path.write_text("", encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.llm_input_cost_per_million_usd == 2.0
    assert settings.llm_output_cost_per_million_usd == 8.0
    assert settings.tavily_cost_per_call_usd == 0.02
    assert settings.price_config_version == (
        "local-estimate-2026-07-18-tavily-credit-v2"
    )

    for field_name in (
        "llm_input_cost_per_million_usd",
        "llm_output_cost_per_million_usd",
        "tavily_cost_per_call_usd",
    ):
        description = OpenCompeteSettings.model_fields[field_name].description or ""
        assert "本地估算" in description
        assert "供应商账单" in description


def test_report_judge_is_disabled_by_default_and_explicitly_configurable(
    tmp_path: Path,
) -> None:
    # 仓库根目录的 opencompete.toml 配置了 judge_model，测试用 tempdir 隔离默认值。
    isolated = OpenCompeteSettings(
        data_dir=tmp_path,
        judge_model=None,
        judge_model_max_tokens=8192,
        judge_prompt_version="opencompete-race-fact-v1",
    )

    assert isolated.judge_model is None
    assert isolated.judge_model_max_tokens == 8192
    assert isolated.judge_prompt_version == "opencompete-race-fact-v1"

    config_path = tmp_path / "opencompete.toml"
    config_path.write_text(
        'judge_model = "openai:test-judge"\n'
        "judge_model_max_tokens = 4096\n"
        'judge_prompt_version = "opencompete-race-fact-v1"\n',
        encoding="utf-8",
    )

    configured = load_settings(config_path)

    assert configured.judge_model == "openai:test-judge"
    assert configured.judge_model_max_tokens == 4096
    assert configured.judge_prompt_version == "opencompete-race-fact-v1"


def test_settings_reject_unknown_judge_prompt_version() -> None:
    with pytest.raises(ValueError):
        OpenCompeteSettings(judge_prompt_version="unversioned-prompt")


@pytest.mark.parametrize(
    "field_name",
    (
        "llm_input_cost_per_million_usd",
        "llm_output_cost_per_million_usd",
        "tavily_cost_per_call_usd",
    ),
)
def test_settings_reject_negative_cost_rates(field_name: str) -> None:
    with pytest.raises(ValueError):
        OpenCompeteSettings(**{field_name: -0.01})


def test_runnable_config_requires_an_explicit_run_id() -> None:
    with pytest.raises(ConfigurationError):
        OpenCompeteSettings().to_runnable_config(run_id="")


def test_anthropic_model_env_becomes_non_secret_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENCOMPETE_FALLBACK_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "deepseek-fallback")

    settings = load_settings(config_path=None)

    assert settings.fallback_model == "anthropic:deepseek-fallback"
    assert "ANTHROPIC_API_KEY" not in settings.model_dump_json()
    assert "ANTHROPIC_BASE_URL" not in settings.model_dump_json()


def test_anthropic_model_from_dotenv_becomes_fallback_before_settings_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENCOMPETE_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    config_path = tmp_path / "opencompete.toml"
    config_path.write_text('research_model = "openai:primary"\n', encoding="utf-8")
    (tmp_path / ".env").write_text(
        "ANTHROPIC_MODEL=deepseek-from-dotenv\n",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.fallback_model == "anthropic:deepseek-from-dotenv"


def test_fallback_runnable_config_switches_all_model_roles_and_thinking() -> None:
    settings = OpenCompeteSettings(fallback_model="anthropic:deepseek-fallback")

    configurable = settings.to_runnable_config(
        "run-fallback",
        model_override=settings.fallback_model,
        disable_thinking=True,
    )["configurable"]

    assert configurable["research_model"] == "anthropic:deepseek-fallback"
    assert configurable["summarization_model"] == "anthropic:deepseek-fallback"
    assert configurable["compression_model"] == "anthropic:deepseek-fallback"
    assert configurable["final_report_model"] == "anthropic:deepseek-fallback"
    assert configurable["model_thinking"] == {"type": "disabled"}
    assert configurable["max_structured_output_retries"] == (
        settings.max_structured_output_retries
    )


def test_primary_disable_thinking_defaults_off_and_toml_can_enable(
    tmp_path: Path,
) -> None:
    # 仓库根目录的 opencompete.toml 已启用该开关；用空 tempdir TOML 验证默认值。
    empty_config = tmp_path / "opencompete.toml"
    empty_config.write_text("", encoding="utf-8")
    assert load_settings(empty_config).primary_disable_thinking is False

    enabled = OpenCompeteSettings(primary_disable_thinking=True)

    configurable = enabled.to_runnable_config(
        "run-primary-thinking",
        disable_thinking=enabled.primary_disable_thinking,
    )["configurable"]

    assert configurable["model_thinking"] == {"type": "disabled"}
    assert configurable["research_model"] == enabled.research_model


def test_toml_primary_disable_thinking_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENCOMPETE_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    config_path = tmp_path / "opencompete.toml"
    config_path.write_text(
        'research_model = "anthropic:deepseek-v4-flash"\n'
        "primary_disable_thinking = true\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("", encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.primary_disable_thinking is True
    assert settings.fallback_model is None


def test_model_attempt_total_cannot_exceed_five() -> None:
    with pytest.raises(ValueError, match="不能超过五次"):
        OpenCompeteSettings(
            fallback_model="anthropic:fallback",
            primary_model_attempts=4,
            fallback_model_attempts=2,
        )
