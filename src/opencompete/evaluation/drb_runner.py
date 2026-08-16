"""不依赖 Agent Server 的官方 DRB 通用研究任务 Runner。."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from time import perf_counter
from typing import Any, Protocol, cast

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from opencompete.budget import BudgetGuard, InMemoryBudgetLedger
from opencompete.callbacks import BudgetCallback
from opencompete.evaluation.official_drb import (
    DrbArticle,
    DrbQuery,
    DrbTaskMetric,
    append_jsonl,
    load_articles,
    load_official_queries,
    verify_official_repository,
)
from opencompete.models import BudgetEstimate
from opencompete.security import (
    environment_secret_values,
    redact_text,
    sanitized_error,
)
from opencompete.settings import OpenCompeteSettings

ProgressSink = Callable[[int, int, str], object]


class ResearchGraph(Protocol):
    """DRB Runner 使用的最小上游 Graph 契约。."""

    async def ainvoke(
        self,
        value: Mapping[str, Any],
        config: RunnableConfig,
    ) -> Mapping[str, Any]:
        """运行一个独立研究任务并返回最终状态。."""
        ...


async def generate_drb_articles(
    *,
    repository: str,
    articles_path: str,
    metrics_path: str,
    settings: OpenCompeteSettings,
    task_ids: Sequence[int] = (),
    limit: int | None = None,
    graph: ResearchGraph,
    progress: ProgressSink | None = None,
) -> tuple[DrbArticle, ...]:
    """逐题生成官方 JSONL；成功记录存在时安全跳过。."""
    repo = verify_official_repository(repository)
    queries = load_official_queries(repo)
    selected = _select_queries(queries, task_ids=task_ids, limit=limit)
    existing = {
        article.id: article
        for article in (
            load_articles(articles_path) if _is_file(articles_path) else ()
        )
    }
    query_by_id = {query.id: query for query in queries}
    for article in existing.values():
        query = query_by_id.get(article.id)
        if query is None or article.prompt != query.prompt:
            raise ValueError("已有 Article 与固定官方 Query 不一致")

    total = len(selected)
    for index, query in enumerate(selected, start=1):
        if query.id in existing:
            _notify(progress, index, total, f"跳过已完成任务 {query.id}")
            continue
        _notify(progress, index, total, f"运行任务 {query.id}")
        metric, generated_article = await _run_query(
            query,
            settings=settings,
            graph=graph,
        )
        append_jsonl(metrics_path, metric)
        if generated_article is None:
            _notify(progress, index, total, f"任务 {query.id} 失败")
            continue
        append_jsonl(articles_path, generated_article)
        existing[generated_article.id] = generated_article
        _notify(progress, index, total, f"任务 {query.id} 完成")
    return tuple(existing[identifier] for identifier in sorted(existing))


async def _run_query(
    query: DrbQuery,
    *,
    settings: OpenCompeteSettings,
    graph: ResearchGraph,
) -> tuple[DrbTaskMetric, DrbArticle | None]:
    run_id = f"drb-{query.id}"
    limit = BudgetEstimate(
        input_tokens=settings.max_input_tokens,
        output_tokens=settings.max_output_tokens,
        tavily_calls=settings.max_tavily_calls,
        cost_usd=settings.max_cost_usd,
    )
    budget = BudgetGuard(InMemoryBudgetLedger(), limit, run_id)
    callback = BudgetCallback(
        budget,
        default_max_output_tokens=max(
            settings.research_model_max_tokens,
            settings.summarization_model_max_tokens,
            settings.compression_model_max_tokens,
            settings.final_report_model_max_tokens,
        ),
        llm_input_cost_per_million_usd=settings.llm_input_cost_per_million_usd,
        llm_output_cost_per_million_usd=settings.llm_output_cost_per_million_usd,
        tavily_cost_per_call_usd=settings.tavily_cost_per_call_usd,
    )
    config = cast(RunnableConfig, settings.to_runnable_config(run_id))
    config["callbacks"] = [callback]
    started = perf_counter()
    try:
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content=query.prompt)]},
            config,
        )
        report = str(state.get("final_report") or "").strip()
        if not report:
            raise ValueError("上游研究任务没有生成最终报告")
        if redact_text(report, environment_secret_values()) != report:
            raise ValueError("最终报告疑似包含环境秘密，拒绝写入 DRB JSONL")
    except Exception as exc:
        error = sanitized_error(exc)
        snapshot = await budget.snapshot()
        return (
            DrbTaskMetric(
                id=query.id,
                status="failed",
                duration_seconds=perf_counter() - started,
                usage=snapshot.spent + snapshot.reserved,
                error_code=error.code,
                error_message=error.message,
            ),
            None,
        )
    snapshot = await budget.snapshot()
    return (
        DrbTaskMetric(
            id=query.id,
            status="completed",
            duration_seconds=perf_counter() - started,
            usage=snapshot.spent + snapshot.reserved,
        ),
        DrbArticle(id=query.id, prompt=query.prompt, article=report),
    )


def _select_queries(
    queries: Sequence[DrbQuery],
    *,
    task_ids: Sequence[int],
    limit: int | None,
) -> tuple[DrbQuery, ...]:
    if limit is not None and limit < 1:
        raise ValueError("limit 必须大于零")
    requested = set(task_ids)
    available = {query.id for query in queries}
    if requested - available:
        raise ValueError("请求包含官方数据集之外的任务 ID")
    selected = tuple(query for query in queries if not requested or query.id in requested)
    return selected[:limit] if limit is not None else selected


def _is_file(path: str) -> bool:
    from pathlib import Path

    return Path(path).is_file()


def _notify(
    sink: ProgressSink | None,
    current: int,
    total: int,
    message: str,
) -> None:
    if sink is None:
        return
    try:
        sink(current, total, message)
    except Exception:
        return


__all__ = ["ResearchGraph", "generate_drb_articles"]
