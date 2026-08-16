"""Provide atomic local budget reservation and settlement."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from time import perf_counter
from typing import Protocol, runtime_checkable
from uuid import uuid4

from open_deep_research.model_routing import (
    TRANSIENT_MODEL_EXCEPTIONS,
    retry_delay_seconds,
)
from opencompete.errors import BudgetExceededError
from opencompete.models import BudgetEstimate, BudgetSnapshot, ErrorRecord
from opencompete.security import sanitized_error

_BUDGET_FIELDS = ("input_tokens", "output_tokens", "tavily_calls", "cost_usd")
logger = logging.getLogger(__name__)


class ReservationStatus(str, Enum):
    """Describe the lifecycle state of one external-call reservation."""

    RESERVED = "reserved"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class AttemptOutcome(str, Enum):
    """Describe the externally observed result of one sent attempt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AttemptEventContext:
    """Carry only non-sensitive identifiers needed for attempt diagnostics."""

    node: str = "external_call"
    unit_id: str | None = None
    provider_kind: str = "external"
    retry_number: int = 1
    provider: str | None = None
    model: str | None = None
    fallback: bool = False
    route_attempts: int = 1
    primary_attempts: int = 1
    fallback_attempts: int = 0
    total_attempts: int = 1
    retry_initial_seconds: float = 0.0
    retry_max_seconds: float = 0.0
    fallback_provider: str | None = None
    fallback_model: str | None = None

    def __post_init__(self) -> None:
        """Reject ambiguous diagnostic context before an external call starts."""
        if not self.node.strip():
            raise ValueError("attempt 事件的 node 不能为空")
        if not self.provider_kind.strip():
            raise ValueError("attempt 事件的 provider_kind 不能为空")
        if self.retry_number < 1:
            raise ValueError("attempt 事件的 retry_number 必须大于零")
        if self.route_attempts < 1 or self.primary_attempts < 1:
            raise ValueError("模型路由尝试次数必须大于零")
        if self.fallback_attempts < 0 or self.total_attempts < 1:
            raise ValueError("模型总尝试次数无效")
        if self.retry_initial_seconds < 0 or self.retry_max_seconds < 0:
            raise ValueError("模型重试等待时间不能为负数")


AttemptEvent = dict[str, object]
AttemptEventSink = Callable[[AttemptEvent], object]


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    """Record one actual attempt for a stable logical external call."""

    reservation_id: str
    run_id: str
    call_id: str
    attempt_id: str
    estimate: BudgetEstimate
    actual: BudgetEstimate | None = None
    status: ReservationStatus = ReservationStatus.RESERVED
    replayed: bool = False
    execution_id: str | None = field(default_factory=lambda: str(uuid4()))
    attempt_number: int = 0


@dataclass(frozen=True, slots=True)
class _ActiveAttempt:
    """Keep process-local timing and redacted event context until settlement."""

    reservation: BudgetReservation
    context: AttemptEventContext
    started_at: float


@runtime_checkable
class BudgetLedger(Protocol):
    """Persist reservations and arbitrate limits atomically."""

    async def try_reserve(
        self,
        reservation: BudgetReservation,
        limit: BudgetEstimate,
    ) -> BudgetReservation | None:
        """Insert a reservation if the run still has sufficient budget."""

    async def settle(
        self,
        reservation_id: str,
        actual: BudgetEstimate | None,
    ) -> BudgetReservation:
        """Settle a reservation, retaining its estimate when usage is unknown."""

    async def cancel(self, reservation_id: str) -> BudgetReservation:
        """Cancel a reservation known not to have reached the provider."""

    async def snapshot(
        self,
        run_id: str,
        limit: BudgetEstimate,
    ) -> BudgetSnapshot:
        """Return budget totals for one run."""


def stable_call_id(
    run_id: str,
    node: str,
    unit_id: str,
    logical_call_index: int,
) -> str:
    """Build a deterministic identifier for one logical external call."""
    payload = json.dumps(
        [run_id, node, unit_id, logical_call_index],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BudgetGuard:
    """Expose the application budget API over a durable ledger."""

    def __init__(
        self,
        ledger: BudgetLedger,
        limit: BudgetEstimate,
        run_id: str,
        *,
        execution_id: str | None = None,
        event_sink: AttemptEventSink | None = None,
    ) -> None:
        """Bind a ledger and limit to one application run."""
        if not run_id.strip():
            raise ValueError("run_id 不能为空")
        resolved_execution_id = execution_id or str(uuid4())
        if not resolved_execution_id.strip():
            raise ValueError("execution_id 不能为空")
        self._ledger = ledger
        self._limit = limit
        self._run_id = run_id
        self._execution_id = resolved_execution_id
        self._event_sink = event_sink
        self._active_attempts: dict[str, _ActiveAttempt] = {}

    @property
    def run_id(self) -> str:
        """Return the run whose budget this guard controls."""
        return self._run_id

    @property
    def limit(self) -> BudgetEstimate:
        """Return the immutable configured limit."""
        return self._limit

    @property
    def execution_id(self) -> str:
        """Return the Graph execution shared by every attempt from this guard."""
        return self._execution_id

    async def reserve(
        self,
        call_id: str,
        estimate: BudgetEstimate,
        *,
        context: AttemptEventContext | None = None,
    ) -> BudgetReservation:
        """Reserve budget before an external request can begin."""
        if not call_id.strip():
            raise ValueError("call_id 不能为空")
        reservation = BudgetReservation(
            reservation_id=str(uuid4()),
            run_id=self._run_id,
            call_id=call_id,
            attempt_id=str(uuid4()),
            estimate=estimate,
            execution_id=self._execution_id,
        )
        accepted = await self._ledger.try_reserve(reservation, self._limit)
        if accepted is None:
            raise BudgetExceededError(
                f"任务 {self._run_id} 的剩余预算不足，外部调用未发送"
            )
        active = _ActiveAttempt(
            reservation=accepted,
            context=context or AttemptEventContext(),
            started_at=perf_counter(),
        )
        self._active_attempts[accepted.reservation_id] = active
        self._emit_attempt_event("call_attempt_started", active)
        return accepted

    async def settle(
        self,
        reservation_id: str,
        actual: BudgetEstimate | None,
        *,
        outcome: AttemptOutcome = AttemptOutcome.SUCCEEDED,
        error: BaseException | None = None,
    ) -> BudgetReservation:
        """Settle an external call using actual or conservatively estimated usage."""
        if outcome is AttemptOutcome.FAILED and error is None:
            raise ValueError("失败的 attempt 必须提供异常以生成脱敏错误码")
        if outcome is AttemptOutcome.SUCCEEDED and error is not None:
            raise ValueError("成功的 attempt 不能携带异常")
        settled = await self._ledger.settle(reservation_id, actual)
        active = self._active_attempts.pop(reservation_id, None)
        if active is not None:
            error_record = sanitized_error(error) if error is not None else None
            self._emit_attempt_event(
                (
                    "call_attempt_succeeded"
                    if outcome is AttemptOutcome.SUCCEEDED
                    else "call_attempt_failed"
                ),
                replace(active, reservation=settled),
                duration_ms=_duration_ms(active.started_at),
                error_record=error_record,
                error_type=(type(error).__name__ if error is not None else None),
            )
            if (
                error is not None
                and error_record is not None
                and isinstance(error, TRANSIENT_MODEL_EXCEPTIONS)
            ):
                self._emit_retry_event(
                    replace(active, reservation=settled),
                    error_record,
                )
        return settled

    async def cancel(self, reservation_id: str) -> BudgetReservation:
        """Release a reservation only when no external request was sent."""
        cancelled = await self._ledger.cancel(reservation_id)
        active = self._active_attempts.pop(reservation_id, None)
        if active is not None:
            self._emit_attempt_event(
                "call_attempt_cancelled_before_send",
                replace(active, reservation=cancelled),
                duration_ms=_duration_ms(active.started_at),
            )
        return cancelled

    async def snapshot(self) -> BudgetSnapshot:
        """Return current totals for this run."""
        return await self._ledger.snapshot(self._run_id, self._limit)

    def _emit_attempt_event(
        self,
        event_type: str,
        active: _ActiveAttempt,
        *,
        duration_ms: int | None = None,
        error_record: ErrorRecord | None = None,
        error_type: str | None = None,
    ) -> None:
        """Best-effort diagnostics must never change durable budget semantics."""
        sink = self._event_sink
        if sink is None:
            return
        reservation = active.reservation
        usage = reservation.actual or reservation.estimate
        event: AttemptEvent = {
            "event": event_type,
            "occurred_at": datetime.now(UTC).isoformat(),
            "run_id": reservation.run_id,
            "execution_id": reservation.execution_id or self._execution_id,
            "node": active.context.node,
            "unit_id": active.context.unit_id,
            "provider_kind": active.context.provider_kind,
            "call_id": reservation.call_id,
            "attempt_id": reservation.attempt_id,
            "attempt_number": reservation.attempt_number,
            "retry_number": active.context.retry_number,
            "replayed": reservation.replayed,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "tavily_calls": usage.tavily_calls,
            "cost_usd": usage.cost_usd,
            "usage_source": (
                "actual" if reservation.actual is not None else "estimated"
            ),
        }
        event.update(_route_event_values(active.context))
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        if error_record is not None:
            event.update(_error_event_values(error_record))
        if error_type is not None:
            event["error_type"] = error_type
        try:
            sink(event)
        except Exception:
            logger.warning(
                "attempt 事件写入失败：%s/%s",
                reservation.run_id,
                reservation.attempt_id,
            )

    def _emit_retry_event(
        self,
        active: _ActiveAttempt,
        error: ErrorRecord,
    ) -> None:
        """在 LangChain 实际等待前发布与路由配置一致的重试提示。."""
        sink = self._event_sink
        context = active.context
        if sink is None or context.provider_kind != "llm":
            return
        route_attempt = context.retry_number
        if route_attempt < context.route_attempts:
            next_provider = context.provider
            next_model = context.model
            next_fallback = context.fallback
            next_attempt = (
                context.primary_attempts + route_attempt + 1
                if context.fallback
                else route_attempt + 1
            )
            delay = retry_delay_seconds(
                route_attempt,
                initial=context.retry_initial_seconds,
                maximum=context.retry_max_seconds,
            )
        elif not context.fallback and context.fallback_attempts > 0:
            next_provider = context.fallback_provider
            next_model = context.fallback_model
            next_fallback = True
            next_attempt = context.primary_attempts + 1
            delay = 0.0
        else:
            return
        event: AttemptEvent = {
            "event": "model_retry_scheduled",
            "occurred_at": datetime.now(UTC).isoformat(),
            "run_id": active.reservation.run_id,
            "execution_id": (
                active.reservation.execution_id or self._execution_id
            ),
            "node": context.node,
            "unit_id": context.unit_id,
            "call_id": active.reservation.call_id,
            "attempt": _window_attempt(context),
            "next_attempt": next_attempt,
            "total_attempts": context.total_attempts,
            "provider": context.provider,
            "model": context.model,
            "fallback": context.fallback,
            "next_provider": next_provider,
            "next_model": next_model,
            "next_fallback": next_fallback,
            "delay_seconds": delay,
            **_error_event_values(error),
        }
        try:
            sink(event)
        except Exception:
            logger.warning(
                "模型重试事件写入失败：%s/%s",
                active.reservation.run_id,
                active.reservation.attempt_id,
            )


def _window_attempt(context: AttemptEventContext) -> int:
    """把路由内尝试序号转换为当前自动执行窗口序号。."""
    if context.fallback:
        return context.primary_attempts + context.retry_number
    return context.retry_number


def _route_event_values(context: AttemptEventContext) -> AttemptEvent:
    """只输出非秘密模型身份和当前执行窗口信息。."""
    if context.provider_kind != "llm":
        return {}
    return {
        "provider": context.provider,
        "model": context.model,
        "fallback": context.fallback,
        "route_attempt": context.retry_number,
        "window_attempt": _window_attempt(context),
        "total_attempts": context.total_attempts,
    }


def _error_event_values(error: ErrorRecord) -> AttemptEvent:
    """把已经脱敏的 Provider 错误字段复制到事件。."""
    provider_error = error.exception_type is not None
    return {
        "error_code": error.code,
        "error_message": error.message if provider_error else None,
        "error_detail": error.detail if provider_error else None,
        "exception_type": error.exception_type,
        "cause_type": error.cause_type,
        "transport_reason": error.transport_reason,
        "http_status": error.http_status,
        "provider_error_code": error.provider_error_code,
        "request_id": error.request_id,
        "retryable": error.retryable,
    }


class InMemoryBudgetLedger:
    """Implement the ledger contract for deterministic unit tests."""

    def __init__(self) -> None:
        """Initialize an empty process-local ledger."""
        self._reservations: dict[str, BudgetReservation] = {}
        self._lock = asyncio.Lock()

    async def try_reserve(
        self,
        reservation: BudgetReservation,
        limit: BudgetEstimate,
    ) -> BudgetReservation | None:
        """Atomically check and insert an in-memory reservation."""
        if reservation.execution_id is None or not reservation.execution_id.strip():
            raise ValueError("新预留必须包含非空 execution_id")
        async with self._lock:
            spent, reserved = self._totals(reservation.run_id)
            requested = _add_estimates(spent, reserved, reservation.estimate)
            if not _within_limit(requested, limit):
                return None
            replayed = any(
                existing.run_id == reservation.run_id
                and existing.call_id == reservation.call_id
                and existing.status is not ReservationStatus.CANCELLED
                and existing.execution_id != reservation.execution_id
                for existing in self._reservations.values()
            )
            same_execution_prior = any(
                existing.run_id == reservation.run_id
                and existing.call_id == reservation.call_id
                and existing.status is not ReservationStatus.CANCELLED
                and existing.execution_id == reservation.execution_id
                for existing in self._reservations.values()
            )
            attempt_number = 1 + sum(
                existing.run_id == reservation.run_id
                and existing.call_id == reservation.call_id
                for existing in self._reservations.values()
            )
            accepted = replace(
                reservation,
                replayed=replayed and not same_execution_prior,
                attempt_number=attempt_number,
            )
            self._reservations[accepted.reservation_id] = accepted
            return accepted

    async def settle(
        self,
        reservation_id: str,
        actual: BudgetEstimate | None,
    ) -> BudgetReservation:
        """Settle an in-memory reservation idempotently."""
        async with self._lock:
            current = self._get(reservation_id)
            if current.status is ReservationStatus.SETTLED:
                return current
            if current.status is ReservationStatus.CANCELLED:
                raise ValueError("已取消的预算预留不能结算")
            settled = replace(
                current,
                actual=actual,
                status=ReservationStatus.SETTLED,
            )
            self._reservations[reservation_id] = settled
            return settled

    async def cancel(self, reservation_id: str) -> BudgetReservation:
        """Cancel an in-memory reservation idempotently."""
        async with self._lock:
            current = self._get(reservation_id)
            if current.status is ReservationStatus.CANCELLED:
                return current
            if current.status is ReservationStatus.SETTLED:
                raise ValueError("已结算的预算预留不能取消")
            cancelled = replace(current, status=ReservationStatus.CANCELLED)
            self._reservations[reservation_id] = cancelled
            return cancelled

    async def snapshot(
        self,
        run_id: str,
        limit: BudgetEstimate,
    ) -> BudgetSnapshot:
        """Return an internally consistent snapshot for one run."""
        async with self._lock:
            spent, reserved = self._totals(run_id)
            remaining = _subtract_estimates(limit, spent, reserved)
            return BudgetSnapshot(
                limit=limit,
                spent=spent,
                reserved=reserved,
                remaining=remaining,
            )

    def _get(self, reservation_id: str) -> BudgetReservation:
        try:
            return self._reservations[reservation_id]
        except KeyError as exc:
            raise KeyError(f"预算预留不存在：{reservation_id}") from exc

    def _totals(self, run_id: str) -> tuple[BudgetEstimate, BudgetEstimate]:
        spent = BudgetEstimate()
        reserved = BudgetEstimate()
        for reservation in self._reservations.values():
            if reservation.run_id != run_id:
                continue
            if reservation.status is ReservationStatus.RESERVED:
                reserved = _add_estimates(reserved, reservation.estimate)
            elif reservation.status is ReservationStatus.SETTLED:
                spent = _add_estimates(
                    spent,
                    reservation.actual or reservation.estimate,
                )
        return spent, reserved


def _add_estimates(*values: BudgetEstimate) -> BudgetEstimate:
    return BudgetEstimate(
        **{
            field: sum(getattr(value, field) for value in values)
            for field in _BUDGET_FIELDS
        }
    )


def _subtract_estimates(
    limit: BudgetEstimate,
    *values: BudgetEstimate,
) -> BudgetEstimate:
    return BudgetEstimate(
        **{
            field: max(
                0,
                getattr(limit, field)
                - sum(getattr(value, field) for value in values),
            )
            for field in _BUDGET_FIELDS
        }
    )


def _within_limit(value: BudgetEstimate, limit: BudgetEstimate) -> bool:
    return all(
        getattr(value, field) <= getattr(limit, field)
        for field in _BUDGET_FIELDS
    )


def _duration_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))
