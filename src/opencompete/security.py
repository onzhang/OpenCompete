"""不可信 URL 边界和诊断文本脱敏。."""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from opencompete.errors import OpenCompeteError
from opencompete.models import ErrorCategory, ErrorRecord

_AUTHORIZATION_PATTERN = re.compile(
    r"(?im)(authorization\s*[:=]\s*)[^\r\n]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_API_KEY_PATTERN = re.compile(
    r"(?i)((?:api[-_ ]?key|access[-_ ]?token|secret)\s*[:=]\s*)[^\s,;]+"
)
_FORBIDDEN_HOST_SUFFIXES = (
    ".internal",
    ".invalid",
    ".lan",
    ".local",
    ".localhost",
    ".test",
)
_SECRET_ENV_NAME_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "secret",
    "token",
    "base_url",
)


class _SecretValues(tuple[str, ...]):
    """允许迭代脱敏值，但禁止异常堆栈展开其内容。."""

    def __repr__(self) -> str:
        return "<redacted>"
_NON_SECRET_CONTROL_VALUES = {
    "0",
    "1",
    "false",
    "no",
    "none",
    "null",
    "true",
    "yes",
}


def environment_secret_values() -> tuple[str, ...]:
    """返回环境中的真实秘密值，排除密钥开关等普通控制值."""
    values: list[str] = []
    for key, raw_value in os.environ.items():
        value = raw_value.strip()
        if not value or len(value) < 4:
            continue
        if not any(marker in key.casefold() for marker in _SECRET_ENV_NAME_MARKERS):
            continue
        if value.casefold() in _NON_SECRET_CONTROL_VALUES:
            continue
        values.append(value)
    return tuple(dict.fromkeys(values))


def validate_public_http_url(url: str) -> str:
    """校验并规范化不含凭据的公网 HTTP/HTTPS URL。."""
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL 格式无效") from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError("只允许 HTTP 或 HTTPS URL")
    if not parsed.hostname:
        raise ValueError("URL 必须包含主机名")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL 不能包含用户名或密码")

    raw_host = parsed.hostname.rstrip(".").casefold()
    host = _validate_public_host(raw_host)
    if ":" in host:
        serialized_host = f"[{host}]"
    else:
        serialized_host = host

    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = serialized_host if port is None or default_port else f"{serialized_host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))


def _validate_public_host(host: str) -> str:
    if host == "localhost" or host.endswith(_FORBIDDEN_HOST_SUFFIXES):
        raise ValueError("不允许本机或内部主机名")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return _validate_hostname(host)
    if not address.is_global:
        raise ValueError("不允许非公网 IP 地址")
    return address.compressed


def _validate_hostname(host: str) -> str:
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("主机名无效") from exc
    if len(ascii_host) > 253 or "." not in ascii_host:
        raise ValueError("必须使用可公开解析的完整主机名")
    labels = ascii_host.split(".")
    if all(label.isdigit() for label in labels):
        raise ValueError("不接受非标准数字 IP 表示")
    label_pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    if any(not label_pattern.fullmatch(label) for label in labels):
        raise ValueError("主机名无效")
    return ascii_host


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """删除显式秘密、Bearer Token 和常见认证头。."""
    redacted = text
    for secret in sorted(
        {value for value in secrets if value and len(value) >= 4},
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _BEARER_PATTERN.sub("[REDACTED]", redacted)
    return _API_KEY_PATTERN.sub(r"\1[REDACTED]", redacted)


def sanitized_error(
    exc: BaseException,
    secrets: Iterable[str] = (),
) -> ErrorRecord:
    """把异常转换为不会泄漏密钥或堆栈的错误记录。."""
    environment_secrets = _SecretValues(
        value
        for key, value in os.environ.items()
        if value
        and any(marker in key.casefold() for marker in _SECRET_ENV_NAME_MARKERS)
    )
    all_secrets = _SecretValues((*tuple(secrets), *environment_secrets))
    message = redact_text(str(exc), all_secrets)[:1000]
    if not message:
        message = "发生未提供详情的错误"
    if isinstance(exc, OpenCompeteError):
        return exc.to_record(message)
    provider_record = _provider_error_record(
        exc,
        all_secrets,
    )
    if provider_record is not None:
        return provider_record
    return ErrorRecord(
        code="unexpected_error",
        category=ErrorCategory.INTERNAL,
        message=message,
        retryable=False,
    )


def _provider_error_record(
    exc: BaseException,
    secrets: tuple[str, ...],
) -> ErrorRecord | None:
    """只用异常类型和状态码生成不含响应详情的 Provider 分类。."""
    provider_exc = _provider_exception(exc)
    status_code = getattr(provider_exc, "status_code", None)
    name = type(provider_exc).__name__
    detail = _safe_provider_detail(provider_exc, secrets)
    http_status = (
        status_code
        if isinstance(status_code, int) and 100 <= status_code <= 599
        else None
    )
    provider_error_code = _provider_error_code(provider_exc)
    request_id = _provider_request_id(provider_exc)
    cause_type, transport_reason = _safe_transport_cause(provider_exc)
    if status_code in {401, 403}:
        return ErrorRecord(
            code="authentication_error",
            category=ErrorCategory.AUTHENTICATION,
            message="模型 Provider 认证或权限失败",
            detail=detail,
            exception_type=name,
            cause_type=cause_type,
            transport_reason=transport_reason,
            http_status=http_status,
            provider_error_code=provider_error_code,
            request_id=request_id,
            retryable=False,
        )
    transient_names = {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "InternalServerError",
        "RateLimitError",
        "ReadError",
        "ReadTimeout",
    }
    normalized_detail = detail.casefold() if detail is not None else ""
    transient_text = any(
        marker in normalized_detail
        for marker in (
            "stream disconnected",
            "upstream request failed",
            "connection reset",
            "connection closed",
            "temporarily unavailable",
            "timed out",
            "timeout",
        )
    )
    if status_code == 429 or (
        isinstance(status_code, int) and status_code >= 500
    ) or name in transient_names or transient_text:
        return ErrorRecord(
            code="temporary_provider_error",
            category=ErrorCategory.PROVIDER,
            message="模型 Provider 暂时不可用",
            detail=detail,
            exception_type=name,
            cause_type=cause_type,
            transport_reason=transport_reason,
            http_status=http_status,
            provider_error_code=provider_error_code,
            request_id=request_id,
            retryable=True,
        )
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return ErrorRecord(
            code="provider_error",
            category=ErrorCategory.PROVIDER,
            message="模型 Provider 拒绝了请求",
            detail=detail,
            exception_type=name,
            cause_type=cause_type,
            transport_reason=transport_reason,
            http_status=http_status,
            provider_error_code=provider_error_code,
            request_id=request_id,
            retryable=False,
        )
    return None


def _safe_transport_cause(exc: BaseException) -> tuple[str | None, str | None]:
    """只从异常链提取类型和枚举化传输原因，不保存 cause 原文。."""
    current = exc.__cause__ or exc.__context__
    visited = {id(exc)}
    for _ in range(7):
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        name = type(current).__name__[:200]
        normalized = str(current).casefold()
        if (
            "incomplete chunked read" in normalized
            or "incomplete message body" in normalized
        ):
            return name, "peer_closed_incomplete_body"
        if "stream disconnected" in normalized:
            return name, "stream_disconnected"
        if name in {"ReadTimeout", "APITimeoutError", "TimeoutError"}:
            return name, "read_timeout"
        if name in {"ConnectError", "ConnectTimeout"}:
            return name, "connect_failed"
        current = current.__cause__ or current.__context__
    return None, None


def _provider_exception(exc: BaseException) -> BaseException:
    """在有限异常链中定位带状态码或已知传输类型的 Provider 异常。."""
    current: BaseException | None = exc
    visited: set[int] = set()
    transient_names = {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "InternalServerError",
        "RateLimitError",
        "ReadError",
        "ReadTimeout",
    }
    for _ in range(8):
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        if getattr(current, "status_code", None) is not None:
            return current
        if type(current).__name__ in transient_names:
            return current
        current = current.__cause__ or current.__context__
    return exc


def _safe_provider_detail(
    exc: BaseException,
    secrets: tuple[str, ...],
) -> str | None:
    """提取单条 Provider 摘要，而不是保存完整响应正文。."""
    candidate: object | None = None
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error", body)
        if isinstance(error, Mapping):
            for key in ("message", "detail", "title"):
                value = error.get(key)
                if isinstance(value, str) and value.strip():
                    candidate = value
                    break
    if not isinstance(candidate, str) or not candidate.strip():
        direct = getattr(exc, "message", None)
        candidate = direct if isinstance(direct, str) else str(exc)
    detail = redact_text(str(candidate), secrets)
    detail = re.sub(r"https?://[^\s\])}>,]+", "[URL]", detail)
    detail = " ".join(detail.split())[:500].strip()
    return detail or None


def _provider_error_code(exc: BaseException) -> str | None:
    """只提取短错误码，不保存 Provider body。."""
    value = getattr(exc, "code", None)
    if value is None:
        body = getattr(exc, "body", None)
        if isinstance(body, Mapping):
            error = body.get("error", body)
            if isinstance(error, Mapping):
                for key in ("code", "error_code", "error_name", "type"):
                    candidate = error.get(key)
                    if candidate is not None:
                        value = candidate
                        break
    text = str(value).strip() if value is not None else ""
    return text[:200] or None


def _provider_request_id(exc: BaseException) -> str | None:
    """提取可供供应商支持定位的请求 ID。."""
    for name in ("request_id", "_request_id"):
        value = getattr(exc, name, None)
        text = str(value).strip() if value is not None else ""
        if text:
            return text[:300]
    return None
