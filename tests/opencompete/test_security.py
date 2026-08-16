import pytest

from opencompete.errors import AuthenticationError
from opencompete.security import (
    environment_secret_values,
    redact_text,
    sanitized_error,
    validate_public_http_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.0.2.1/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "https://user:password@example.com/",
    ],
)
def test_private_or_non_http_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        validate_public_http_url(url)


def test_public_https_url_is_normalized() -> None:
    assert (
        validate_public_http_url("HTTPS://Example.COM:443/reports?q=1#private")
        == "https://example.com/reports?q=1"
    )


def test_text_and_error_records_are_redacted() -> None:
    secret = "secret-sentinel-value"
    text = (
        f"API_KEY={secret}\n"
        f"Authorization: Bearer {secret}\n"
        f"request failed with Bearer {secret}"
    )

    redacted = redact_text(text, (secret,))
    record = sanitized_error(
        AuthenticationError(f"认证失败，Authorization: Bearer {secret}"),
        (secret,),
    )

    assert secret not in redacted
    assert secret not in record.message
    assert "Bearer" not in record.message
    assert record.category == "authentication"
    assert record.retryable is False


def test_environment_secret_values_excludes_boolean_key_switches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "live-secret-sentinel-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("GET_API_KEYS_FROM_CONFIG", "false")
    monkeypatch.setenv("FEATURE_TOKEN_ENABLED", "true")

    values = environment_secret_values()

    assert secret in values
    assert "false" not in values
    assert "true" not in values


def test_provider_5xx_is_classified_without_persisting_response_details() -> None:
    class InternalServerError(Exception):
        status_code = 500

    record = sanitized_error(
        InternalServerError(
            "provider response from https://private-provider.example/v1: secret body"
        )
    )

    assert record.code == "temporary_provider_error"
    assert record.category == "provider"
    assert record.retryable is True
    assert "private-provider" not in record.message
    assert "secret body" not in record.message


def test_incomplete_chunked_cause_is_reduced_to_safe_transport_reason() -> None:
    APIConnectionError = type("APIConnectionError", (Exception,), {})
    RemoteProtocolError = type("RemoteProtocolError", (Exception,), {})
    cause = RemoteProtocolError(
        "peer closed connection without sending complete message body "
        "(incomplete chunked read): Authorization: Bearer secret"
    )
    error = APIConnectionError("Connection error.")
    error.__cause__ = cause

    record = sanitized_error(error)

    assert record.code == "temporary_provider_error"
    assert record.exception_type == "APIConnectionError"
    assert record.cause_type == "RemoteProtocolError"
    assert record.transport_reason == "peer_closed_incomplete_body"
    assert "secret" not in record.model_dump_json()
