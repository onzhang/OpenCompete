"""评测结果不可变持久化测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opencompete.evaluation.models import (
    EVALUATION_CONTRACT_VERSION,
    CostMetrics,
    EvaluationCaseResult,
    EvaluationTarget,
    FactEvaluation,
    HardGateEvaluation,
    MetricStatus,
    RaceEvaluation,
)
from opencompete.evaluation.service import save_evaluation


def _result() -> EvaluationCaseResult:
    return EvaluationCaseResult(
        evaluation_id="eval-fixed",
        dataset_id="opencompete-bench-v1",
        dataset_version="1.0.0",
        case_id="ocb-001",
        evaluation_contract_version=EVALUATION_CONTRACT_VERSION,
        case_content_sha256="a" * 64,
        target=EvaluationTarget(
            case_id="ocb-001",
            run_id="run-001",
            artifact_revision=2,
        ),
        hard_gates=HardGateEvaluation(passed=True),
        race=RaceEvaluation(
            status=MetricStatus.NOT_RUN,
            reason="未配置报告 Judge，RACE 未运行。",
        ),
        fact=FactEvaluation(
            status=MetricStatus.NOT_RUN,
            reason="未配置报告 Judge，FACT 未运行。",
        ),
        cost=CostMetrics(),
    )


def test_save_evaluation_uses_target_run_evaluations_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True)

    path = save_evaluation(_result(), data_root=tmp_path)

    assert path == run_dir / "evaluations" / "eval-fixed.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["target"]["artifact_revision"] == 2
    assert payload["evaluation_contract_version"] == EVALUATION_CONTRACT_VERSION
    assert payload["case_content_sha256"] == "a" * 64
    assert payload["race"]["status"] == "not_run"
    assert "total_score" not in payload


def test_saved_evaluation_is_immutable(tmp_path: Path) -> None:
    (tmp_path / "runs" / "run-001").mkdir(parents=True)
    result = _result()
    save_evaluation(result, data_root=tmp_path)

    with pytest.raises(FileExistsError, match="评测结果已存在"):
        save_evaluation(result, data_root=tmp_path)


def test_save_evaluation_refuses_path_traversal(tmp_path: Path) -> None:
    result = _result().model_copy(
        update={
            "target": EvaluationTarget(
                case_id="ocb-001",
                run_id="../outside",
                artifact_revision=2,
            )
        }
    )

    with pytest.raises(ValueError, match="路径"):
        save_evaluation(result, data_root=tmp_path)


def test_save_evaluation_refuses_environment_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "TEST_EVALUATION_SECRET_123456"
    monkeypatch.setenv("OPENCOMPETE_TEST_API_KEY", sentinel)
    (tmp_path / "runs" / "run-001").mkdir(parents=True)
    result = _result().model_copy(
        update={
            "race": RaceEvaluation(
                status=MetricStatus.NOT_RUN,
                reason=f"未配置 Judge：{sentinel}",
            )
        }
    )

    with pytest.raises(ValueError, match="秘密"):
        save_evaluation(result, data_root=tmp_path)

    assert not list((tmp_path / "runs" / "run-001").rglob("*.json"))
