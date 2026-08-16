"""统一异常映射：领域异常 → HTTP 状态码 + 脱敏错误体。.

所有错误响应统一为 ``{"error": {code, category, message, retryable}}``，
内容来自 :func:`opencompete.security.sanitized_error`，不回显原始异常文本。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from opencompete.api.schemas import error_view
from opencompete.errors import (
    AuthenticationError,
    BudgetExceededError,
    ConfigurationError,
    DataValidationError,
    OpenCompeteError,
    PersistenceError,
    ProviderError,
    TemporaryProviderError,
)
from opencompete.security import sanitized_error

logger = logging.getLogger(__name__)

# 子类必须排在父类之前，按顺序做 isinstance 匹配。
_STATUS_BY_TYPE: tuple[tuple[type[OpenCompeteError], int], ...] = (
    (DataValidationError, 409),
    (BudgetExceededError, 402),
    (AuthenticationError, 502),
    (TemporaryProviderError, 503),
    (ProviderError, 502),
    (ConfigurationError, 500),
    (PersistenceError, 500),
)


class RunNotFoundError(Exception):
    """请求的运行不存在（映射为 404）。."""


class CapacityExhaustedError(Exception):
    """研究执行容量已满（映射为 429）。."""


class RunAlreadyActiveError(Exception):
    """同一运行已被当前进程受控执行（映射为 409）。."""


class RunNotActiveError(Exception):
    """请求的操作需要运行中的任务，但当前没有（映射为 409）。."""


def _error_response(exc: BaseException, status_code: int) -> JSONResponse:
    """把异常脱敏后包装为统一 JSON 错误响应。."""
    record = sanitized_error(exc)
    return JSONResponse(
        status_code=status_code,
        content={"error": error_view(record)},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """在应用上注册全部异常处理器。."""

    @app.exception_handler(RunNotFoundError)
    async def handle_not_found(
        request: Request, exc: RunNotFoundError
    ) -> JSONResponse:
        """运行不存在。."""
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "run_not_found",
                    "category": "validation",
                    "message": "任务不存在或尚未初始化",
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(CapacityExhaustedError)
    async def handle_capacity(
        request: Request, exc: CapacityExhaustedError
    ) -> JSONResponse:
        """执行容量已满。."""
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "research_capacity_exhausted",
                    "category": "validation",
                    "message": "已有研究正在执行，请等待其完成后再提交",
                    "retryable": True,
                }
            },
        )

    @app.exception_handler(RunAlreadyActiveError)
    async def handle_already_active(
        request: Request, exc: RunAlreadyActiveError
    ) -> JSONResponse:
        """同一运行已经在执行。."""
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "run_already_active",
                    "category": "validation",
                    "message": "本任务已经启动，正在进入研究链路。",
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(RunNotActiveError)
    async def handle_not_active(
        request: Request, exc: RunNotActiveError
    ) -> JSONResponse:
        """没有运行中的任务可操作。."""
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "run_not_active",
                    "category": "validation",
                    "message": "该任务当前没有正在执行的进程内任务，无法停止",
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """请求体不符合契约。."""
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "category": "validation",
                    "message": "请求参数不符合接口契约",
                    "retryable": False,
                    "detail": exc.errors(),
                }
            },
        )

    @app.exception_handler(ValidationError)
    async def handle_domain_validation(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        """领域模型校验失败（如非法 URL、层级重复）。."""
        messages = "；".join(
            str(item.get("msg", "")) for item in exc.errors()[:5]
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "category": "validation",
                    "message": messages or "请求内容未通过领域校验",
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(OpenCompeteError)
    async def handle_domain_error(
        request: Request, exc: OpenCompeteError
    ) -> JSONResponse:
        """领域异常按类型映射状态码。."""
        for exc_type, status_code in _STATUS_BY_TYPE:
            if isinstance(exc, exc_type):
                return _error_response(exc, status_code)
        return _error_response(exc, 500)

    @app.exception_handler(Exception)
    async def handle_unexpected(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """兜底：未知异常也走脱敏输出。."""
        logger.exception("API 未预期异常: %s", type(exc).__name__)
        return _error_response(exc, 500)
