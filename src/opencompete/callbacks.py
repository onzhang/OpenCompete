"""Apply budget reservations through LangChain's async callback API."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from open_deep_research.utils import (
    RESEARCH_EVIDENCE_ARTIFACT_TYPE,
    RESEARCH_EVIDENCE_ARTIFACT_VERSION,
    validated_tavily_extract_urls,
)
from opencompete.budget import (
    AttemptEventContext,
    AttemptOutcome,
    BudgetGuard,
    BudgetReservation,
    stable_call_id,
)
from opencompete.models import BudgetEstimate

_CALL_ID_METADATA_KEYS = ("opencompete_call_id", "call_id")


@dataclass(frozen=True, slots=True)
class _TokenUsage:
    """保留 Provider 是否缺失某一侧 Token 用量的信息。."""

    input_tokens: int | None
    output_tokens: int | None


class BudgetCallback(AsyncCallbackHandler):
    """Reserve before LangChain external calls and settle after completion."""

    raise_error = True
    run_inline = True

    def __init__(
        self,
        budget: BudgetGuard,
        *,
        default_max_output_tokens: int,
        node: str = "external_call",
        llm_input_cost_per_million_usd: float = 0.0,
        llm_output_cost_per_million_usd: float = 0.0,
        tavily_cost_per_call_usd: float = 0.0,
        tavily_max_queries_per_call: int = 3,
    ) -> None:
        """Bind callback events to one run budget."""
        super().__init__()
        self._budget = budget
        self._default_max_output_tokens = default_max_output_tokens
        self._node = node
        self._llm_input_cost_per_million_usd = _nonnegative_rate(
            llm_input_cost_per_million_usd,
            "llm_input_cost_per_million_usd",
        )
        self._llm_output_cost_per_million_usd = _nonnegative_rate(
            llm_output_cost_per_million_usd,
            "llm_output_cost_per_million_usd",
        )
        self._tavily_cost_per_call_usd = _nonnegative_rate(
            tavily_cost_per_call_usd,
            "tavily_cost_per_call_usd",
        )
        self._tavily_max_queries_per_call = _bounded_positive_int(
            tavily_max_queries_per_call,
            default=3,
            maximum=20,
        )
        self._reservations: dict[UUID, BudgetReservation] = {}
        self._tavily_tool_names: dict[UUID, str] = {}
        self._chain_parents: dict[UUID, UUID | None] = {}
        self._chain_units: dict[UUID, str] = {}

    async def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Track only a hash of a research topic across nested callback runs."""
        del serialized, tags, metadata, kwargs
        self._chain_parents[run_id] = parent_run_id
        topic = inputs.get("research_topic") if isinstance(inputs, Mapping) else None
        if isinstance(topic, str) and topic.strip():
            self._chain_units[run_id] = _research_unit_id(
                self._budget.run_id,
                topic,
            )
            return
        inherited = self._unit_for(parent_run_id)
        if inherited is not None:
            self._chain_units[run_id] = inherited

    async def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Discard completed parent mappings without persisting chain inputs."""
        del outputs, parent_run_id, tags, kwargs
        self._forget_chain(run_id)

    async def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Discard failed parent mappings; attempt errors are handled separately."""
        del error, parent_run_id, tags, kwargs
        self._forget_chain(run_id)

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Reserve input and maximum output tokens before an LLM request."""
        del serialized
        await self._reserve_model(
            run_id,
            prompts,
            metadata,
            kwargs,
            parent_run_id=parent_run_id,
            tags=tags,
        )

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Reserve tokens before a chat-model request."""
        del serialized
        prompts = [
            "\n".join(str(message.content) for message in message_batch)
            for message_batch in messages
        ]
        await self._reserve_model(
            run_id,
            prompts,
            metadata,
            kwargs,
            parent_run_id=parent_run_id,
            tags=tags,
        )

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Settle an LLM request from provider usage metadata when available."""
        del parent_run_id, tags, kwargs
        usage = _extract_llm_usage(response)
        actual = self._merge_model_usage(run_id, usage)
        await self._settle(run_id, actual)

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Conservatively retain estimated usage after an attempted LLM call."""
        del parent_run_id, tags, kwargs
        await self._settle(
            run_id,
            None,
            outcome=AttemptOutcome.FAILED,
            error=error,
        )

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Reserve conservative Tavily credits before search or extract execution."""
        del kwargs
        tool_name = serialized.get("name")
        if tool_name not in {"tavily_search", "tavily_extract"}:
            return
        if tool_name == "tavily_search":
            query_limit = _tavily_query_limit(
                metadata,
                default=self._tavily_max_queries_per_call,
            )
            tavily_credits = _query_count(
                inputs,
                input_str,
                query_limit=query_limit,
            )
            if tavily_credits == 0:
                return
            fingerprint = _tool_fingerprint(
                inputs,
                input_str,
                query_limit=query_limit,
            )
        else:
            urls, source_query, extract_depth = _extract_admission(
                inputs,
                input_str,
            )
            tavily_credits = len(urls) * (2 if extract_depth == "advanced" else 1)
            fingerprint = _content_fingerprint(
                [extract_depth, source_query, *urls]
            )
        reservation = await self._budget.reserve(
            _callback_call_id(
                metadata,
                self._budget.run_id,
                self._node,
                fingerprint,
            ),
            BudgetEstimate(
                tavily_calls=tavily_credits,
                cost_usd=tavily_credits * self._tavily_cost_per_call_usd,
            ),
            context=_attempt_context(
                metadata,
                default_node=self._node,
                unit_id=self._unit_for(parent_run_id),
                provider_kind="tavily",
                tags=tags,
            ),
        )
        self._remember(run_id, reservation)
        self._tavily_tool_names[run_id] = tool_name

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Settle search from admission or Extract from versioned usage output."""
        del parent_run_id, tags, kwargs
        reservation = self._reservations.get(run_id)
        if reservation is None or reservation.estimate.tavily_calls == 0:
            return
        tool_name = self._tavily_tool_names.get(run_id)
        actual_credits = (
            _extract_usage_credits(
                output,
                maximum=reservation.estimate.tavily_calls,
            )
            if tool_name == "tavily_extract"
            else reservation.estimate.tavily_calls
        )
        actual = (
            None
            if actual_credits is None
            else BudgetEstimate(
                tavily_calls=actual_credits,
                cost_usd=actual_credits * self._tavily_cost_per_call_usd,
            )
        )
        await self._settle(
            run_id,
            actual,
        )

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Retain the Tavily reservation when an attempted tool call fails."""
        del parent_run_id, tags, kwargs
        await self._settle(
            run_id,
            None,
            outcome=AttemptOutcome.FAILED,
            error=error,
        )

    async def _reserve_model(
        self,
        run_id: UUID,
        prompts: Sequence[str],
        metadata: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
        *,
        parent_run_id: UUID | None,
        tags: Sequence[str] | None,
    ) -> None:
        invocation_params = kwargs.get("invocation_params")
        params = invocation_params if isinstance(invocation_params, Mapping) else {}
        max_output_tokens = params.get(
            "max_tokens",
            params.get(
                "max_completion_tokens",
                params.get("max_output_tokens", self._default_max_output_tokens),
            ),
        )
        estimate = self._model_estimate(
            _estimate_input_tokens(prompts),
            max(0, int(max_output_tokens or 0)),
        )
        reservation = await self._budget.reserve(
            _callback_call_id(
                metadata,
                self._budget.run_id,
                self._node,
                _content_fingerprint(prompts),
            ),
            estimate,
            context=_attempt_context(
                metadata,
                default_node=self._node,
                unit_id=self._unit_for(parent_run_id),
                provider_kind="llm",
                tags=tags,
            ),
        )
        self._remember(run_id, reservation)

    def _model_estimate(
        self,
        input_tokens: int,
        output_tokens: int,
    ) -> BudgetEstimate:
        cost_usd = (
            input_tokens * self._llm_input_cost_per_million_usd
            + output_tokens * self._llm_output_cost_per_million_usd
        ) / 1_000_000
        return BudgetEstimate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    def _merge_model_usage(
        self,
        run_id: UUID,
        usage: _TokenUsage | None,
    ) -> BudgetEstimate | None:
        """实际用量缺失一侧时保留该侧预留，避免低估成本。."""
        if usage is None:
            return None
        reservation = self._reservations.get(run_id)
        if reservation is None:
            return None
        return self._model_estimate(
            (
                reservation.estimate.input_tokens
                if usage.input_tokens is None
                else usage.input_tokens
            ),
            (
                reservation.estimate.output_tokens
                if usage.output_tokens is None
                else usage.output_tokens
            ),
        )

    def _remember(self, run_id: UUID, reservation: BudgetReservation) -> None:
        if run_id in self._reservations:
            raise RuntimeError(f"回调运行 {run_id} 已存在未结算预算")
        self._reservations[run_id] = reservation

    async def _settle(
        self,
        run_id: UUID,
        actual: BudgetEstimate | None,
        *,
        outcome: AttemptOutcome = AttemptOutcome.SUCCEEDED,
        error: BaseException | None = None,
    ) -> None:
        reservation = self._reservations.get(run_id)
        if reservation is None:
            return
        await self._budget.settle(
            reservation.reservation_id,
            actual,
            outcome=outcome,
            error=error,
        )
        self._reservations.pop(run_id, None)
        self._tavily_tool_names.pop(run_id, None)

    def _unit_for(self, run_id: UUID | None) -> str | None:
        current = run_id
        visited: set[UUID] = set()
        while current is not None and current not in visited:
            visited.add(current)
            unit_id = self._chain_units.get(current)
            if unit_id is not None:
                return unit_id
            current = self._chain_parents.get(current)
        return None

    def _forget_chain(self, run_id: UUID) -> None:
        self._chain_units.pop(run_id, None)
        self._chain_parents.pop(run_id, None)


def _callback_call_id(
    metadata: Mapping[str, Any] | None,
    run_id: str,
    default_node: str,
    fingerprint: str,
) -> str:
    if metadata:
        for key in _CALL_ID_METADATA_KEYS:
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
    metadata = metadata or {}
    configured_node = metadata.get("langgraph_node")
    node = configured_node if isinstance(configured_node, str) else default_node
    configured_step = metadata.get("langgraph_step", 0)
    try:
        logical_call_index = int(configured_step)
    except (TypeError, ValueError):
        logical_call_index = 0
    return stable_call_id(run_id, node, fingerprint, logical_call_index)


def _attempt_context(
    metadata: Mapping[str, Any] | None,
    *,
    default_node: str,
    unit_id: str | None,
    provider_kind: str,
    tags: Sequence[str] | None,
) -> AttemptEventContext:
    values = metadata or {}
    configured_node = values.get("langgraph_node")
    node = configured_node if isinstance(configured_node, str) else default_node
    return AttemptEventContext(
        node=node,
        unit_id=unit_id,
        provider_kind=provider_kind,
        retry_number=_retry_number(tags),
        provider=_optional_text(values.get("opencompete_provider")),
        model=_optional_text(values.get("opencompete_model")),
        fallback=bool(values.get("opencompete_fallback", False)),
        route_attempts=_positive_int(
            values.get("opencompete_route_attempts"),
            default=1,
        ),
        primary_attempts=_positive_int(
            values.get("opencompete_primary_attempts"),
            default=1,
        ),
        fallback_attempts=_nonnegative_int(
            values.get("opencompete_fallback_attempts"),
            default=0,
        ),
        total_attempts=_positive_int(
            values.get("opencompete_total_attempts"),
            default=1,
        ),
        retry_initial_seconds=_nonnegative_float(
            values.get("opencompete_retry_initial_seconds"),
        ),
        retry_max_seconds=_nonnegative_float(
            values.get("opencompete_retry_max_seconds"),
        ),
        fallback_provider=_optional_text(
            values.get("opencompete_fallback_provider")
        ),
        fallback_model=_optional_text(values.get("opencompete_fallback_model")),
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _nonnegative_float(value: object) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed >= 0 else 0.0


def _retry_number(tags: Sequence[str] | None) -> int:
    attempts = [1]
    for tag in tags or ():
        prefix = "retry:attempt:"
        if not tag.startswith(prefix):
            continue
        try:
            attempts.append(max(1, int(tag.removeprefix(prefix))))
        except ValueError:
            continue
    return max(attempts)


def _research_unit_id(run_id: str, topic: str) -> str:
    normalized = " ".join(topic.casefold().split())
    return _sha256(
        json.dumps(
            [run_id, normalized],
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )


def _content_fingerprint(values: Sequence[str]) -> str:
    normalized = [" ".join(value.split()) for value in values]
    payload = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return _sha256(payload)


def _tool_fingerprint(
    inputs: Mapping[str, Any] | None,
    input_str: str,
    *,
    query_limit: int,
) -> str:
    values: Mapping[str, Any] | None = inputs
    if not values:
        try:
            parsed = json.loads(input_str)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, Mapping):
            values = parsed
    queries = (values or {}).get("queries")
    if isinstance(queries, Sequence) and not isinstance(queries, str | bytes):
        normalized = sorted(
            " ".join(str(query).casefold().split())
            for query in queries[:query_limit]
        )
        return _content_fingerprint(normalized)
    if values is None:
        return _sha256(" ".join(input_str.split()))
    payload = json.dumps(
        values,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(payload)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _nonnegative_rate(value: float, name: str) -> float:
    rate = float(value)
    if rate < 0:
        raise ValueError(f"{name} 不能为负数")
    return rate


def _bounded_positive_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return min(parsed, maximum)


def _estimate_input_tokens(prompts: Sequence[str]) -> int:
    byte_count = sum(len(prompt.encode("utf-8")) for prompt in prompts)
    return (byte_count + 2) // 3 if byte_count else 0


def _tavily_query_limit(
    metadata: Mapping[str, Any] | None,
    *,
    default: int,
) -> int:
    value = (metadata or {}).get("tavily_max_queries_per_call")
    return _bounded_positive_int(value, default=default, maximum=20)


def _extract_admission(
    inputs: Mapping[str, Any] | None,
    input_str: str,
) -> tuple[list[str], str, str]:
    values: Mapping[str, Any] = inputs or {}
    if not values:
        try:
            parsed = json.loads(input_str)
        except (TypeError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, Mapping):
            values = parsed
    raw_urls = values.get("urls")
    if not isinstance(raw_urls, Sequence) or isinstance(raw_urls, str | bytes):
        raise ValueError("Tavily Extract requires a URL list")
    if any(not isinstance(url, str) for url in raw_urls):
        raise ValueError("Tavily Extract URLs must be strings")
    urls = validated_tavily_extract_urls(list(raw_urls))
    source_query = values.get("query")
    if not isinstance(source_query, str) or not source_query.strip():
        raise ValueError("Tavily Extract requires a source query")
    extract_depth = values.get("extract_depth", "basic")
    if extract_depth not in {"basic", "advanced"}:
        raise ValueError("Tavily Extract depth must be basic or advanced")
    return urls, source_query.strip(), extract_depth


def _extract_usage_credits(output: Any, *, maximum: int) -> int | None:
    if isinstance(output, BaseMessage):
        artifact = getattr(output, "artifact", None)
        if (
            isinstance(artifact, Mapping)
            and artifact.get("type") == RESEARCH_EVIDENCE_ARTIFACT_TYPE
            and artifact.get("version") == RESEARCH_EVIDENCE_ARTIFACT_VERSION
        ):
            artifact_records = artifact.get("records")
            if isinstance(artifact_records, Sequence) and not isinstance(
                artifact_records,
                str | bytes,
            ):
                return _extract_usage_credits_from_records(
                    artifact_records,
                    maximum=maximum,
                )
        output = output.content
    if not isinstance(output, str):
        return None
    lines = output.splitlines()
    if "TAVILY_EXTRACT_JSONL_V1" not in lines:
        return None
    records: list[object] = []
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            return None
    return _extract_usage_credits_from_records(records, maximum=maximum)


def _extract_usage_credits_from_records(
    records: Sequence[object],
    *,
    maximum: int,
) -> int | None:
    by_batch: dict[int, int] = {}
    for record in records:
        if not isinstance(record, Mapping):
            return None
        if record.get("type") != "tavily_extract" or record.get("version") != 1:
            continue
        batch_index = record.get("batch_index")
        usage = record.get("usage")
        if (
            not isinstance(batch_index, int)
            or isinstance(batch_index, bool)
            or batch_index < 1
            or not isinstance(usage, Mapping)
        ):
            return None
        credits = usage.get("credits")
        if (
            not isinstance(credits, int)
            or isinstance(credits, bool)
            or credits < 0
        ):
            return None
        previous = by_batch.setdefault(batch_index, credits)
        if previous != credits:
            return None
    if not by_batch:
        return None
    actual = sum(by_batch.values())
    return actual if actual <= maximum else None


def _query_count(
    inputs: Mapping[str, Any] | None,
    input_str: str,
    *,
    query_limit: int,
) -> int:
    values: Mapping[str, Any] = inputs or {}
    if not values:
        try:
            parsed = json.loads(input_str)
        except (TypeError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, Mapping):
            values = parsed
    queries = values.get("queries")
    if isinstance(queries, Sequence) and not isinstance(queries, str | bytes):
        return min(len(queries), query_limit)
    return 1


def _extract_llm_usage(response: LLMResult) -> _TokenUsage | None:
    usage = _mapping_usage(response.llm_output)
    if usage is None:
        for generation_batch in response.generations:
            for generation in generation_batch:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if isinstance(usage, Mapping):
                    break
            if isinstance(usage, Mapping):
                break
    if not isinstance(usage, Mapping):
        return None
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    if input_tokens is None and output_tokens is None:
        return None
    return _TokenUsage(
        input_tokens=(
            None if input_tokens is None else max(0, int(input_tokens))
        ),
        output_tokens=(
            None if output_tokens is None else max(0, int(output_tokens))
        ),
    )


def _mapping_usage(llm_output: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(llm_output, Mapping):
        return None
    for key in ("token_usage", "usage", "usage_metadata"):
        value = llm_output.get(key)
        if isinstance(value, Mapping):
            return value
    return None
