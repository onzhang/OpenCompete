"""API 路由：包装 ApplicationRunner 的全部 HTTP 端点。.

单例依赖（runner / repository / hub / registry / meta / 确认缓存）
挂在 ``app.state``，由 ``server.create_app`` 的 lifespan 装配。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from opencompete.api.errors import (
    RunAlreadyActiveError,
    RunNotActiveError,
    RunNotFoundError,
)
from opencompete.api.events import follow_events
from opencompete.api.schemas import (
    CandidateSelectionBody,
    CreateRunBody,
    ScopeConfirmationBody,
    bundle_view,
    run_detail,
    run_list_item,
)
from opencompete.errors import DataValidationError, PersistenceError
from opencompete.models import (
    DiscoveryCandidate,
    RunHandle,
    RunManifest,
    RunStatus,
    ScopeSpec,
)

router = APIRouter(prefix="/api")


@dataclass
class CachedConfirmation:
    """进程内缓存的待确认载荷（来自最近一次 RunHandle）。."""

    kind: str
    scope: ScopeSpec
    candidates: tuple[DiscoveryCandidate, ...] = field(default_factory=tuple)


def _state(request: Request) -> Any:
    """访问 lifespan 装配的单例集合。."""
    return request.app.state


def _load_manifest(state: Any, run_id: str) -> RunManifest:
    """读取 manifest，不存在时统一转 404。."""
    try:
        return state.repository.load_manifest(run_id)
    except (PersistenceError, ValueError) as exc:
        raise RunNotFoundError(run_id) from exc


def _cache_from_handle(state: Any, handle: RunHandle) -> None:
    """把带确认点的句柄写入进程内缓存，终态则清除。."""
    if handle.pending_confirmation and handle.scope is not None:
        kind = (
            handle.confirmation_kind.value
            if handle.confirmation_kind
            else "scope_confirmation"
        )
        state.confirmation_cache[handle.run_id] = CachedConfirmation(
            kind=kind,
            scope=handle.scope,
            candidates=handle.discovery_candidates,
        )
    else:
        state.confirmation_cache.pop(handle.run_id, None)


async def _precheck_confirmation(
    state: Any,
    run_id: str,
    expected_revision: int,
    *,
    kind: str,
) -> RunHandle:
    """基于 durable Checkpoint 校验确认类型与版本，失败即 409。."""
    if state.registry.is_active(run_id):
        raise RunAlreadyActiveError(f"任务已在执行中: {run_id}")
    inspection = await state.runner.inspect_run(run_id)
    handle = inspection.handle
    if not inspection.has_confirmation_interrupt or not handle.pending_confirmation:
        raise DataValidationError("当前任务不在可提交的用户确认点")
    actual_kind = (
        handle.confirmation_kind.value
        if handle.confirmation_kind is not None
        else "scope_confirmation"
    )
    if actual_kind != kind:
        current = (
            "候选选择" if actual_kind == "candidate_selection" else "范围确认"
        )
        raise DataValidationError(
            f"当前确认点是「{current}」，与提交的决策类型不匹配，请刷新后重试"
        )
    if handle.scope is None:
        raise DataValidationError("确认点没有可用的研究范围")
    if handle.scope.revision != expected_revision:
        raise DataValidationError(
            f"范围版本已过期（当前 rev.{handle.scope.revision}），请刷新后重新确认"
        )
    return handle


def _detail_response(
    state: Any,
    run_id: str,
    *,
    handle: RunHandle | None = None,
    recoverable: bool = False,
    cache_handle: bool = True,
) -> dict[str, Any]:
    """组装详情视图：manifest + meta + 待确认载荷。."""
    manifest = _load_manifest(state, run_id)
    meta = state.meta_store.ensure(run_id, state.repository)
    if handle is not None and cache_handle:
        _cache_from_handle(state, handle)
    if handle is not None:
        updates: dict[str, Any] = {"status": handle.status}
        if handle.scope is not None:
            updates["scope_revision"] = handle.scope.revision
        if handle.bundle is not None:
            updates["artifact_revision"] = handle.bundle.artifact_revision
            updates["budget_summary"] = handle.bundle.budget
        manifest = manifest.model_copy(update=updates)

    cached = state.confirmation_cache.get(run_id)
    pending_scope = None
    kind = None
    candidates: tuple[DiscoveryCandidate, ...] = ()
    if handle is not None and handle.pending_confirmation:
        pending_scope = handle.scope
        kind = (
            handle.confirmation_kind.value
            if handle.confirmation_kind is not None
            else "scope_confirmation"
        )
        candidates = handle.discovery_candidates
    elif manifest.status.value == "awaiting_confirmation" and cached is not None:
        pending_scope = cached.scope
        kind = cached.kind
        candidates = cached.candidates
    return run_detail(
        manifest,
        meta,
        active=state.registry.is_active(run_id),
        pending_scope=pending_scope,
        confirmation_kind=kind,
        candidates=candidates,
        recoverable=recoverable,
    )


@router.get("/health")
async def health() -> dict[str, str]:
    """存活检查。."""
    return {"status": "ok"}


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    """暴露前端展示所需的预算上限（不含任何模型或密钥配置）。."""
    settings = _state(request).settings
    return {
        "budget_limit": {
            "input_tokens": settings.max_input_tokens,
            "output_tokens": settings.max_output_tokens,
            "tavily_calls": settings.max_tavily_calls,
            "cost_usd": settings.max_cost_usd,
        }
    }


@router.get("/runs")
async def list_runs(request: Request) -> list[dict[str, Any]]:
    """全部任务的列表摘要（按更新时间倒序）。."""
    state = _state(request)
    summaries = await state.runner.list_runs()
    items: list[dict[str, Any]] = []
    for summary in summaries:
        try:
            manifest = state.repository.load_manifest(summary.run_id)
        except PersistenceError:
            continue
        meta = state.meta_store.ensure(summary.run_id, state.repository)
        item = run_list_item(
            manifest, meta, active=state.registry.is_active(summary.run_id)
        )
        # 列表层的哈希校验可能把损坏产物降级为 failed，以其为准。
        item["status"] = summary.status.value
        items.append(item)
    return items


@router.post("/runs", status_code=202)
async def create_run(request: Request, body: CreateRunBody) -> JSONResponse:
    """创建研究任务并立即返回 202；范围规划与候选发现转入后台。.

    轻量初始化（分配 run_id、写 manifest 与 ``run_created`` 事件）同步
    完成；随后的模型范围规划与 Tavily 佐证检索作为后台任务运行，进度
    经事件直播接口跟踪，到达确认点后详情页自动切换确认表单。
    """
    state = _state(request)
    startup = await state.runner.initialize_commission_run(body.to_domain())
    run_id = startup.handle.run_id
    try:
        state.registry.reserve(run_id)
    except Exception:
        # 未启动的协程必须显式关闭，否则事件循环报 never awaited。
        startup.execution.close()
        raise
    try:
        state.registry.start_reserved(
            run_id,
            state.runner.guard_cancellation(run_id, startup.execution),
        )
    except Exception:
        state.registry.release(run_id)
        raise
    state.meta_store.write(
        run_id,
        {
            "query": body.query,
            "entities": list(body.entities),
            "confirmation_kind": None,
            "created_at": None,
        },
    )
    return JSONResponse(
        status_code=202,
        content=_detail_response(state, run_id, handle=startup.handle),
    )


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> dict[str, Any]:
    """只读任务详情：投影 Checkpoint，绝不恢复或推进研究。."""
    state = _state(request)
    manifest = _load_manifest(state, run_id)
    if manifest.status in {
        RunStatus.COMPLETED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        return _detail_response(state, run_id)
    inspection = await state.runner.inspect_run(run_id)
    return _detail_response(
        state,
        run_id,
        handle=inspection.handle,
        recoverable=inspection.recoverable,
        cache_handle=False,
    )


@router.get("/runs/{run_id}/bundle")
async def get_bundle(request: Request, run_id: str) -> dict[str, Any]:
    """已发布任务的完整结构化产物（含引用编号注入）。."""
    state = _state(request)
    _load_manifest(state, run_id)
    bundle = await state.runner.load_run(run_id)
    return bundle_view(bundle.model_dump(mode="json"))


@router.post("/runs/{run_id}/candidate-selection")
async def confirm_candidates(
    request: Request, run_id: str, body: CandidateSelectionBody
) -> dict[str, Any]:
    """提交候选选择决定，任务推进到范围确认点。."""
    state = _state(request)
    _load_manifest(state, run_id)
    handle = await _precheck_confirmation(
        state, run_id, body.expected_revision, kind="candidate_selection"
    )
    known_candidate_ids = {
        candidate.candidate_id for candidate in handle.discovery_candidates
    }
    unknown_candidate_ids = {
        choice.candidate_id for choice in body.selected_candidates
    } - known_candidate_ids
    if unknown_candidate_ids:
        raise DataValidationError("候选选择包含当前确认页没有展示的对象")
    # 到达确认点的后台执行可能仍在收尾；先等完再预留执行槽，避免与
    # 用户确认请求竞态返回 409。
    await state.registry.wait_idle(run_id)
    async with state.registry.inflight(run_id):
        handle = await state.runner.resume_run(run_id, body.to_domain())
    if handle.scope is not None:
        state.meta_store.merge(
            run_id,
            entities=[entity.name for entity in handle.scope.entities],
            confirmation_kind=(
                handle.confirmation_kind.value
                if handle.confirmation_kind
                else None
            ),
        )
    return _detail_response(state, run_id, handle=handle)


@router.post("/runs/{run_id}/scope-confirmation")
async def confirm_scope(
    request: Request, run_id: str, body: ScopeConfirmationBody
) -> Any:
    """提交范围确认决定。.

    拒绝（accepted=False）同步取消并返回终态；接受则把完整研究
    放入后台执行并立即返回 202，进度经事件直播接口跟踪。
    """
    state = _state(request)
    _load_manifest(state, run_id)
    await _precheck_confirmation(
        state, run_id, body.expected_revision, kind="scope_confirmation"
    )
    confirmation = body.to_domain()
    if not confirmation.accepted:
        await state.registry.wait_idle(run_id)
        async with state.registry.inflight(run_id):
            handle = await state.runner.resume_run(run_id, confirmation)
        return _detail_response(state, run_id, handle=handle)
    state.registry.reserve(run_id)
    try:
        handle = await state.runner.freeze_confirmed_scope(run_id, confirmation)
        state.confirmation_cache.pop(run_id, None)
        state.registry.start_reserved(
            run_id,
            state.runner.guard_cancellation(
                run_id, state.runner.resume_execution(run_id)
            ),
        )
    except Exception:
        state.registry.release(run_id)
        raise
    return JSONResponse(
        status_code=202,
        content=_detail_response(state, run_id, handle=handle),
    )


@router.post("/runs/{run_id}/resume")
async def resume_run(request: Request, run_id: str) -> JSONResponse:
    """显式继续一个非确认点的中断研究，读取接口绝不会调用此路径。."""
    state = _state(request)
    manifest = _load_manifest(state, run_id)
    if manifest.status is RunStatus.CANCELLED:
        raise DataValidationError("任务已取消，不能恢复执行")
    inspection = await state.runner.inspect_run(run_id)
    if inspection.handle.pending_confirmation:
        raise DataValidationError("任务正在等待用户确认，请先完成对应确认")
    if not inspection.recoverable:
        raise DataValidationError("当前任务不是可恢复的中断研究")
    state.registry.reserve(run_id)
    try:
        state.registry.start_reserved(
            run_id,
            state.runner.guard_cancellation(
                run_id, state.runner.resume_execution(run_id)
            ),
        )
    except Exception:
        state.registry.release(run_id)
        raise
    return JSONResponse(
        status_code=202,
        content=_detail_response(
            state,
            run_id,
            handle=inspection.handle,
            recoverable=False,
        ),
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str) -> dict[str, Any]:
    """停止运行中的研究；取消是终态，不保留恢复。.

    只作用于本进程正在执行的任务；确认点等待的任务请用对应确认
    接口拒绝。取消请求会等待后台协程完成终态落盘后返回。
    """
    state = _state(request)
    manifest = _load_manifest(state, run_id)
    if manifest.status in {
        RunStatus.COMPLETED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        raise RunNotActiveError("任务已结束")
    task = state.registry.request_cancel(run_id)
    if task is None:
        raise RunNotActiveError("没有正在执行的任务")
    # 取消落盘由 guard_cancellation 在被取消协程内完成；此处只等收尾。
    with suppress(asyncio.CancelledError, Exception):
        await task
    return _detail_response(state, run_id)


@router.get("/runs/{run_id}/events")
async def stream_events(
    request: Request,
    run_id: str,
    after: int = Query(default=0, ge=0),
) -> EventSourceResponse:
    """任务事件直播（SSE）：回放历史后实时续播。.

    帧 ``id`` 为 events.jsonl 行号；浏览器自动重连时的
    ``Last-Event-ID`` 请求头优先于 ``after`` 查询参数。
    """
    state = _state(request)
    _load_manifest(state, run_id)
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        after = int(last_event_id)
    events_path = (
        Path(state.settings.data_dir) / "runs" / run_id / "events.jsonl"
    )
    generator = follow_events(
        run_id,
        after,
        events_path=events_path,
        hub=state.hub,
        registry=state.registry,
        repository=state.repository,
    )
    return EventSourceResponse(generator, ping=15)
