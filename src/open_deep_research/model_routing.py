"""使用 LangChain 官方 Runnable 能力实现单次模型调用重试与降级。."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import anthropic
import httpx
import openai
from langchain_core.runnables import Runnable, RunnableConfig

ModelPreparer = Callable[[Any], Runnable[Any, Any]]


def _unique_exception_types(
    values: Sequence[type[BaseException]],
) -> tuple[type[BaseException], ...]:
    """保持异常类型顺序并去重。."""
    return tuple(dict.fromkeys(values))


TRANSIENT_MODEL_EXCEPTIONS = _unique_exception_types(
    (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.InternalServerError,
        openai.RateLimitError,
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.InternalServerError,
        anthropic.RateLimitError,
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadError,
        httpx.ReadTimeout,
        TimeoutError,
        ConnectionError,
    )
)


def build_resilient_model(
    base_model: Any,
    *,
    prepare: ModelPreparer,
    primary_config: RunnableConfig,
    primary_attempts: int,
    fallback_config: RunnableConfig | None = None,
    fallback_attempts: int = 0,
    retry_delays_seconds: Sequence[float] = (2.0, 5.0, 10.0, 20.0),
) -> Runnable[Any, Any]:
    """只重试当前模型调用，并仅在临时错误后切换备用模型。."""
    if primary_attempts < 1:
        raise ValueError("主模型尝试次数必须大于零")
    if fallback_config is None and fallback_attempts:
        raise ValueError("没有备用模型时不能配置备用尝试次数")
    if fallback_config is not None and fallback_attempts < 1:
        raise ValueError("备用模型尝试次数必须大于零")
    total_attempts = primary_attempts + (
        fallback_attempts if fallback_config is not None else 0
    )
    if total_attempts > 5:
        raise ValueError("单次模型调用的真实尝试总数不能超过五次")
    if not retry_delays_seconds or any(delay < 0 for delay in retry_delays_seconds):
        raise ValueError("模型重试等待配置无效")

    retry_options = _retry_options(retry_delays_seconds)
    primary = prepare(base_model).with_config(primary_config).with_retry(
        retry_if_exception_type=TRANSIENT_MODEL_EXCEPTIONS,
        stop_after_attempt=primary_attempts,
        **retry_options,
    )
    if fallback_config is None:
        return primary

    fallback = prepare(base_model).with_config(fallback_config).with_retry(
        retry_if_exception_type=TRANSIENT_MODEL_EXCEPTIONS,
        stop_after_attempt=fallback_attempts,
        **retry_options,
    )
    return primary.with_fallbacks(
        [fallback],
        exceptions_to_handle=TRANSIENT_MODEL_EXCEPTIONS,
    )


def route_metadata(
    *,
    provider: str,
    model: str,
    fallback: bool,
    route_attempts: int,
    primary_attempts: int,
    fallback_attempts: int,
    retry_delays_seconds: Sequence[float],
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
    connection_role: str | None = None,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成供预算回调和 CLI 使用的非秘密路由元数据。."""
    total_attempts = primary_attempts + fallback_attempts
    values = dict(existing or {})
    values.update(
        {
            "opencompete_provider": provider,
            "opencompete_model": model,
            "opencompete_fallback": fallback,
            "opencompete_route_attempts": route_attempts,
            "opencompete_primary_attempts": primary_attempts,
            "opencompete_fallback_attempts": fallback_attempts,
            "opencompete_total_attempts": total_attempts,
            "opencompete_retry_initial_seconds": float(retry_delays_seconds[0]),
            "opencompete_retry_max_seconds": float(max(retry_delays_seconds)),
        }
    )
    if fallback_provider is not None:
        values["opencompete_fallback_provider"] = fallback_provider
    if fallback_model is not None:
        values["opencompete_fallback_model"] = fallback_model
    if connection_role is not None:
        values["opencompete_connection_role"] = connection_role
    return values


def retry_delay_seconds(
    route_attempt: int,
    *,
    initial: float,
    maximum: float,
) -> float:
    """返回与当前 LangChain 指数退避配置一致的确定性等待时间。."""
    return min(maximum, initial * (2 ** max(0, route_attempt - 1)))


def _retry_options(delays: Sequence[float]) -> dict[str, Any]:
    initial = float(delays[0])
    maximum = float(max(delays))
    if initial == 0:
        return {"wait_exponential_jitter": False}
    return {
        "wait_exponential_jitter": True,
        "exponential_jitter_params": {
            "initial": initial,
            "max": maximum,
            "jitter": 0.0,
        },
    }


__all__ = [
    "TRANSIENT_MODEL_EXCEPTIONS",
    "build_resilient_model",
    "retry_delay_seconds",
    "route_metadata",
]
