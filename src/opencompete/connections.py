"""主备模型连接的进程内适配，绝不向 Graph 配置传递凭据。."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from open_deep_research.utils import RuntimeModelConnection
from opencompete.settings import OpenCompeteSettings

ConnectionRole = Literal["primary", "fallback"]
ConnectionFormat = Literal["openai_chat_completions", "anthropic_messages"]

PRIMARY_CONNECTION_API_KEY = "OPENCOMPETE_PRIMARY_API_KEY"
PRIMARY_CONNECTION_BASE_URL = "OPENCOMPETE_PRIMARY_BASE_URL"
FALLBACK_CONNECTION_API_KEY = "OPENCOMPETE_FALLBACK_API_KEY"
FALLBACK_CONNECTION_BASE_URL = "OPENCOMPETE_FALLBACK_BASE_URL"

_CONNECTION_ENV_NAMES: dict[ConnectionRole, tuple[str, str]] = {
    "primary": (PRIMARY_CONNECTION_API_KEY, PRIMARY_CONNECTION_BASE_URL),
    "fallback": (FALLBACK_CONNECTION_API_KEY, FALLBACK_CONNECTION_BASE_URL),
}


def provider_for_format(connection_format: ConnectionFormat) -> str:
    """将用户选择的兼容格式映射为 LangChain provider 标识。."""
    return (
        "openai"
        if connection_format == "openai_chat_completions"
        else "anthropic"
    )


def display_model_name(model: str | None) -> str | None:
    """移除内部 provider 前缀，供设置页面编辑。."""
    if model is None:
        return None
    _provider, separator, model_name = model.partition(":")
    return model_name if separator else model


def model_identifier(
    model_name: str | None,
    connection_format: ConnectionFormat,
) -> str | None:
    """把页面的纯模型名转为 LangChain 内部模型路由。."""
    if model_name is None:
        return None
    normalized = model_name.strip()
    if not normalized:
        return None
    if ":" in normalized:
        return normalized
    return f"{provider_for_format(connection_format)}:{normalized}"


def format_for_model(
    model: str | None,
    fallback: ConnectionFormat,
) -> ConnectionFormat:
    """兼容旧 provider:model 配置，并为无前缀值返回指定默认格式。."""
    if model is not None and model.startswith("anthropic:"):
        return "anthropic_messages"
    if model is not None and model.startswith("openai:"):
        return "openai_chat_completions"
    return fallback


def connection_environment_names(role: ConnectionRole) -> tuple[str, str]:
    """返回连接角色受管的 API Key 与 Base URL 环境变量名。."""
    return _CONNECTION_ENV_NAMES[role]


def runtime_model_connections(
    settings: OpenCompeteSettings,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, RuntimeModelConnection]:
    """从当前进程环境构造主备连接，不把值写入任何持久化对象。."""
    values = environment if environment is not None else os.environ
    return {
        "primary": _connection_for_role("primary", settings, values),
        "fallback": _connection_for_role("fallback", settings, values),
    }


def connection_values_for_view(
    role: ConnectionRole,
    settings: OpenCompeteSettings,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """返回页面可见的非秘密连接信息和当前 Key 状态。."""
    values = environment if environment is not None else os.environ
    api_key_name, base_url_name = connection_environment_names(role)
    connection = _connection_for_role(role, settings, values)
    configured = bool(connection.api_key)
    return {
        "role": role,
        "format": _format_for_role(role, settings),
        "base_url": connection.base_url,
        "base_url_name": base_url_name,
        "api_key_name": api_key_name,
        "api_key_configured": configured,
    }


def api_key_for_reveal(
    role: ConnectionRole,
    settings: OpenCompeteSettings,
    *,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """仅供本机显式 reveal 返回当前有效 Key。."""
    values = environment if environment is not None else os.environ
    return _connection_for_role(role, settings, values).api_key


def _connection_for_role(
    role: ConnectionRole,
    settings: OpenCompeteSettings,
    environment: Mapping[str, str],
) -> RuntimeModelConnection:
    api_key_name, base_url_name = connection_environment_names(role)
    connection_format = _format_for_role(role, settings)
    api_key = _clean(environment.get(api_key_name))
    base_url = _clean(environment.get(base_url_name))
    if api_key is None:
        api_key = _clean(environment.get(_legacy_api_key_name(connection_format)))
    if base_url is None:
        base_url = _clean(environment.get(_legacy_base_url_name(connection_format)))
    return RuntimeModelConnection(api_key=api_key, base_url=base_url)


def _format_for_role(
    role: ConnectionRole,
    settings: OpenCompeteSettings,
) -> ConnectionFormat:
    if role == "primary":
        return format_for_model(
            settings.research_model,
            settings.primary_connection_format,
        )
    return format_for_model(settings.fallback_model, settings.fallback_connection_format)


def _legacy_api_key_name(connection_format: ConnectionFormat) -> str:
    return (
        "OPENAI_API_KEY"
        if connection_format == "openai_chat_completions"
        else "ANTHROPIC_API_KEY"
    )


def _legacy_base_url_name(connection_format: ConnectionFormat) -> str:
    return (
        "OPENAI_BASE_URL"
        if connection_format == "openai_chat_completions"
        else "ANTHROPIC_BASE_URL"
    )


def _clean(value: str | None) -> str | None:
    normalized = value.strip() if value is not None else ""
    return normalized or None


__all__ = [
    "ConnectionFormat",
    "ConnectionRole",
    "FALLBACK_CONNECTION_API_KEY",
    "FALLBACK_CONNECTION_BASE_URL",
    "PRIMARY_CONNECTION_API_KEY",
    "PRIMARY_CONNECTION_BASE_URL",
    "api_key_for_reveal",
    "connection_environment_names",
    "connection_values_for_view",
    "display_model_name",
    "format_for_model",
    "model_identifier",
    "provider_for_format",
    "runtime_model_connections",
]
