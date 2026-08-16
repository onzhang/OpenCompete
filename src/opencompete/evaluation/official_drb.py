"""固定官方 Deep Research Bench 的输入、执行和量化结果边界。."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from opencompete.models import BudgetEstimate, FrozenModel

OFFICIAL_DRB_REPOSITORY = "https://github.com/Ayanami0730/deep_research_bench.git"
OFFICIAL_DRB_COMMIT = "469cce54ea7f6a63c163d3d9fec879cf289ec484"
OFFICIAL_RACE_JUDGE_MODELS = frozenset({"gpt-5.5", "openai/gpt-5.5"})
_TARGET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_RACE_LABELS = {
    "Comprehensiveness": "comprehensiveness",
    "Insight": "insight",
    "Instruction Following": "instruction_following",
    "Readability": "readability",
    "Overall Score": "overall",
}


class DrbQuery(FrozenModel):
    """官方 Query JSONL 的最小稳定字段。."""

    id: int = Field(ge=1)
    topic: str = Field(min_length=1)
    language: Literal["zh", "en"]
    prompt: str = Field(min_length=1)


class DrbArticle(FrozenModel):
    """官方 evaluator 要求的三字段报告格式。."""

    id: int = Field(ge=1)
    prompt: str = Field(min_length=1)
    article: str = Field(min_length=1)


class DrbTaskMetric(FrozenModel):
    """不进入官方报告 JSONL 的单任务运行侧车指标。."""

    id: int = Field(ge=1)
    status: Literal["completed", "failed"]
    duration_seconds: float = Field(ge=0.0)
    usage: BudgetEstimate = Field(default_factory=BudgetEstimate)
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_error(self) -> Self:
        """失败必须有安全原因，成功不能携带错误。."""
        if self.status == "failed" and (not self.error_code or not self.error_message):
            raise ValueError("失败的 DRB 任务必须记录安全错误分类")
        if self.status == "completed" and (self.error_code or self.error_message):
            raise ValueError("成功的 DRB 任务不能携带错误")
        return self


class OfficialRaceScores(FrozenModel):
    """官方 race_result.txt 中的五项批次均分。."""

    comprehensiveness: float = Field(ge=0.0, le=1.0)
    insight: float = Field(ge=0.0, le=1.0)
    instruction_following: float = Field(ge=0.0, le=1.0)
    readability: float = Field(ge=0.0, le=1.0)
    overall: float = Field(ge=0.0, le=1.0)


class DrbQuantitativeSummary(FrozenModel):
    """不混合不同语义指标的 DRB 量化摘要。."""

    result_kind: Literal["official_race", "race_compatible_local"]
    evaluator_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    judge_model: str = Field(min_length=1)
    expected_tasks: int = Field(ge=1)
    generated_tasks: int = Field(ge=0)
    failed_tasks: int = Field(ge=0)
    generation_completion_rate: float = Field(ge=0.0, le=1.0)
    scored_tasks: int = Field(ge=0)
    scoring_completion_rate: float = Field(ge=0.0, le=1.0)
    race: OfficialRaceScores | None = None
    average_duration_seconds: float | None = Field(default=None, ge=0.0)
    average_input_tokens: float | None = Field(default=None, ge=0.0)
    average_output_tokens: float | None = Field(default=None, ge=0.0)
    average_tavily_calls: float | None = Field(default=None, ge=0.0)
    average_cost_usd: float | None = Field(default=None, ge=0.0)


def verify_official_repository(repository: str | Path) -> Path:
    """确认外部仓库正好处于已批准的官方提交。."""
    path = Path(repository).resolve()
    if not (path / "deepresearch_bench_race.py").is_file():
        raise ValueError(f"不是可用的 Deep Research Bench 仓库：{path}")
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    actual = completed.stdout.strip()
    if actual != OFFICIAL_DRB_COMMIT:
        raise ValueError(
            f"Deep Research Bench 提交不一致：需要 {OFFICIAL_DRB_COMMIT}，实际 {actual}"
        )
    return path


def load_official_queries(
    repository: str | Path,
    *,
    expected_count: int = 100,
) -> tuple[DrbQuery, ...]:
    """从固定官方仓库读取并验证唯一 Query。."""
    path = Path(repository) / "data" / "prompt_data" / "query.jsonl"
    queries = tuple(DrbQuery.model_validate(item) for item in _read_jsonl(path))
    if len(queries) != expected_count:
        raise ValueError(f"官方 Query 数量应为 {expected_count}，实际 {len(queries)}")
    ids = [item.id for item in queries]
    prompts = [item.prompt for item in queries]
    if len(ids) != len(set(ids)) or len(prompts) != len(set(prompts)):
        raise ValueError("官方 Query 的 ID 和 Prompt 必须唯一")
    return queries


def load_articles(path: str | Path) -> tuple[DrbArticle, ...]:
    """读取目标报告并拒绝重复任务。."""
    articles = tuple(DrbArticle.model_validate(item) for item in _read_jsonl(path))
    ids = [item.id for item in articles]
    if len(ids) != len(set(ids)):
        raise ValueError("目标 Article JSONL 包含重复任务 ID")
    return articles


def append_jsonl(path: str | Path, value: FrozenModel) -> None:
    """追加一条可恢复记录并立即落盘。."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    with destination.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(payload + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def parse_race_result(path: str | Path) -> OfficialRaceScores:
    """严格解析官方 evaluator 输出的 race_result.txt。."""
    values: dict[str, float] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        label, separator, raw_value = raw_line.partition(":")
        field = _RACE_LABELS.get(label.strip())
        if not separator or field is None:
            continue
        values[field] = float(raw_value.strip())
    if set(values) != set(_RACE_LABELS.values()):
        raise ValueError("官方 RACE 结果缺少四维或 Overall")
    return OfficialRaceScores.model_validate(values)


def race_result_kind(judge_model: str) -> Literal[
    "official_race", "race_compatible_local"
]:
    """只按官方明确模型名称决定结果标识。."""
    normalized = judge_model.strip().casefold()
    return (
        "official_race"
        if normalized in OFFICIAL_RACE_JUDGE_MODELS
        else "race_compatible_local"
    )


def summarize_drb(
    *,
    queries: Sequence[DrbQuery],
    articles: Sequence[DrbArticle],
    metrics: Sequence[DrbTaskMetric],
    scored_tasks: int,
    judge_model: str,
    race: OfficialRaceScores | None,
) -> DrbQuantitativeSummary:
    """从显式样本数和成功任务生成可复算摘要。."""
    expected = len(queries)
    article_ids = {item.id for item in articles}
    query_ids = {item.id for item in queries}
    if not article_ids <= query_ids:
        raise ValueError("Article 包含官方 Query 之外的任务")
    latest_metrics = {item.id: item for item in metrics}
    completed_metrics = [
        item
        for item in latest_metrics.values()
        if item.status == "completed" and item.id in article_ids
    ]
    failed = sum(item.status == "failed" for item in latest_metrics.values())
    generated = len(article_ids)
    if scored_tasks < 0 or scored_tasks > generated:
        raise ValueError("RACE 成功评分任务数不能超过已生成报告数")
    return DrbQuantitativeSummary(
        result_kind=race_result_kind(judge_model),
        evaluator_commit=OFFICIAL_DRB_COMMIT,
        judge_model=judge_model,
        expected_tasks=expected,
        generated_tasks=generated,
        failed_tasks=failed,
        generation_completion_rate=generated / expected,
        scored_tasks=scored_tasks,
        scoring_completion_rate=scored_tasks / expected,
        race=race,
        average_duration_seconds=_mean(
            item.duration_seconds for item in completed_metrics
        ),
        average_input_tokens=_mean(
            float(item.usage.input_tokens) for item in completed_metrics
        ),
        average_output_tokens=_mean(
            float(item.usage.output_tokens) for item in completed_metrics
        ),
        average_tavily_calls=_mean(
            float(item.usage.tavily_calls) for item in completed_metrics
        ),
        average_cost_usd=_mean(
            item.usage.cost_usd for item in completed_metrics
        ),
    )


def run_official_race(
    *,
    repository: str | Path,
    articles: str | Path,
    work_dir: str | Path,
    target_name: str,
    limit: int | None = None,
    task_ids: Sequence[int] = (),
    max_workers: int = 1,
) -> Path:
    """把目标 JSONL 交给固定官方 evaluator，不复制其实现。."""
    repo = verify_official_repository(repository)
    if not _TARGET_NAME.fullmatch(target_name):
        raise ValueError("target_name 只能包含字母、数字、点、下划线和连字符")
    if limit is not None and limit < 1:
        raise ValueError("limit 必须大于零")
    if limit is not None and task_ids:
        raise ValueError("limit 和 task_ids 不能同时使用")
    if max_workers < 1:
        raise ValueError("max_workers 必须大于零")
    work = Path(work_dir).resolve()
    raw_dir = work / "raw_data"
    cleaned_dir = work / "cleaned_data"
    output_dir = work / "results"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cleaned_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(articles), raw_dir / f"{target_name}.jsonl")
    query_file = repo / "data" / "prompt_data" / "query.jsonl"
    if task_ids:
        identifiers = tuple(dict.fromkeys(task_ids))
        queries = load_official_queries(repo)
        by_id = {query.id: query for query in queries}
        missing = [identifier for identifier in identifiers if identifier not in by_id]
        if missing:
            raise ValueError(f"官方 Query 不包含任务 ID：{missing}")
        query_file = work / "selected_queries.jsonl"
        _write_jsonl_atomic(query_file, (by_id[item] for item in identifiers))
    command = [
        os.fspath(Path(sys.executable)),
        "deepresearch_bench_race.py",
        target_name,
        "--raw_data_dir",
        os.fspath(raw_dir),
        "--cleaned_data_dir",
        os.fspath(cleaned_dir),
        "--query_file",
        os.fspath(query_file),
        "--output_dir",
        os.fspath(output_dir),
        "--max_workers",
        str(max_workers),
    ]
    if limit is not None:
        command.extend(("--limit", str(limit)))
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    provider_host = urlsplit(environment.get("OPENAI_BASE_URL", "")).hostname
    if provider_host:
        no_proxy = environment.get("NO_PROXY") or environment.get("no_proxy", "")
        bypass = ",".join(value for value in (no_proxy, provider_host) if value)
        environment["NO_PROXY"] = bypass
        environment["no_proxy"] = bypass
    completed = subprocess.run(
        command,
        cwd=repo,
        check=False,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.stdout:
        sys.stdout.write(completed.stdout)
        sys.stdout.flush()
    completed.check_returncode()
    result = output_dir / "race_result.txt"
    if not result.is_file():
        raise RuntimeError("官方 evaluator 未生成 race_result.txt")
    return result


def _write_jsonl_atomic(
    path: Path,
    values: Iterable[FrozenModel],
) -> None:
    """原子写入 evaluator 工作目录中的官方 Query 子集。."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        "".join(
            json.dumps(value.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_summary(path: str | Path, summary: DrbQuantitativeSummary) -> Path:
    """原子写入不包含秘密的量化 JSON。."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(
        json.dumps(
            summary.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def _read_jsonl(path: str | Path) -> Iterable[object]:
    source = Path(path)
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL 第 {line_number} 行无效：{source}") from exc


def _mean(values: Iterable[float]) -> float | None:
    items = tuple(values)
    return sum(items) / len(items) if items else None


__all__ = [
    "DrbArticle",
    "DrbQuantitativeSummary",
    "DrbQuery",
    "DrbTaskMetric",
    "OFFICIAL_DRB_COMMIT",
    "OFFICIAL_DRB_REPOSITORY",
    "OfficialRaceScores",
    "append_jsonl",
    "load_articles",
    "load_official_queries",
    "parse_race_result",
    "race_result_kind",
    "run_official_race",
    "summarize_drb",
    "verify_official_repository",
    "write_summary",
]
