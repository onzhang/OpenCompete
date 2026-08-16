import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from tavily.errors import InvalidAPIKeyError
from tavily.errors import TimeoutError as TavilyTimeoutError

from opencompete.budget import BudgetGuard, InMemoryBudgetLedger
from opencompete.errors import (
    AuthenticationError,
    BudgetExceededError,
    TemporaryProviderError,
)
from opencompete.models import BudgetEstimate, DiscoveryRequest, ResearchEntity
from opencompete.providers import (
    LlmDiscoveryProvider,
    TavilyDiscoveryProvider,
    _binding_batches,
    _BindingDraft,
    _BoundCandidate,
    _CoverageAssessment,
    _IntentDraft,
    _NamedCandidate,
)
from opencompete.settings import OpenCompeteSettings


class FakeTavilyClient:
    def __init__(
        self,
        response: Mapping[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or {"results": []}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class PlannedTavilyClient(FakeTavilyClient):
    """按查询返回不同结果，验证发现阶段可聚合多个检索空间。."""

    def __init__(self, responses: Mapping[str, Mapping[str, Any]]) -> None:
        super().__init__()
        self.responses = responses

    async def search(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        return self.responses[str(kwargs["query"])]


class BlockingTavilyClient(FakeTavilyClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def search(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        self.started.set()
        await self.release.wait()
        return self.response


def make_budget(
    calls: int = 5,
    *,
    events: list[dict[str, object]] | None = None,
) -> BudgetGuard:
    return BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=calls),
        run_id="run-1",
        execution_id="execution-1",
        event_sink=events.append if events is not None else None,
    )


@pytest.mark.asyncio
async def test_discovery_returns_candidates_without_mutating_existing_scope() -> None:
    existing = ResearchEntity(name="Existing", user_provided=True)
    request = DiscoveryRequest(
        query="enterprise collaboration competitors",
        existing_entities=(existing,),
        search_results_per_query=3,
    )
    client = FakeTavilyClient(
        {
            "results": [
                {
                    "title": "Candidate One",
                    "url": "https://example.com/candidate-one",
                    "content": "Candidate description",
                },
                {
                    "title": "Existing",
                    "url": "https://example.com/existing",
                    "content": "Duplicate of an existing entity",
                },
                {
                    "title": "Unsafe",
                    "url": "http://127.0.0.1/private",
                    "content": "Must not become a candidate",
                },
            ]
        }
    )
    provider = TavilyDiscoveryProvider(client=client)

    candidates = await provider.discover(request, make_budget())

    assert [candidate.entity.name for candidate in candidates] == ["Candidate One"]
    assert candidates[0].entity.description == "Candidate description"
    assert candidates[0].entity.user_provided is False
    assert candidates[0].source_url == "https://example.com/candidate-one"
    assert candidates[0].source_title == "Candidate One"
    assert request.existing_entities == (existing,)
    assert client.calls[0]["query"] == request.query
    assert client.calls[0]["max_results"] == 3


@pytest.mark.asyncio
async def test_discovery_validates_allowed_urls_before_spending_budget() -> None:
    client = FakeTavilyClient()
    provider = TavilyDiscoveryProvider(client=client)
    budget = make_budget()
    request = DiscoveryRequest(
        query="competitors",
        allowed_urls=("http://localhost/private",),
    )

    with pytest.raises(ValueError):
        await provider.discover(request, budget)

    assert client.calls == []
    assert (await budget.snapshot()).spent.tavily_calls == 0
    assert (await budget.snapshot()).reserved.tavily_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidAPIKeyError("invalid"), AuthenticationError),
        (TavilyTimeoutError(1), TemporaryProviderError),
    ],
)
async def test_provider_distinguishes_authentication_and_temporary_errors(
    error: Exception,
    expected: type[Exception],
) -> None:
    provider = TavilyDiscoveryProvider(client=FakeTavilyClient(error=error))

    with pytest.raises(expected):
        await provider.discover(DiscoveryRequest(query="competitors"), make_budget())


@pytest.mark.asyncio
async def test_provider_emits_redacted_attempt_events_for_success_and_failure() -> None:
    success_events: list[dict[str, object]] = []
    successful = TavilyDiscoveryProvider(client=FakeTavilyClient())
    await successful.discover(
        DiscoveryRequest(query="private success query"),
        make_budget(events=success_events),
    )

    failure_events: list[dict[str, object]] = []
    secret = "provider-secret-body"
    failed = TavilyDiscoveryProvider(
        client=FakeTavilyClient(error=RuntimeError(secret))
    )
    with pytest.raises(Exception):
        await failed.discover(
            DiscoveryRequest(query="private failure query"),
            make_budget(events=failure_events),
        )

    assert [event["event"] for event in success_events] == [
        "call_attempt_started",
        "call_attempt_succeeded",
    ]
    assert success_events[0]["node"] == "discovery"
    assert success_events[0]["provider_kind"] == "tavily"
    assert len(str(success_events[0]["unit_id"])) == 64
    assert "private success query" not in repr(success_events)
    assert [event["event"] for event in failure_events] == [
        "call_attempt_started",
        "call_attempt_failed",
    ]
    assert failure_events[-1]["error_code"] == "provider_error"
    assert secret not in repr(failure_events)


@pytest.mark.asyncio
async def test_provider_refuses_request_when_budget_is_exhausted() -> None:
    client = FakeTavilyClient()
    provider = TavilyDiscoveryProvider(client=client)

    with pytest.raises(Exception) as raised:
        await provider.discover(
            DiscoveryRequest(query="competitors"),
            make_budget(calls=0),
        )

    assert raised.type.__name__ == "BudgetExceededError"
    assert client.calls == []


@pytest.mark.asyncio
async def test_provider_cost_limit_rejects_before_search_request() -> None:
    client = FakeTavilyClient()
    provider = TavilyDiscoveryProvider(
        client=client,
        tavily_cost_per_call_usd=0.02,
    )
    budget = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=1, cost_usd=0.019),
        run_id="run-1",
    )

    with pytest.raises(BudgetExceededError):
        await provider.discover(DiscoveryRequest(query="competitors"), budget)

    assert client.calls == []


@pytest.mark.asyncio
async def test_provider_cost_reservation_prevents_concurrent_overspend() -> None:
    client = BlockingTavilyClient()
    provider = TavilyDiscoveryProvider(
        client=client,
        tavily_cost_per_call_usd=0.02,
    )
    budget = BudgetGuard(
        InMemoryBudgetLedger(),
        BudgetEstimate(tavily_calls=2, cost_usd=0.02),
        run_id="run-1",
    )

    first = asyncio.create_task(
        provider.discover(DiscoveryRequest(query="first"), budget)
    )
    await client.started.wait()
    second = asyncio.create_task(
        provider.discover(DiscoveryRequest(query="second"), budget)
    )
    second_result = await asyncio.gather(second, return_exceptions=True)

    assert isinstance(second_result[0], BudgetExceededError)
    assert len(client.calls) == 1

    client.release.set()
    await first
    snapshot = await budget.snapshot()
    assert snapshot.spent.cost_usd == pytest.approx(0.02)


def test_provider_classifies_missing_api_key_before_any_request(monkeypatch: Any) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with pytest.raises(AuthenticationError):
        TavilyDiscoveryProvider()


class _StubModel:
    """按顺序返回预设结构化输出的模型替身。."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        self.calls.append({"messages": messages, "config": config})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _SequenceStubModel:
    """按调用顺序返回结构化结果，模拟覆盖审计提出新检索词。."""

    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        self.calls.append({"messages": messages, "config": config})
        result = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


class _BudgetAfterFirstBindingModel:
    """第一批返回候选，后续批次模拟预算拒绝。."""

    def __init__(self, first_result: Any) -> None:
        self.first_result = first_result
        self.calls = 0

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        del messages, config
        self.calls += 1
        if self.calls == 1:
            return self.first_result
        raise BudgetExceededError("预算耗尽")


def _llm_provider(
    *,
    intent: Any,
    binding: Any,
    tavily: FakeTavilyClient,
    coverage: Any | None = None,
) -> LlmDiscoveryProvider:
    return LlmDiscoveryProvider(
        OpenCompeteSettings(tavily_cost_per_call_usd=0.0),
        intent_model=_StubModel(intent),
        binding_model=_StubModel(binding),
        coverage_model=_StubModel(
            coverage
            if coverage is not None
            else _CoverageAssessment(saturated=False)
        ),
        tavily_client=tavily,
    )


def _search_results() -> Mapping[str, Any]:
    return {
        "results": [
            {
                "title": "Trae vs Cursor vs Windsurf 对比评测",
                "url": "https://example.com/compare",
                "content": "Cursor 与 Windsurf 是 Trae 最直接的替代品……",
            },
            {
                "title": "内网私有页",
                "url": "http://127.0.0.1/private",
                "content": "不安全来源必须被过滤",
            },
        ]
    }


@pytest.mark.asyncio
async def test_llm_discovery_binds_candidates_to_search_results() -> None:
    intent = _IntentDraft(
        intent_summary="Trae 是字节跳动的 AI IDE",
        search_queries=("Trae vs Cursor Windsurf alternatives",),
        candidates=(
            _NamedCandidate(name="Cursor", description="AI 原生代码编辑器"),
        ),
    )
    binding = _BindingDraft(
        candidates=(
            _BoundCandidate(
                name="Cursor",
                description="AI 原生代码编辑器",
                source_url="https://example.com/compare",
                summary="对比评测将 Cursor 列为 Trae 直接替代品",
            ),
            _BoundCandidate(
                name="Windsurf",
                source_url="https://example.com/compare",
                summary="同一评测中的另一替代品",
            ),
        )
    )
    tavily = FakeTavilyClient(_search_results())
    provider = _llm_provider(intent=intent, binding=binding, tavily=tavily)
    request = DiscoveryRequest(
        query="调研trae",
        existing_entities=(ResearchEntity(name="trae"),),
        search_results_per_query=5,
    )

    candidates = await provider.discover(request, make_budget())

    assert [c.entity.name for c in candidates] == ["Cursor", "Windsurf"]
    assert all(c.source_url == "https://example.com/compare" for c in candidates)
    assert all(c.entity.user_provided is False for c in candidates)
    assert candidates[0].source_title == "Trae vs Cursor vs Windsurf 对比评测"
    # 搜索词来自意图识别，而不是原始研究问题
    assert tavily.calls[0]["query"] == "Trae vs Cursor Windsurf alternatives"


@pytest.mark.asyncio
async def test_llm_discovery_drops_hallucinated_sources_and_duplicates() -> None:
    intent = _IntentDraft(search_queries=("q",))
    binding = _BindingDraft(
        candidates=(
            _BoundCandidate(
                name="Fabricated",
                source_url="https://not-in-results.example.com/",
            ),
            _BoundCandidate(
                name="trae",
                source_url="https://example.com/compare",
            ),
            _BoundCandidate(
                name="Cursor",
                source_url="https://example.com/compare",
            ),
        )
    )
    provider = _llm_provider(
        intent=intent,
        binding=binding,
        tavily=FakeTavilyClient(_search_results()),
    )
    request = DiscoveryRequest(
        query="调研trae",
        existing_entities=(ResearchEntity(name="Trae"),),
    )

    candidates = await provider.discover(request, make_budget())

    # 幻觉来源被丢弃；与已有对象重名（大小写不敏感）被剔除
    assert [c.entity.name for c in candidates] == ["Cursor"]


@pytest.mark.asyncio
async def test_llm_discovery_returns_empty_without_verifiable_results() -> None:
    intent = _IntentDraft(
        search_queries=("q",),
        candidates=(_NamedCandidate(name="Cursor"),),
    )
    binding = _BindingDraft(
        candidates=(
            _BoundCandidate(name="Cursor", source_url="https://example.com/x"),
        )
    )
    provider = _llm_provider(
        intent=intent,
        binding=binding,
        tavily=FakeTavilyClient({"results": []}),
    )

    candidates = await provider.discover(
        DiscoveryRequest(query="调研trae"), make_budget()
    )

    assert candidates == []


@pytest.mark.asyncio
async def test_llm_discovery_still_accounts_tavily_budget() -> None:
    events: list[dict[str, object]] = []
    provider = _llm_provider(
        intent=_IntentDraft(search_queries=("q",)),
        binding=_BindingDraft(),
        tavily=FakeTavilyClient(_search_results()),
    )

    await provider.discover(
        DiscoveryRequest(query="调研trae"), make_budget(events=events)
    )

    assert [event["event"] for event in events] == [
        "call_attempt_started",
        "call_attempt_succeeded",
    ]
    assert events[0]["node"] == "discovery"
    assert events[0]["provider_kind"] == "tavily"


@pytest.mark.asyncio
async def test_llm_discovery_aggregates_query_plan_without_candidate_cap() -> None:
    """单次搜索页大小不能限制来自集合页或多查询的候选数量。."""
    intent = _IntentDraft(
        search_queries=(
            "Trae direct AI IDE competitors",
            "Trae coding agent alternatives",
        )
    )
    binding = _BindingDraft(
        candidates=(
            _BoundCandidate(
                name="Cursor",
                category="直接竞品",
                source_url="https://example.com/direct",
                source_excerpt="Cursor is a Trae alternative.",
            ),
            _BoundCandidate(
                name="GitHub Copilot",
                category="直接竞品",
                source_url="https://example.com/direct",
            ),
            _BoundCandidate(
                name="Windsurf",
                category="直接竞品",
                source_url="https://example.com/agents",
            ),
            _BoundCandidate(
                name="Devin",
                category="相邻替代路径",
                source_url="https://example.com/agents",
            ),
        )
    )
    tavily = PlannedTavilyClient(
        {
            "Trae direct AI IDE competitors": {
                "results": [
                    {
                        "title": "Trae alternatives",
                        "url": "https://example.com/direct",
                        "raw_content": "完整集合页正文：Cursor、GitHub Copilot 等。",
                    }
                ]
            },
            "Trae coding agent alternatives": {
                "results": [
                    {
                        "title": "Trae agent alternatives",
                        "url": "https://example.com/agents",
                        "raw_content": "完整集合页正文：Windsurf、Devin 等。",
                    }
                ]
            },
        }
    )
    provider = _llm_provider(intent=intent, binding=binding, tavily=tavily)

    candidates = await provider.discover(
        DiscoveryRequest(query="调研 Trae", search_results_per_query=1),
        make_budget(calls=2),
    )

    assert [item.entity.name for item in candidates] == [
        "Cursor",
        "GitHub Copilot",
        "Windsurf",
        "Devin",
    ]
    assert candidates[0].category == "直接竞品"
    assert candidates[0].source_excerpt == "Cursor is a Trae alternative."
    assert [call["query"] for call in tavily.calls] == [
        "Trae direct AI IDE competitors",
        "Trae coding agent alternatives",
    ]
    assert all(call["max_results"] == 1 for call in tavily.calls)
    assert all(call["include_raw_content"] == "markdown" for call in tavily.calls)


@pytest.mark.asyncio
async def test_llm_discovery_stops_on_semantic_coverage_not_candidate_count() -> None:
    intent = _IntentDraft(search_queries=("first", "later"))
    binding = _BindingDraft(
        candidates=(
            _BoundCandidate(
                name="Cursor",
                source_url="https://example.com/first",
            ),
        )
    )
    tavily = PlannedTavilyClient(
        {
            "first": {
                "results": [
                    {
                        "title": "完整候选集合页",
                        "url": "https://example.com/first",
                        "raw_content": "已列出不同类型的可核验竞品。",
                    }
                ]
            },
            "later": {
                "results": [
                    {
                        "title": "不应执行的后续搜索",
                        "url": "https://example.com/later",
                        "raw_content": "后续来源。",
                    }
                ]
            },
        }
    )
    provider = _llm_provider(
        intent=intent,
        binding=binding,
        coverage=_CoverageAssessment(
            saturated=True,
            rationale="当前集合页已覆盖直接竞争和替代路径。",
        ),
        tavily=tavily,
    )

    candidates = await provider.discover(
        DiscoveryRequest(query="调研 Trae"), make_budget(calls=2)
    )

    assert [candidate.entity.name for candidate in candidates] == ["Cursor"]
    assert [call["query"] for call in tavily.calls] == ["first"]


@pytest.mark.asyncio
async def test_llm_discovery_expands_query_frontier_from_coverage_gaps() -> None:
    intent = _IntentDraft(search_queries=("initial",))
    binding = _SequenceStubModel(
        [
            _BindingDraft(
                candidates=(
                    _BoundCandidate(
                        name="Cursor",
                        source_url="https://example.com/initial",
                    ),
                )
            ),
            _BindingDraft(
                candidates=(
                    _BoundCandidate(
                        name="Windsurf",
                        source_url="https://example.com/follow-up",
                    ),
                )
            ),
        ]
    )
    coverage = _SequenceStubModel(
        [
            _CoverageAssessment(
                saturated=False,
                rationale="终端代理替代路径尚未覆盖。",
                follow_up_queries=("follow-up", "initial"),
            ),
            _CoverageAssessment(saturated=True, rationale="两个竞争路径均已覆盖。"),
        ]
    )
    tavily = PlannedTavilyClient(
        {
            "initial": {
                "results": [
                    {
                        "title": "初始集合页",
                        "url": "https://example.com/initial",
                        "raw_content": "Cursor",
                    }
                ]
            },
            "follow-up": {
                "results": [
                    {
                        "title": "替代路径集合页",
                        "url": "https://example.com/follow-up",
                        "raw_content": "Windsurf",
                    }
                ]
            },
        }
    )
    provider = LlmDiscoveryProvider(
        OpenCompeteSettings(tavily_cost_per_call_usd=0.0),
        intent_model=_StubModel(intent),
        binding_model=binding,
        coverage_model=coverage,
        tavily_client=tavily,
    )

    candidates = await provider.discover(
        DiscoveryRequest(query="调研 Trae", search_results_per_query=1),
        make_budget(calls=2),
    )

    assert [candidate.entity.name for candidate in candidates] == [
        "Cursor",
        "Windsurf",
    ]
    # 覆盖模型重复返回 initial，不应导致重复搜索；新增 follow-up 才进入前沿。
    assert [call["query"] for call in tavily.calls] == ["initial", "follow-up"]
    assert len(coverage.calls) == 2


@pytest.mark.asyncio
async def test_llm_discovery_recovers_from_an_empty_initial_search_result() -> None:
    binding = _StubModel(
        _BindingDraft(
            candidates=(
                _BoundCandidate(
                    name="Cursor",
                    source_url="https://example.com/recovered",
                ),
            )
        )
    )
    coverage = _SequenceStubModel(
        [
            _CoverageAssessment(
                saturated=False,
                rationale="初始词没有找到公开来源，需改用产品名检索。",
                follow_up_queries=("recovered",),
            ),
            _CoverageAssessment(saturated=True, rationale="已取得可核验候选。"),
        ]
    )
    tavily = PlannedTavilyClient(
        {
            "empty": {"results": []},
            "recovered": {
                "results": [
                    {
                        "title": "恢复后的对比页",
                        "url": "https://example.com/recovered",
                        "raw_content": "Cursor 是 Trae 的替代产品。",
                    }
                ]
            },
        }
    )
    provider = LlmDiscoveryProvider(
        OpenCompeteSettings(tavily_cost_per_call_usd=0.0),
        intent_model=_StubModel(_IntentDraft(search_queries=("empty",))),
        binding_model=binding,
        coverage_model=coverage,
        tavily_client=tavily,
    )

    candidates = await provider.discover(
        DiscoveryRequest(query="调研 Trae", search_results_per_query=1),
        make_budget(calls=2),
    )

    assert [candidate.entity.name for candidate in candidates] == ["Cursor"]
    assert [call["query"] for call in tavily.calls] == ["empty", "recovered"]
    assert len(coverage.calls) == 2


@pytest.mark.asyncio
async def test_llm_discovery_keeps_verified_pool_when_later_query_hits_budget() -> None:
    intent = _IntentDraft(search_queries=("first", "second", "third"))
    binding = _BindingDraft(
        candidates=(
            _BoundCandidate(name="Cursor", source_url="https://example.com/first"),
            _BoundCandidate(
                name="Windsurf", source_url="https://example.com/second"
            ),
        )
    )
    tavily = PlannedTavilyClient(
        {
            query: {
                "results": [
                    {
                        "title": query,
                        "url": f"https://example.com/{query}",
                        "content": query,
                    }
                ]
            }
            for query in ("first", "second", "third")
        }
    )
    provider = _llm_provider(intent=intent, binding=binding, tavily=tavily)

    candidates = await provider.discover(
        DiscoveryRequest(query="调研 Trae", search_results_per_query=1),
        make_budget(calls=2),
    )

    assert [item.entity.name for item in candidates] == ["Cursor", "Windsurf"]
    assert [call["query"] for call in tavily.calls] == ["first", "second"]


@pytest.mark.asyncio
async def test_llm_discovery_keeps_completed_binding_batches_when_budget_runs_out() -> None:
    tavily = FakeTavilyClient(
        {
            "results": [
                {
                    "title": "候选集合页",
                    "url": "https://example.com/collection",
                    "raw_content": "A" * 2500,
                }
            ]
        }
    )
    binding_model = _BudgetAfterFirstBindingModel(
        _BindingDraft(
            candidates=(
                _BoundCandidate(
                    name="Cursor",
                    source_url="https://example.com/collection",
                ),
            )
        )
    )
    provider = LlmDiscoveryProvider(
        OpenCompeteSettings(
            tavily_cost_per_call_usd=0.0,
            discovery_binding_max_input_chars=1000,
            max_concurrent_summarizations=1,
        ),
        intent_model=_StubModel(_IntentDraft(search_queries=("q",))),
        binding_model=binding_model,
        coverage_model=_StubModel(_CoverageAssessment(saturated=False)),
        tavily_client=tavily,
    )

    candidates = await provider.discover(
        DiscoveryRequest(query="调研 Trae"), make_budget()
    )

    assert [candidate.entity.name for candidate in candidates] == ["Cursor"]
    assert binding_model.calls == 3


def test_binding_batches_preserve_every_source_chunk_without_candidate_cap() -> None:
    first = "A" * 2300
    second = "B" * 1700
    batches = _binding_batches(
        [
            {"title": "集合页", "url": "https://example.com/a", "content": first},
            {"title": "评测页", "url": "https://example.com/b", "content": second},
        ],
        max_input_chars=1000,
    )

    reconstructed: dict[str, str] = {}
    for batch in batches:
        assert sum(len(item["content"]) for item in batch) <= 1000
        for item in batch:
            reconstructed[item["url"]] = reconstructed.get(item["url"], "") + item[
                "content"
            ]

    assert reconstructed == {
        "https://example.com/a": first,
        "https://example.com/b": second,
    }


@pytest.mark.asyncio
async def test_llm_discovery_intent_failure_prevents_search_spend() -> None:
    tavily = FakeTavilyClient(_search_results())
    provider = LlmDiscoveryProvider(
        OpenCompeteSettings(tavily_cost_per_call_usd=0.0),
        intent_model=_StubModel(RuntimeError("model unavailable")),
        binding_model=_StubModel(_BindingDraft()),
        tavily_client=tavily,
    )
    budget = make_budget()

    with pytest.raises(RuntimeError):
        await provider.discover(DiscoveryRequest(query="调研trae"), budget)

    assert tavily.calls == []
    assert (await budget.snapshot()).spent.tavily_calls == 0
