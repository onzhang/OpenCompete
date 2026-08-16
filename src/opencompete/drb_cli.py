"""官方 RACE 生成、评分与量化摘要命令。."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from open_deep_research.deep_researcher import deep_researcher
from opencompete.evaluation.drb_runner import ResearchGraph, generate_drb_articles
from opencompete.evaluation.official_drb import (
    DrbQuantitativeSummary,
    DrbTaskMetric,
    load_articles,
    load_official_queries,
    parse_race_result,
    run_official_race,
    summarize_drb,
    verify_official_repository,
    write_summary,
)
from opencompete.security import sanitized_error
from opencompete.settings import load_settings

console = Console(highlight=False)
error_console = Console(stderr=True, highlight=False)


def build_parser() -> argparse.ArgumentParser:
    """构建独立于产品 CLI 的官方 RACE 命令。."""
    parser = argparse.ArgumentParser(prog="opencompete-race")
    parser.add_argument("--config", type=Path, help="OpenCompete 非秘密配置文件")
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify", help="验证官方仓库固定提交和 100 个任务")
    verify.add_argument("repository", type=Path)

    generate = commands.add_parser("generate", help="使用 ODR 通用入口生成官方 JSONL")
    generate.add_argument("repository", type=Path)
    generate.add_argument("--articles", type=Path, required=True)
    generate.add_argument("--metrics", type=Path, required=True)
    generate.add_argument("--id", dest="task_ids", type=int, action="append", default=[])
    generate.add_argument("--limit", type=int)

    score = commands.add_parser("score", help="调用固定官方 evaluator 并量化汇总")
    score.add_argument("repository", type=Path)
    score.add_argument("--articles", type=Path, required=True)
    score.add_argument("--metrics", type=Path, required=True)
    score.add_argument("--work-dir", type=Path, required=True)
    score.add_argument("--target-name", required=True)
    score.add_argument("--judge-model", required=True)
    score.add_argument("--summary", type=Path, required=True)
    score.add_argument("--limit", type=int)
    score.add_argument("--id", dest="task_ids", type=int, action="append", default=[])
    score.add_argument("--max-workers", type=int, default=1)
    return parser


async def _generate(args: argparse.Namespace) -> None:
    settings = load_settings(config_path=args.config)

    def progress(current: int, total: int, message: str) -> None:
        console.print(f"[{current}/{total}] {message}")

    await generate_drb_articles(
        repository=os.fspath(args.repository),
        articles_path=os.fspath(args.articles),
        metrics_path=os.fspath(args.metrics),
        settings=settings,
        task_ids=tuple(args.task_ids),
        limit=args.limit,
        graph=cast(ResearchGraph, deep_researcher),
        progress=progress,
    )


def _score(args: argparse.Namespace) -> None:
    load_settings(config_path=args.config)
    repo = verify_official_repository(args.repository)
    queries = load_official_queries(repo)
    if args.task_ids:
        selected = set(args.task_ids)
        queries = tuple(query for query in queries if query.id in selected)
    articles = load_articles(args.articles)
    result_path = run_official_race(
        repository=repo,
        articles=args.articles,
        work_dir=args.work_dir,
        target_name=args.target_name,
        limit=args.limit,
        task_ids=tuple(args.task_ids),
        max_workers=args.max_workers,
    )
    official_results = _read_jsonl(Path(args.work_dir) / "results" / "raw_results.jsonl")
    scored_tasks = sum(
        isinstance(item, dict) and "error" not in item for item in official_results
    )
    metrics = tuple(
        DrbTaskMetric.model_validate(item) for item in _read_jsonl(args.metrics)
    )
    summary = summarize_drb(
        queries=queries,
        articles=articles,
        metrics=metrics,
        scored_tasks=scored_tasks,
        judge_model=args.judge_model,
        race=parse_race_result(result_path),
    )
    write_summary(args.summary, summary)
    _display_summary(summary)


def _display_summary(summary: DrbQuantitativeSummary) -> None:
    table = Table(title="RACE 量化结果")
    table.add_column("指标")
    table.add_column("结果", justify="right")
    table.add_row("结果类型", summary.result_kind)
    table.add_row("报告完成率", f"{summary.generation_completion_rate:.2%}")
    table.add_row("评分完成率", f"{summary.scoring_completion_rate:.2%}")
    if summary.race is not None:
        for key, value in summary.race.model_dump().items():
            table.add_row(f"RACE {key}", f"{value:.4f}")
    console.print(table)


def _read_jsonl(path: str | Path) -> tuple[object, ...]:
    values: list[object] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                values.append(json.loads(line))
    return tuple(values)


def main(argv: Sequence[str] | None = None) -> int:
    """执行官方 RACE 命令并只展示脱敏错误。."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "verify":
            repo = verify_official_repository(args.repository)
            queries = load_official_queries(repo)
            console.print(f"官方 DRB 提交与 {len(queries)} 个任务验证通过。")
        elif args.command == "generate":
            asyncio.run(_generate(args))
        elif args.command == "score":
            _score(args)
        else:
            raise ValueError(f"不支持的命令：{args.command}")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, ValidationError) as exc:
        error = sanitized_error(exc)
        error_console.print(f"错误：{error.message}（{error.code}）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
