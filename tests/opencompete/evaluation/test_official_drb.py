"""官方 Deep Research Bench 薄适配层测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import opencompete.evaluation.official_drb as official_drb
from opencompete.evaluation.official_drb import (
    DrbArticle,
    DrbQuery,
    DrbTaskMetric,
    OfficialRaceScores,
    append_jsonl,
    load_articles,
    load_official_queries,
    parse_race_result,
    race_result_kind,
    run_official_race,
    summarize_drb,
)
from opencompete.models import BudgetEstimate


def _query(identifier: int) -> DrbQuery:
    return DrbQuery(
        id=identifier,
        topic="测试",
        language="zh" if identifier % 2 else "en",
        prompt=f"研究任务 {identifier}",
    )


def test_load_queries_and_articles_reject_duplicate_ids(tmp_path: Path) -> None:
    repo = tmp_path / "drb"
    query_path = repo / "data" / "prompt_data" / "query.jsonl"
    query_path.parent.mkdir(parents=True)
    query_path.write_text(
        "\n".join(
            json.dumps(_query(identifier).model_dump(mode="json"), ensure_ascii=False)
            for identifier in (1, 2)
        ),
        encoding="utf-8",
    )

    assert len(load_official_queries(repo, expected_count=2)) == 2

    articles = tmp_path / "articles.jsonl"
    article = DrbArticle(id=1, prompt="研究任务 1", article="# 报告")
    append_jsonl(articles, article)
    append_jsonl(articles, article)
    with pytest.raises(ValueError, match="重复"):
        load_articles(articles)


def test_parse_official_race_result_and_label_model(tmp_path: Path) -> None:
    result = tmp_path / "race_result.txt"
    result.write_text(
        "Comprehensiveness: 0.4110\n"
        "Insight: 0.4051\n"
        "Instruction Following: 0.4621\n"
        "Readability: 0.4172\n"
        "Overall Score: 0.4218\n",
        encoding="utf-8",
    )

    scores = parse_race_result(result)

    assert scores.overall == 0.4218
    assert race_result_kind("gpt-5.5") == "official_race"
    assert race_result_kind("provider-compatible-model") == "race_compatible_local"


def test_summary_keeps_completion_and_cost_separate_from_race() -> None:
    queries = tuple(_query(identifier) for identifier in (1, 2, 3))
    articles = (
        DrbArticle(id=1, prompt=queries[0].prompt, article="# 报告 1"),
        DrbArticle(id=2, prompt=queries[1].prompt, article="# 报告 2"),
    )
    metrics = (
        DrbTaskMetric(
            id=1,
            status="completed",
            duration_seconds=10,
            usage=BudgetEstimate(input_tokens=100, output_tokens=50, cost_usd=0.1),
        ),
        DrbTaskMetric(
            id=2,
            status="completed",
            duration_seconds=20,
            usage=BudgetEstimate(input_tokens=300, output_tokens=150, cost_usd=0.3),
        ),
        DrbTaskMetric(
            id=3,
            status="failed",
            duration_seconds=5,
            error_code="temporary_provider_error",
            error_message="模型暂时不可用",
        ),
    )
    race = OfficialRaceScores(
        comprehensiveness=0.4,
        insight=0.5,
        instruction_following=0.6,
        readability=0.7,
        overall=0.55,
    )

    summary = summarize_drb(
        queries=queries,
        articles=articles,
        metrics=metrics,
        scored_tasks=2,
        judge_model="local-judge",
        race=race,
    )

    assert summary.result_kind == "race_compatible_local"
    assert summary.generation_completion_rate == pytest.approx(2 / 3)
    assert summary.scoring_completion_rate == pytest.approx(2 / 3)
    assert summary.race is race
    assert summary.average_duration_seconds == 15
    assert summary.average_input_tokens == 200
    assert summary.average_cost_usd == pytest.approx(0.2)


def test_official_runner_filters_task_ids_and_bypasses_provider_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "drb"
    query_path = repo / "data" / "prompt_data" / "query.jsonl"
    query_path.parent.mkdir(parents=True)
    query_path.write_text(
        "".join(
            json.dumps(_query(identifier).model_dump(mode="json"), ensure_ascii=False)
            + "\n"
            for identifier in range(1, 101)
        ),
        encoding="utf-8",
    )
    (repo / "deepresearch_bench_race.py").write_text("# 测试替身\n", encoding="utf-8")
    articles = tmp_path / "articles.jsonl"
    append_jsonl(
        articles,
        DrbArticle(id=1, prompt="研究任务 1", article="# 报告"),
    )
    captured: dict[str, object] = {}

    def fake_run(command, *, cwd, check, env, **kwargs):
        captured.update(command=command, cwd=cwd, check=check, env=env, kwargs=kwargs)
        output_dir = Path(command[command.index("--output_dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "race_result.txt").write_text(
            "Overall Score: 0.5\n",
            encoding="utf-8",
        )
        return official_drb.subprocess.CompletedProcess(command, 0, stdout="完成\n")

    monkeypatch.setattr(
        official_drb,
        "verify_official_repository",
        lambda _path: repo,
    )
    monkeypatch.setattr(official_drb.subprocess, "run", fake_run)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setenv("NO_PROXY", "localhost")

    result = run_official_race(
        repository=repo,
        articles=articles,
        work_dir=tmp_path / "work",
        target_name="smoke",
        task_ids=(1, 3),
    )

    assert result.is_file()
    command = captured["command"]
    assert isinstance(command, list)
    selected_path = Path(command[command.index("--query_file") + 1])
    selected = [json.loads(line) for line in selected_path.read_text(encoding="utf-8").splitlines()]
    assert [item["id"] for item in selected] == [1, 3]
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["PYTHONUTF8"] == "1"
    assert "provider.example" in environment["NO_PROXY"]
