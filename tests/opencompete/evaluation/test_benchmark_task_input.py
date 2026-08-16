"""Benchmark 规范化任务输入与同源哈希的离线契约测试。."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from opencompete import cli
from opencompete.evaluation.competitive_dataset import load_competitive_dataset
from opencompete.evaluation.competitive_judge import _quality_candidate_payload
from opencompete.evaluation.competitive_models import (
    normalize_competitive_task_text,
)
from opencompete.models import ResearchRequest, RunHandle, RunStatus, ScopeSpec

DATASET = Path("benchmarks/opencompete/v2")


class _RecordingRunner:
    """只记录固定入口参数，不构建 Graph、不读取配置或访问网络。."""

    def __init__(self) -> None:
        self.fixed_scope_calls: list[
            tuple[ResearchRequest, ScopeSpec, str | None]
        ] = []

    async def create_fixed_scope_run(
        self,
        request: ResearchRequest,
        scope: ScopeSpec,
        *,
        benchmark_case_id: str | None = None,
    ) -> RunHandle:
        self.fixed_scope_calls.append((request, scope, benchmark_case_id))
        return RunHandle(run_id="offline-benchmark-run", status=RunStatus.COMPLETED)


def _payload_json(serialized: str) -> dict[str, Any]:
    body = serialized.split("<untrusted_evaluation_data>\n", 1)[1].split(
        "\n</untrusted_evaluation_data>",
        1,
    )[0]
    payload = json.loads(body)
    assert isinstance(payload, dict)
    return payload


def test_task_text_normalization_preserves_case_and_body_semantics() -> None:
    """只统一 Unicode、换行与边界空白，不折叠正文或改变大小写。."""
    raw = " \tCafe\u0301  Product A\r\n\r\nKeep\tCASE\rLine  \n "

    normalized = normalize_competitive_task_text(raw)

    assert normalized == "Café  Product A\n\nKeep\tCASE\nLine"
    assert "Product A" in normalized
    assert "CASE" in normalized


@pytest.mark.asyncio
async def test_fixed_runner_judge_text_and_hash_share_one_normalized_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """固定生成参数、Judge 值和哈希完全一致，且不加载 .env 或真实 Provider。."""
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda *args, **kwargs: pytest.fail("离线固定入口不应加载配置或 .env"),
    )
    case = load_competitive_dataset(DATASET).cases[0]
    runner = _RecordingRunner()
    args = cli.build_parser().parse_args(
        ["benchmark-run", str(DATASET), "--case", case.case_id]
    )

    await cli._dispatch(args, runner)  # noqa: SLF001 - 验证 CLI 到 Runner 的唯一边界

    assert len(runner.fixed_scope_calls) == 1
    request, scope, benchmark_case_id = runner.fixed_scope_calls[0]
    judge_payload = _payload_json(
        _quality_candidate_payload(
            case,
            {},
            {},
            input_order_id="offline-task-input",
        )
    )
    task_text = case.normalized_task_text
    expected_sha256 = hashlib.sha256(task_text.encode("utf-8")).hexdigest()

    assert request.query == task_text
    assert judge_payload["task"]["fixed_prompt"] == task_text
    assert case.normalized_task_sha256 == expected_sha256
    assert scope.frozen is True
    assert benchmark_case_id == case.case_id
