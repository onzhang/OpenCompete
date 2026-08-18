"""API 路由：包装 ApplicationRunner 的全部 HTTP 端点。.

单例依赖（runner / repository / hub / registry / meta / 确认缓存）
挂在 ``app.state``，由 ``server.create_app`` 的 lifespan 装配。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from langchain.chat_models import init_chat_model
from sse_starlette.sse import EventSourceResponse

from open_deep_research.utils import (
    get_api_key_for_model,
    get_base_url_for_model,
    model_connection_context,
)
from opencompete.api.errors import (
    RunAlreadyActiveError,
    RunNotActiveError,
    RunNotFoundError,
)
from opencompete.api.events import follow_events
from opencompete.api.schemas import (
    CandidateSelectionBody,
    CreateRunBody,
    CredentialUpdateBody,
    ScopeConfirmationBody,
    SettingsUpdateBody,
    bundle_view,
    run_detail,
    run_list_item,
)
from opencompete.connections import runtime_model_connections
from opencompete.errors import DataValidationError, PersistenceError
from opencompete.models import (
    DiscoveryCandidate,
    RunHandle,
    RunManifest,
    RunStatus,
    ScopeSpec,
)
from opencompete.providers import AsyncTavilyClient
from opencompete.security import sanitized_error
from opencompete.settings import thinking_disable_options

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


@asynccontextmanager
async def _settings_creation_lock(state: Any):
    """正式服务下串行化设置保存与任务初始化；兼容最小路由测试替身。."""
    service = getattr(state, "settings_service", None)
    if service is None:
        yield
        return
    async with service.lock:
        yield


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
    project_handle_status: bool = True,
) -> dict[str, Any]:
    """组装详情视图：manifest + meta + 待确认载荷。."""
    manifest = _load_manifest(state, run_id)
    meta = state.meta_store.ensure(run_id, state.repository)
    if handle is not None and cache_handle:
        _cache_from_handle(state, handle)
    if handle is not None and project_handle_status:
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


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, object]:
    """读取本地设置的严格公开视图，绝不回显凭据或 Base URL。."""
    return await _state(request).settings_service.public_view()


@router.put("/settings")
async def update_settings(
    request: Request,
    body: SettingsUpdateBody,
) -> dict[str, object]:
    """保存只影响后续新任务的非秘密设置。."""
    state = _state(request)
    view = await state.settings_service.update_settings(
        body.values,
        expected_revision=body.expected_revision,
        on_updated=lambda settings, revision: state.runner.reconfigure_default_settings(
            settings, revision=revision
        ),
    )
    state.settings = state.settings_service.settings
    return view


@router.put("/settings/credentials")
async def update_credential(
    request: Request,
    body: CredentialUpdateBody,
) -> dict[str, object]:
    """设置或清除单个 `.env` 凭据，未终态任务存在时拒绝轮换。."""
    state = _state(request)

    async def active_statuses() -> dict[str, RunStatus]:
        return {
            summary.run_id: summary.status
            for summary in await state.runner.list_runs()
        }

    return await state.settings_service.update_credential(
        body.name,
        operation=body.operation,
        value=body.value,
        expected_revision=body.expected_revision,
        active_statuses_loader=active_statuses,
    )


@router.get("/settings/connections/{role}/api-key")
async def reveal_connection_api_key(request: Request, role: str) -> Response:
    """用户主动查看当前连接 Key；响应不缓存，服务端不记录值。."""
    value = await _state(request).settings_service.reveal_connection_api_key(role)
    return JSONResponse(
        content={"role": role, "configured": value is not None, "api_key": value},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/settings/connections/{target}/test", response_model=None)
async def test_connection(
    request: Request, target: str
) -> dict[str, object] | JSONResponse:
    """显式执行一次最小连接验证，不创建任务或持久化 Provider 输出。."""
    settings = _state(request).settings_service.settings
    normalized = target.strip()
    model_by_target = {
        "primary": settings.research_model,
        "fallback": settings.fallback_model,
    }
    try:
        if normalized == "tavily":
            client = AsyncTavilyClient(api_key=None)
            await client.search(query="OpenCompete connection check", max_results=1)
            return {"target": "tavily", "status": "ok"}
        model = model_by_target.get(normalized)
        if model is None:
            raise DataValidationError("该连接未配置备用模型或连接角色不受支持")
        connections = runtime_model_connections(settings)
        with model_connection_context(connections):
            runnable = init_chat_model(
                model=model,
                max_tokens=1,
                api_key=get_api_key_for_model(
                    model,
                    {},
                    connection_role=normalized,
                ),
                base_url=get_base_url_for_model(
                    model,
                    {},
                    connection_role=normalized,
                ),
                timeout=settings.llm_request_timeout_seconds,
                max_retries=0,
                **thinking_disable_options(model),
            )
            await runnable.ainvoke("hi")
        provider, _, model_name = model.partition(":")
        return {
            "target": normalized,
            "status": "ok",
            "provider": provider,
            "model": model_name,
        }
    except DataValidationError:
        raise
    except Exception as exc:
        record = sanitized_error(exc)
        return JSONResponse(
            status_code=502 if not record.retryable else 503,
            content={
                "error": {
                    "code": record.code,
                    "category": record.category.value,
                    "message": record.message,
                    "retryable": record.retryable,
                }
            },
        )


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    """兼容旧页面的预算摘要（不含模型或密钥配置）。."""
    settings = _state(request).settings_service.settings
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
    async with _settings_creation_lock(state):
        # 设置保存和任务初始化共用锁：刚创建的 Manifest 必然使用同一版本快照。
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
        RunStatus.CANCELLED,
    }:
        return _detail_response(state, run_id)
    try:
        inspection = await state.runner.inspect_run(run_id)
    except PersistenceError:
        if manifest.status is RunStatus.FAILED:
            return _detail_response(state, run_id)
        raise
    return _detail_response(
        state,
        run_id,
        handle=inspection.handle,
        recoverable=inspection.recoverable,
        cache_handle=False,
        project_handle_status=manifest.status is not RunStatus.FAILED,
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
    # 到达确认点的后台执行可能仍在收尾；先等完再做预检与预留，
    # 避免读到收尾前状态或与用户确认请求竞态返回 409。
    await state.registry.wait_idle(run_id)
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
    # 同 candidate-selection：先等后台收尾，再预检确认快照。
    await state.registry.wait_idle(run_id)
    await _precheck_confirmation(
        state, run_id, body.expected_revision, kind="scope_confirmation"
    )
    confirmation = body.to_domain()
    if not confirmation.accepted:
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


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(request: Request, run_id: str) -> Response:
    """彻底删除任务：Checkpoint thread、业务产物目录与展示索引。.

    不可恢复，且只允许删除本进程没有在执行的任务（运行中先停止）。
    Checkpoint 清理失败不阻断业务目录删除（孤儿 thread 无业务入口）。
    """
    state = _state(request)
    _load_manifest(state, run_id)
    if state.registry.is_active(run_id):
        raise RunAlreadyActiveError("任务正在执行，请先停止再删除")
    with suppress(Exception):
        await state.runner.purge_checkpoint(run_id)
    state.repository.delete_run(run_id)
    state.meta_store.delete(run_id)
    state.confirmation_cache.pop(run_id, None)
    return Response(status_code=204)


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
