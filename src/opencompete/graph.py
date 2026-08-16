"""组合可恢复范围确认流程与上游 Open Deep Research 子图。."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from open_deep_research.deep_researcher import deep_researcher_builder
from opencompete.analysis import (
    build_draft_scope,
)
from opencompete.analysis import (
    freeze_scope as freeze_scope_spec,
)
from opencompete.budget import BudgetGuard
from opencompete.errors import DataValidationError
from opencompete.extractors import (
    ArtifactExtraction,
    ArtifactExtractor,
    StaticArtifactExtractor,
)
from opencompete.materialize import materialize_bundle
from opencompete.models import (
    BudgetSnapshot,
    CandidateSelection,
    ConfirmationKind,
    DiscoveryCandidate,
    DiscoveryRequest,
    ResearchCommission,
    ResearchEntity,
    ResearchRequest,
    RunBundle,
    RunStatus,
    ScopeConfirmation,
    ScopeSpec,
)
from opencompete.providers import DiscoveryProvider
from opencompete.scope_planning import ScopePlanner
from opencompete.security import validate_public_http_url


class OpenCompeteGraphInput(TypedDict, total=False):
    """创建任务时进入 Graph 的最小业务输入。."""

    run_id: str
    request: dict[str, Any]
    commission: dict[str, Any]


class OpenCompeteGraphState(MessagesState, total=False):
    """外层流程以及嵌入上游子图共享的 JSON 可恢复状态。."""

    run_id: str
    commission: NotRequired[dict[str, Any]]
    request: dict[str, Any]
    scope: dict[str, Any]
    status: str
    confirmation: dict[str, Any]
    confirmation_kind: str
    discovery_candidates: list[dict[str, Any]]
    candidate_selection: dict[str, Any]
    research_brief: NotRequired[str | None]
    research_contract: NotRequired[dict[str, Any]]
    supervisor_messages: NotRequired[list[Any]]
    raw_notes: NotRequired[list[str]]
    notes: NotRequired[list[str]]
    final_report: NotRequired[str]
    budget_exhausted: NotRequired[bool]
    supervisor_model_turns: NotRequired[int]
    supervisor_research_calls: NotRequired[int]
    supervisor_reflection_calls: NotRequired[int]
    supervisor_termination_reason: NotRequired[str | None]
    unexecuted_research_calls: NotRequired[int]
    bundle: dict[str, Any]
    gate_violations: list[dict[str, Any]]


def build_graph(
    checkpointer: BaseCheckpointSaver[Any] | None,
    research_graph: StateGraph[Any, Any, Any, Any]
    | CompiledStateGraph[Any, Any, Any, Any]
    | None = None,
    *,
    extractor: ArtifactExtractor | None = None,
    budget_guard: BudgetGuard | None = None,
    discovery_provider: DiscoveryProvider | None = None,
    scope_planner: ScopePlanner | None = None,
    discovery_search_results_per_query: int = 5,
) -> CompiledStateGraph[
    OpenCompeteGraphState,
    None,
    OpenCompeteGraphInput,
    OpenCompeteGraphState,
]:
    """构建不依赖 Agent Server 的本地可恢复研究 Graph。.

    ``research_graph`` 缺省时直接编译上游导出的
    ``deep_researcher_builder``，再把编译产物作为真正的嵌入子图节点。
    只给最外层 Graph 注入 Checkpointer，LangGraph 会负责子图命名空间和恢复。

    生产 Runner 应显式注入带预算回调的 ``budget_guard`` 和结构化
    ``extractor``。空依赖只为离线组合测试以及旧调用方提供零预算、空提取结果。
    """
    child_graph = _compile_research_graph(research_graph)
    artifact_extractor = extractor or StaticArtifactExtractor(ArtifactExtraction())

    async def prepare_scope(
        state: OpenCompeteGraphState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        run_id = _run_id(config)
        input_run_id = state.get("run_id", run_id)
        if input_run_id != run_id:
            raise DataValidationError("输入 run_id 与 Graph thread_id 不一致")
        raw_request = state.get("request")
        raw_commission = state.get("commission")
        if raw_request is not None and raw_commission is not None:
            raise DataValidationError("Graph 输入只能提供 request 或 commission 之一")
        if raw_commission is not None:
            if scope_planner is None:
                raise DataValidationError("Web 委托没有配置 ScopePlanner")
            commission = _research_commission(raw_commission)
            proposal = await scope_planner.plan(commission, config=config)
            request = proposal.to_research_request(commission)
            # Planner 的中文动态维度就是 Web 草案的唯一来源。不能把已规划
            # Scope 再交给 legacy ``build_draft_scope``，否则会重新混入固定键。
            scope = proposal.to_draft_scope(commission)
        elif raw_request is not None:
            request = _research_request(raw_request)
            scope = build_draft_scope(request)
        else:
            raise DataValidationError(
                "Graph 缺少有效的 ResearchRequest 或 ResearchCommission"
            )
        try:
            allowed_urls = tuple(
                validate_public_http_url(url) for url in request.allowed_urls
            )
        except ValueError as exc:
            raise DataValidationError(f"用户网址不满足公网安全边界：{exc}") from exc
        request = request.model_copy(update={"allowed_urls": allowed_urls})
        return {
            "run_id": run_id,
            "request": request.model_dump(mode="json"),
            "scope": scope.model_dump(mode="json"),
            "status": RunStatus.AWAITING_CONFIRMATION.value,
        }

    async def discover_candidates(
        state: OpenCompeteGraphState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        request = _research_request(state.get("request"))
        if not request.discover_candidates:
            return {
                "discovery_candidates": [],
                "confirmation_kind": ConfirmationKind.SCOPE_CONFIRMATION.value,
            }
        if discovery_provider is None or budget_guard is None:
            raise DataValidationError("任务要求发现候选，但没有配置发现 Provider")
        # 原始研究问题原样交给 Provider：意图理解由 Provider 内的模型负责，
        # 不在这里拼接指令式文本（关键词引擎无法理解指令）。
        candidates = await discovery_provider.discover(
            DiscoveryRequest(
                query=request.query,
                levels=request.levels,
                existing_entities=request.entities,
                search_results_per_query=discovery_search_results_per_query,
                allowed_urls=request.allowed_urls,
            ),
            budget_guard,
            config=config,
        )
        return {
            "discovery_candidates": [
                candidate.model_dump(mode="json") for candidate in candidates
            ],
            "confirmation_kind": (
                ConfirmationKind.CANDIDATE_SELECTION.value
                if candidates
                else ConfirmationKind.SCOPE_CONFIRMATION.value
            ),
        }

    def route_after_discovery(
        state: OpenCompeteGraphState,
    ) -> Literal["confirm_candidates", "confirm_scope"]:
        if state.get("discovery_candidates"):
            return "confirm_candidates"
        return "confirm_scope"

    def confirm_candidates(state: OpenCompeteGraphState) -> dict[str, Any]:
        scope = _scope_spec(state.get("scope"))
        candidates = _discovery_candidates(state.get("discovery_candidates"))
        resumed = interrupt(
            {
                "type": ConfirmationKind.CANDIDATE_SELECTION.value,
                "scope_revision": scope.revision,
                "candidates": [
                    candidate.model_dump(mode="json") for candidate in candidates
                ],
                "instruction": "请选择要加入研究范围的候选；未选择的候选视为拒绝。",
            }
        )
        selection = CandidateSelection.model_validate(resumed)
        return {"candidate_selection": selection.model_dump(mode="json")}

    def apply_candidate_selection(state: OpenCompeteGraphState) -> dict[str, Any]:
        request = _research_request(state.get("request"))
        scope = _scope_spec(state.get("scope"))
        selection = CandidateSelection.model_validate(state.get("candidate_selection"))
        if selection.expected_revision != scope.revision:
            raise DataValidationError("候选确认的范围版本已过期")
        candidates = _discovery_candidates(state.get("discovery_candidates"))
        candidates_by_id = {
            candidate.candidate_id: candidate for candidate in candidates
        }
        selected_ids = {choice.candidate_id for choice in selection.selected_candidates}
        unknown = selected_ids - set(candidates_by_id)
        if unknown:
            raise DataValidationError("候选确认包含未展示的对象")
        entities = list(request.entities)
        known_names = {
            name.casefold()
            for entity in entities
            for name in (entity.name, *entity.aliases)
        }
        for choice in selection.selected_candidates:
            discovered = candidates_by_id[choice.candidate_id].entity
            entity = ResearchEntity(
                name=choice.entity_name,
                aliases=discovered.aliases,
                description=discovered.description,
                user_provided=False,
            )
            if entity.name.casefold() not in known_names:
                entities.append(entity)
                known_names.add(entity.name.casefold())
        updated_request = request.model_copy(update={"entities": tuple(entities)})
        # 候选确认只会改变比较对象。动态 Scope 已经由 Planner 生成，
        # 绝不能在这里重新构建并覆盖用户稍后要确认的维度和排除项。
        updated_scope = scope.model_copy(update={"entities": tuple(entities)})
        return {
            "request": updated_request.model_dump(mode="json"),
            "scope": updated_scope.model_dump(mode="json"),
            "confirmation_kind": ConfirmationKind.SCOPE_CONFIRMATION.value,
            "status": RunStatus.AWAITING_CONFIRMATION.value,
        }

    def confirm_scope(state: OpenCompeteGraphState) -> dict[str, dict[str, Any]]:
        scope = _scope_spec(state.get("scope"))
        resumed = interrupt(
            {
                "type": "scope_confirmation",
                "scope": scope.model_dump(mode="json"),
                "instruction": "请确认研究对象、层级、维度和排除项。",
            }
        )
        confirmation = ScopeConfirmation.model_validate(resumed)
        return {"confirmation": confirmation.model_dump(mode="json")}

    def freeze_scope(state: OpenCompeteGraphState) -> dict[str, Any]:
        request = _research_request(state.get("request"))
        scope = _scope_spec(state.get("scope"))
        confirmation = ScopeConfirmation.model_validate(state.get("confirmation"))
        if not confirmation.accepted:
            return {"status": RunStatus.CANCELLED.value}
        frozen = freeze_scope_spec(scope, confirmation)
        frozen_request = request.model_copy(
            update={
                "entities": frozen.entities,
                "levels": frozen.levels,
                "dimensions": dict(frozen.dimensions),
                "exclusions": frozen.exclusions,
            }
        )
        return {
            "request": frozen_request.model_dump(mode="json"),
            "scope": frozen.model_dump(mode="json"),
            "status": RunStatus.SCOPE_FROZEN.value,
        }

    def route_after_confirmation(
        state: OpenCompeteGraphState,
    ) -> Literal["prepare_research_input", "__end__"]:
        if _run_status(state.get("status")) is RunStatus.CANCELLED:
            return "__end__"
        return "prepare_research_input"

    def prepare_research_input(
        state: OpenCompeteGraphState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        _validate_upstream_config(config)
        request = _research_request(state.get("request"))
        scope = _scope_spec(state.get("scope"))
        if not scope.frozen:
            raise DataValidationError("研究开始前必须冻结范围")
        from opencompete.research_contracts import (
            build_prepare_research_message_content,
            build_research_contract,
        )

        # 内部契约仅进入 state 供 Extractor/hard gates；不得进入 messages。
        contract = build_research_contract(request, scope)
        message = build_prepare_research_message_content(request, scope)
        return {
            "status": RunStatus.RESEARCHING.value,
            "research_brief": message,
            "research_contract": contract.model_dump(mode="json"),
            "messages": [HumanMessage(content=message)],
        }

    async def materialize(
        state: OpenCompeteGraphState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        run_id = _run_id(config)
        if budget_guard is not None and budget_guard.run_id != run_id:
            raise DataValidationError("BudgetGuard 的 run_id 与 Graph thread_id 不一致")
        budget = (
            await budget_guard.snapshot()
            if budget_guard is not None
            else BudgetSnapshot()
        )
        scope = _scope_spec(state.get("scope"))
        bundle = await materialize_bundle(
            run_id=run_id,
            scope=scope,
            upstream_state=state,
            budget=budget,
            extractor=artifact_extractor,
            config=config,
            budget_snapshot_provider=(
                budget_guard.snapshot if budget_guard is not None else None
            ),
        )
        return {
            "bundle": bundle.model_dump(mode="json"),
            "status": bundle.status.value,
        }

    def validate(state: OpenCompeteGraphState) -> dict[str, list[dict[str, Any]]]:
        bundle = RunBundle.model_validate(state.get("bundle"))
        violations = tuple(bundle.traceability_violations())
        if violations:
            codes = ", ".join(sorted({item.code for item in violations}))
            raise DataValidationError(f"结构化产物未通过硬门禁：{codes}")
        return {
            "gate_violations": [
                violation.model_dump(mode="json") for violation in violations
            ]
        }

    builder = StateGraph(
        OpenCompeteGraphState,
        input_schema=OpenCompeteGraphInput,
        output_schema=OpenCompeteGraphState,
    )
    builder.add_node("prepare_scope", prepare_scope)
    builder.add_node("discover_candidates", discover_candidates)
    builder.add_node("confirm_candidates", confirm_candidates)
    builder.add_node("apply_candidate_selection", apply_candidate_selection)
    builder.add_node("confirm_scope", confirm_scope)
    builder.add_node("freeze_scope", freeze_scope)
    builder.add_node("prepare_research_input", prepare_research_input)
    # 这里必须直接嵌入 CompiledStateGraph；用节点函数手工 ainvoke 会让整个
    # 上游研究流程退化成父 Graph 无法观察和恢复的大原子节点。
    builder.add_node("odr_research", child_graph)
    builder.add_node("materialize", materialize)
    builder.add_node("validate", validate)

    builder.add_edge(START, "prepare_scope")
    builder.add_edge("prepare_scope", "discover_candidates")
    builder.add_conditional_edges("discover_candidates", route_after_discovery)
    builder.add_edge("confirm_candidates", "apply_candidate_selection")
    builder.add_edge("apply_candidate_selection", "confirm_scope")
    builder.add_edge("confirm_scope", "freeze_scope")
    builder.add_conditional_edges("freeze_scope", route_after_confirmation)
    builder.add_edge("prepare_research_input", "odr_research")
    builder.add_edge("odr_research", "materialize")
    builder.add_edge("materialize", "validate")
    builder.add_edge("validate", END)
    return builder.compile(checkpointer=checkpointer, name="opencompete")


def _compile_research_graph(
    research_graph: StateGraph[Any, Any, Any, Any]
    | CompiledStateGraph[Any, Any, Any, Any]
    | None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    if research_graph is None:
        return deep_researcher_builder.compile()
    candidate = research_graph
    if isinstance(candidate, StateGraph):
        return candidate.compile()
    return candidate


def _run_id(config: RunnableConfig) -> str:
    value = config.get("configurable", {}).get("thread_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("运行 Graph 必须提供非空 configurable.thread_id")
    return value


def _validate_upstream_config(config: RunnableConfig) -> None:
    configurable = config.get("configurable", {})
    if configurable.get("allow_clarification") is not False:
        raise DataValidationError("上游研究必须关闭二次澄清")
    if configurable.get("search_api") != "tavily":
        raise DataValidationError("正式研究只允许使用 Tavily")
    if "mcp_config" not in configurable or configurable.get("mcp_config") is not None:
        raise DataValidationError("正式研究必须显式禁用 MCP")


def _research_request(value: Any) -> ResearchRequest:
    try:
        return ResearchRequest.model_validate(value)
    except Exception as exc:
        raise DataValidationError("Graph 缺少有效的 ResearchRequest") from exc


def _research_commission(value: Any) -> ResearchCommission:
    try:
        return ResearchCommission.model_validate(value)
    except Exception as exc:
        raise DataValidationError("Graph 缺少有效的 ResearchCommission") from exc


def _scope_spec(value: Any) -> ScopeSpec:
    try:
        return ScopeSpec.model_validate(value)
    except Exception as exc:
        raise DataValidationError("Graph 缺少有效的 ScopeSpec") from exc


def _run_status(value: Any) -> RunStatus:
    try:
        return value if isinstance(value, RunStatus) else RunStatus(str(value))
    except ValueError as exc:
        raise DataValidationError(f"Graph 返回了无效任务状态：{value}") from exc


def _discovery_candidates(value: Any) -> tuple[DiscoveryCandidate, ...]:
    try:
        candidates = tuple(
            DiscoveryCandidate.model_validate(candidate) for candidate in (value or ())
        )
        for candidate in candidates:
            validate_public_http_url(candidate.source_url)
        return candidates
    except Exception as exc:
        raise DataValidationError("Graph 的候选对象无法校验") from exc
