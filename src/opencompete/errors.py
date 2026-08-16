"""OpenCompete 可判定的错误分类。."""

from __future__ import annotations

from opencompete.models import ErrorCategory, ErrorRecord


class OpenCompeteError(Exception):
    """所有预期业务错误的基类。."""

    code = "opencompete_error"
    category = ErrorCategory.INTERNAL
    retryable = False

    def to_record(self, message: str | None = None) -> ErrorRecord:
        """创建不含堆栈的持久化错误摘要。."""
        return ErrorRecord(
            code=self.code,
            category=self.category,
            message=message or str(self),
            retryable=self.retryable,
        )


class ConfigurationError(OpenCompeteError):
    """配置缺失、未知或类型错误。."""

    code = "configuration_error"
    category = ErrorCategory.CONFIGURATION


class AuthenticationError(OpenCompeteError):
    """Provider 拒绝密钥或认证信息。."""

    code = "authentication_error"
    category = ErrorCategory.AUTHENTICATION


class BudgetExceededError(OpenCompeteError):
    """可用预算不足，不能开始新的外部调用。."""

    code = "budget_exceeded"
    category = ErrorCategory.BUDGET


class ProviderError(OpenCompeteError):
    """外部 Provider 返回的非临时错误。."""

    code = "provider_error"
    category = ErrorCategory.PROVIDER


class TemporaryProviderError(ProviderError):
    """超时、限流、网络或 Provider 5xx 临时错误。."""

    code = "temporary_provider_error"
    retryable = True


class DataValidationError(OpenCompeteError):
    """结构化输入或模型输出不满足业务契约。."""

    code = "data_validation_error"
    category = ErrorCategory.VALIDATION


class PersistenceError(OpenCompeteError):
    """Checkpoint 或业务产物无法可靠持久化。."""

    code = "persistence_error"
    category = ErrorCategory.PERSISTENCE
