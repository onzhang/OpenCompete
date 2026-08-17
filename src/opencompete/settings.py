"""非秘密配置的 Settings Source 组合。."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from opencompete.errors import ConfigurationError

_MODEL_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*:[^\s:][^\s]*$")
MODEL_SETTINGS_FIELDS = (
    "research_model",
    "summarization_model",
    "compression_model",
    "final_report_model",
    "fallback_model",
    "judge_model",
)
MANAGEABLE_MODEL_PROVIDERS = frozenset({"openai", "anthropic", "google_genai"})

# 设置工作台只管理普通用户能够理解且会直接影响后续研究范围、质量、速度或
# 硬预算的非秘密参数。Judge、费率、传输与编排保护阈值仍可由 TOML 供开发与
# 评测使用，但不能经用户 API 暴露或写入。
MANAGEABLE_SETTINGS_FIELDS = frozenset(
    {
        "research_model",
        "summarization_model",
        "compression_model",
        "final_report_model",
        "fallback_model",
        "research_model_max_tokens",
        "summarization_model_max_tokens",
        "compression_model_max_tokens",
        "final_report_model_max_tokens",
        "tavily_max_results_per_query",
        "discovery_search_results_per_query",
        "max_concurrent_research_units",
        "max_researcher_iterations",
        "max_research_tool_calls",
        "max_tavily_calls",
        "max_input_tokens",
        "max_output_tokens",
        "max_cost_usd",
    }
)

SETTINGS_GROUPS: dict[str, tuple[str, ...]] = {
    "模型与连接": (
        "research_model",
        "summarization_model",
        "compression_model",
        "final_report_model",
        "fallback_model",
        "research_model_max_tokens",
        "summarization_model_max_tokens",
        "compression_model_max_tokens",
        "final_report_model_max_tokens",
    ),
    "研究预算": (
        "max_input_tokens",
        "max_output_tokens",
        "max_tavily_calls",
        "max_cost_usd",
    ),
    "研究深度与速度": (
        "max_concurrent_research_units",
        "max_researcher_iterations",
        "max_research_tool_calls",
    ),
    "搜索范围": (
        "tavily_max_results_per_query",
        "discovery_search_results_per_query",
    ),
}


def thinking_disable_options(model: str | None) -> dict[str, Any]:
    """按模型路由返回兼容的 Thinking 关闭参数。."""
    if model is None:
        return {}
    provider, _, model_name = model.partition(":")
    # DeepSeek 经 Anthropic 兼容端点时必须关闭 Thinking，才能稳定使用结构化
    # 输出与强制工具调用。OpenAI、Google 或 Claude 路由不接收这个参数。
    if provider == "anthropic" and "deepseek" in model_name.casefold():
        return {"thinking": {"type": "disabled"}}
    return {}


class OpenCompeteSettings(BaseSettings):
    """可持久化和传递的非秘密运行配置。."""

    model_config = SettingsConfigDict(
        env_prefix="OPENCOMPETE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        toml_file="opencompete.toml",
    )

    data_dir: Path = Path(".opencompete-data")
    research_model: str = "openai:gpt-4.1"
    summarization_model: str = "openai:gpt-4.1-mini"
    compression_model: str = "openai:gpt-4.1"
    final_report_model: str = "openai:gpt-4.1"
    fallback_model: str | None = Field(default=None, min_length=1)
    primary_provider_name: str = Field(default="OpenAI", min_length=1)
    fallback_provider_name: str = Field(default="DeepSeek", min_length=1)
    primary_model_attempts: int = Field(default=3, ge=1, le=5)
    fallback_model_attempts: int = Field(default=2, ge=1, le=5)
    model_retry_delays_seconds: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0)
    judge_model: str | None = Field(default=None, min_length=1)
    research_model_max_tokens: int = Field(default=10000, ge=1)
    summarization_model_max_tokens: int = Field(default=8192, ge=1)
    compression_model_max_tokens: int = Field(default=8192, ge=1)
    final_report_model_max_tokens: int = Field(default=6000, ge=1)
    artifact_extraction_model_max_tokens: int = Field(default=10000, ge=1)
    final_report_input_max_chars: int = Field(default=200000, ge=1000)
    judge_model_max_tokens: int = Field(default=8192, ge=1)
    llm_request_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    llm_sdk_max_retries: int = Field(default=0, ge=0, le=10)
    judge_prompt_version: Literal["opencompete-race-fact-v1"] = (
        "opencompete-race-fact-v1"
    )
    max_content_length: int = Field(default=50000, ge=1000)
    tavily_max_results_per_query: int = Field(default=5, ge=1, le=20)
    discovery_search_results_per_query: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "候选发现单次 Tavily Search 的相关来源页大小；不限制候选产品数、"
            "检索词数或累计来源数，后续来源由覆盖审计按需继续检索。"
        ),
    )
    discovery_binding_max_input_chars: int = Field(
        default=12000,
        ge=1000,
        le=200000,
        description=(
            "候选发现单次来源绑定模型调用的正文字符预算；超出时按来源内容分批，"
            "不截断候选池或搜索来源。"
        ),
    )
    tavily_max_queries_per_call: int = Field(
        default=3,
        ge=1,
        le=20,
        description="单次 Tavily Search 工具调用在发送前准入的 query 硬上限。",
    )
    tavily_extract_excerpt_max_chars: int = Field(
        default=1200,
        ge=200,
        le=4000,
        description="每个 URL 返回的 Tavily Extract 短摘录字符上限。",
    )
    max_structured_output_retries: int = Field(default=3, ge=1, le=10)
    max_concurrent_research_units: int = Field(default=3, ge=1, le=20)
    max_concurrent_summarizations: int = Field(default=3, ge=1, le=20)
    max_researcher_iterations: int = Field(default=4, ge=1, le=20)
    max_supervisor_model_turns: int = Field(default=9, ge=1, le=50)
    max_react_tool_calls: int = Field(
        default=6,
        ge=1,
        le=50,
        description="单个 Researcher 的模型工具循环硬上限。",
    )
    max_research_tool_calls: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "单个 Researcher 的研究工具额度；本地工具执行前截断，"
            "供应商原生搜索按响应元数据事后计数。"
        ),
    )
    max_tavily_calls: int = Field(
        default=30,
        ge=0,
        description=(
            "Tavily Search query 与 Extract credit 共用的兼容预算字段。"
        ),
    )
    max_input_tokens: int = Field(default=500000, ge=0)
    max_output_tokens: int = Field(default=100000, ge=0)
    max_cost_usd: float = Field(default=20.0, ge=0)
    llm_input_cost_per_million_usd: float = Field(
        default=2.0,
        ge=0,
        description=(
            "当前默认模型的保守统一本地估算输入费率；不代表供应商账单。"
        ),
    )
    llm_output_cost_per_million_usd: float = Field(
        default=8.0,
        ge=0,
        description=(
            "当前默认模型的保守统一本地估算输出费率；不代表供应商账单。"
        ),
    )
    tavily_cost_per_call_usd: float = Field(
        default=0.02,
        ge=0,
        description="每个 Tavily credit 的保守本地估算费率；不代表供应商账单。",
    )
    price_config_version: str = Field(
        default="local-estimate-2026-07-18-tavily-credit-v2",
        min_length=1,
        description="本地估算价格与 Tavily credit 口径版本；不代表供应商账单版本。",
    )
    langsmith_tracing: bool = False

    @field_validator(*MODEL_SETTINGS_FIELDS)
    @classmethod
    def validate_model_identifier(cls, value: str | None) -> str | None:
        """模型角色必须使用 LangChain 支持的 ``provider:model`` 标识。."""
        if value is None:
            return None
        normalized = value.strip()
        if not _MODEL_IDENTIFIER_PATTERN.fullmatch(normalized):
            raise ValueError("模型必须使用 provider:model 格式，且不能包含空白字符")
        return normalized

    @model_validator(mode="after")
    def validate_model_failover(self) -> OpenCompeteSettings:
        """限制主备真实尝试总数，并保证退避配置完整。."""
        total = self.primary_model_attempts
        if self.fallback_model is not None:
            total += self.fallback_model_attempts
        if total > 5:
            raise ValueError("主备模型真实尝试总数不能超过五次")
        if len(self.model_retry_delays_seconds) < total - 1:
            raise ValueError("模型重试等待配置不足")
        if any(delay < 0 for delay in self.model_retry_delays_seconds):
            raise ValueError("模型重试等待时间不能为负数")
        if self.max_react_tool_calls < self.max_research_tool_calls * 2:
            raise ValueError(
                "Researcher 模型轮次硬上限必须覆盖配置的搜索与反思交替"
            )
        if self.max_supervisor_model_turns < self.max_researcher_iterations * 2 + 1:
            raise ValueError(
                "Supervisor 模型轮次硬上限必须覆盖委托、反思与显式完成"
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """设置 CLI、环境、`.env`、TOML、默认值的明确优先级。."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    def to_runnable_config(
        self,
        run_id: str,
        *,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        """生成不包含 API Key 的上游 RunnableConfig。."""
        if not run_id.strip():
            raise ConfigurationError("运行 Graph 必须提供非空 run_id")
        research_model = model_override or self.research_model
        summarization_model = model_override or self.summarization_model
        compression_model = model_override or self.compression_model
        final_report_model = model_override or self.final_report_model
        model_thinking_by_model = {
            model: options["thinking"]
            for model in (
                research_model,
                summarization_model,
                compression_model,
                final_report_model,
            )
            if (options := thinking_disable_options(model))
        }
        configurable: dict[str, Any] = {
            "allow_clarification": False,
            "search_api": "tavily",
            "mcp_config": None,
            "mcp_prompt": None,
            "research_model": research_model,
            "research_model_max_tokens": self.research_model_max_tokens,
            "summarization_model": summarization_model,
            "summarization_model_max_tokens": self.summarization_model_max_tokens,
            "compression_model": compression_model,
            "compression_model_max_tokens": self.compression_model_max_tokens,
            "final_report_model": final_report_model,
            "final_report_model_max_tokens": self.final_report_model_max_tokens,
            "long_form_streaming": True,
            "final_report_input_max_chars": self.final_report_input_max_chars,
            "llm_request_timeout_seconds": self.llm_request_timeout_seconds,
            "llm_sdk_max_retries": self.llm_sdk_max_retries,
            "fallback_model": self.fallback_model,
            "primary_provider_name": self.primary_provider_name,
            "fallback_provider_name": self.fallback_provider_name,
            "primary_model_attempts": self.primary_model_attempts,
            "fallback_model_attempts": self.fallback_model_attempts,
            "model_retry_delays_seconds": self.model_retry_delays_seconds,
            "fallback_disable_thinking": bool(
                thinking_disable_options(self.fallback_model)
            ),
            "max_content_length": self.max_content_length,
            "tavily_max_results_per_query": self.tavily_max_results_per_query,
            "tavily_max_queries_per_call": self.tavily_max_queries_per_call,
            "tavily_extract_excerpt_max_chars": (
                self.tavily_extract_excerpt_max_chars
            ),
            "max_structured_output_retries": self.max_structured_output_retries,
            "max_concurrent_research_units": self.max_concurrent_research_units,
            "max_concurrent_summarizations": self.max_concurrent_summarizations,
            "max_researcher_iterations": self.max_researcher_iterations,
            "max_supervisor_model_turns": self.max_supervisor_model_turns,
            "max_react_tool_calls": self.max_react_tool_calls,
            "max_research_tool_calls": self.max_research_tool_calls,
        }
        if model_thinking_by_model:
            configurable["model_thinking_by_model"] = model_thinking_by_model
        configurable["thread_id"] = run_id
        return {
            "configurable": configurable,
            "metadata": {
                "tavily_max_queries_per_call": self.tavily_max_queries_per_call,
            },
        }


def load_settings(
    config_path: str | Path | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> OpenCompeteSettings:
    """按 CLI、环境、TOML、默认值加载配置，密钥仅留在进程环境。."""
    path = Path(config_path) if config_path is not None else Path("opencompete.toml")
    if config_path is not None and not path.is_file():
        raise ConfigurationError(f"配置文件不存在：{path}")

    dotenv_path = path.parent / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)
    overrides = dict(cli_overrides or {})
    if "fallback_model" not in overrides:
        fallback_model = os.getenv("OPENCOMPETE_FALLBACK_MODEL") or os.getenv(
            "ANTHROPIC_MODEL"
        )
        if fallback_model:
            overrides["fallback_model"] = (
                fallback_model
                if ":" in fallback_model
                else f"anthropic:{fallback_model}"
            )
    allowed_fields = set(OpenCompeteSettings.model_fields)
    unknown_fields = set(overrides) - allowed_fields
    sensitive_fields = {
        key
        for key in overrides
        if any(
            marker in key.casefold().replace("-", "_")
            for marker in ("api_key", "apikey", "authorization", "secret", "token")
        )
    }
    if unknown_fields or sensitive_fields:
        fields = ", ".join(sorted(unknown_fields | sensitive_fields))
        raise ConfigurationError(f"CLI 包含不允许的配置字段：{fields}")

    runtime_config = dict(OpenCompeteSettings.model_config)
    runtime_config["toml_file"] = path
    runtime_config["env_file"] = dotenv_path
    runtime_settings = type(
        "RuntimeOpenCompeteSettings",
        (OpenCompeteSettings,),
        {"model_config": runtime_config},
    )
    try:
        return runtime_settings(**overrides)
    except ValidationError as exc:
        raise ConfigurationError(f"配置校验失败：{exc}") from exc
