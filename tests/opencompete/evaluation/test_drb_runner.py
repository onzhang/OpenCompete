"""ODR 通用 DRB 任务 Runner 测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from opencompete.evaluation import drb_runner
from opencompete.evaluation.drb_runner import generate_drb_articles
from opencompete.evaluation.official_drb import DrbArticle, DrbQuery, append_jsonl
from opencompete.settings import OpenCompeteSettings


class _FakeGraph:
    def __init__(self, *, failing_id: int | None = None) -> None:
        self.failing_id = failing_id
        self.calls: list[int] = []

    async def ainvoke(
        self,
        value: dict[str, Any],
        config: RunnableConfig,
    ) -> dict[str, Any]:
        del value
        identifier = int(str(config["configurable"]["thread_id"]).removeprefix("drb-"))
        self.calls.append(identifier)
        if identifier == self.failing_id:
            raise TimeoutError("provider timeout")
        return {"final_report": f"# 任务 {identifier} 完整报告"}


def _queries() -> tuple[DrbQuery, ...]:
    return tuple(
        DrbQuery(
            id=identifier,
            topic="测试",
            language="zh" if identifier == 1 else "en",
            prompt=f"研究任务 {identifier}",
        )
        for identifier in (1, 2, 3)
    )


@pytest.mark.asyncio
async def test_runner_appends_success_keeps_failure_and_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drb_runner, "verify_official_repository", lambda _path: tmp_path)
    monkeypatch.setattr(drb_runner, "load_official_queries", lambda _path: _queries())
    articles = tmp_path / "articles.jsonl"
    metrics = tmp_path / "metrics.jsonl"
    existing = DrbArticle(id=1, prompt="研究任务 1", article="# 已有报告")
    append_jsonl(articles, existing)
    graph = _FakeGraph(failing_id=3)

    generated = await generate_drb_articles(
        repository="unused",
        articles_path=str(articles),
        metrics_path=str(metrics),
        settings=OpenCompeteSettings(),
        graph=graph,
    )

    assert graph.calls == [2, 3]
    assert [item.id for item in generated] == [1, 2]
    metric_values = [
        json.loads(line)
        for line in metrics.read_text(encoding="utf-8").splitlines()
    ]
    assert [item["status"] for item in metric_values] == ["completed", "failed"]
    assert metric_values[-1]["error_code"] == "temporary_provider_error"
    assert "provider timeout" not in json.dumps(metric_values)


@pytest.mark.asyncio
async def test_runner_rejects_existing_article_with_changed_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drb_runner, "verify_official_repository", lambda _path: tmp_path)
    monkeypatch.setattr(drb_runner, "load_official_queries", lambda _path: _queries())
    articles = tmp_path / "articles.jsonl"
    append_jsonl(
        articles,
        DrbArticle(id=1, prompt="被修改的任务", article="# 报告"),
    )

    with pytest.raises(ValueError, match="不一致"):
        await generate_drb_articles(
            repository="unused",
            articles_path=str(articles),
            metrics_path=str(tmp_path / "metrics.jsonl"),
            settings=OpenCompeteSettings(),
            graph=_FakeGraph(),
        )
