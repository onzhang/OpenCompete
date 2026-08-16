"""读取历史业务产物并原子保存评测结果的离线服务。."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from opencompete.evaluation.dataset import load_dataset
from opencompete.evaluation.hard_gates import evaluate_hard_gates
from opencompete.evaluation.judge import ReportJudge, evaluate_with_judge
from opencompete.evaluation.models import (
    EVALUATION_CONTRACT_VERSION,
    BenchmarkCase,
    CostMetrics,
    EvaluationBatchResult,
    EvaluationCaseResult,
    EvaluationRequest,
    EvaluationTarget,
    FactEvaluation,
    MetricStatus,
    RaceEvaluation,
    benchmark_case_content_sha256,
)
from opencompete.models import RunBundle
from opencompete.persistence.repository import RunRepository
from opencompete.security import (
    environment_secret_values,
    redact_text,
    sanitized_error,
)


class EvaluationService:
    """在不搜索、不修改历史产物的前提下执行 Bench。."""

    def __init__(
        self,
        *,
        repository: RunRepository,
        judge: ReportJudge | None = None,
    ) -> None:
        """绑定历史读取入口和可选 Judge。."""
        self._repository = repository
        self._judge = judge

    async def evaluate(self, request: EvaluationRequest) -> EvaluationBatchResult:
        """评测请求中的全部固定 Case/Run 绑定并逐项持久化。."""
        dataset = load_dataset(request.dataset, allow_draft=request.allow_draft)
        cases = {case.case_id: case for case in dataset.cases}
        results: list[EvaluationCaseResult] = []
        for requested_target in request.targets:
            try:
                case = cases[requested_target.case_id]
            except KeyError as exc:
                raise ValueError(
                    f"数据集中不存在评测案例：{requested_target.case_id}"
                ) from exc
            published = self._repository.load_verified(requested_target.run_id)
            bundle = published.bundle
            hard_gates = evaluate_hard_gates(
                case,
                bundle,
                target=requested_target,
            )
            race, fact = await self._judge_metrics(case, bundle)
            pinned_target = EvaluationTarget(
                case_id=requested_target.case_id,
                run_id=bundle.run_id,
                artifact_revision=bundle.artifact_revision,
            )
            result = EvaluationCaseResult(
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.version,
                case_id=case.case_id,
                evaluation_contract_version=EVALUATION_CONTRACT_VERSION,
                case_content_sha256=benchmark_case_content_sha256(case),
                judge_implementation=_judge_implementation(self._judge),
                judge_model=_judge_str_attribute(self._judge, "model_name"),
                judge_max_tokens=_judge_int_attribute(self._judge, "max_tokens"),
                judge_prompt_version=_judge_str_attribute(
                    self._judge,
                    "prompt_version",
                ),
                target=pinned_target,
                hard_gates=hard_gates,
                race=race,
                fact=fact,
                cost=bundle_cost(bundle),
            )
            save_evaluation(result, data_root=self._repository.data_root)
            results.append(result)
        return EvaluationBatchResult(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            results=tuple(results),
        )

    async def _judge_metrics(
        self,
        case: BenchmarkCase,
        bundle: RunBundle,
    ) -> tuple[RaceEvaluation, FactEvaluation]:
        if self._judge is None:
            return (
                RaceEvaluation(
                    status=MetricStatus.NOT_RUN,
                    reason="未配置报告 Judge，RACE 未运行。",
                ),
                FactEvaluation(
                    status=MetricStatus.NOT_RUN,
                    reason="未配置报告 Judge，FACT 未运行。",
                ),
            )
        try:
            judged = await evaluate_with_judge(case, bundle, self._judge)
        except Exception as exc:  # Judge 失败不能抹掉确定性门禁和成本结果。
            reason = f"报告 Judge 运行失败：{sanitized_error(exc).message}"
            return (
                RaceEvaluation(status=MetricStatus.FAILED, reason=reason),
                FactEvaluation(status=MetricStatus.FAILED, reason=reason),
            )
        return judged.race, judged.fact


def bundle_cost(bundle: RunBundle) -> CostMetrics:
    """把已持久化的预算结算值转换为成本指标。."""
    return CostMetrics.from_bundle(bundle)


def save_evaluation(
    result: EvaluationCaseResult,
    *,
    data_root: str | Path,
) -> Path:
    """以新文件原子保存结果，绝不覆盖既有评测或业务 Bundle。."""
    _validate_path_segment(result.target.run_id, "run_id")
    _validate_path_segment(result.evaluation_id, "evaluation_id")
    run_dir = Path(data_root) / "runs" / result.target.run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"评测目标任务目录不存在：{result.target.run_id}")
    evaluation_dir = run_dir / "evaluations"
    destination = evaluation_dir / f"{result.evaluation_id}.json"
    if destination.exists():
        raise FileExistsError(f"评测结果已存在，拒绝覆盖：{result.evaluation_id}")
    payload = (
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    serialized = payload.decode("utf-8")
    if redact_text(serialized, environment_secret_values()) != serialized:
        raise ValueError("评测结果疑似包含秘密或认证信息，拒绝持久化")
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=evaluation_dir,
        prefix=f".{result.evaluation_id}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if destination.exists():
            raise FileExistsError(
                f"评测结果已存在，拒绝覆盖：{result.evaluation_id}"
            )
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _validate_path_segment(value: str, label: str) -> None:
    if not value or Path(value).name != value or "/" in value or "\\" in value:
        raise ValueError(f"{label} 不能包含路径片段或路径分隔符")


def _judge_implementation(judge: ReportJudge | None) -> str | None:
    if judge is None:
        return None
    judge_type = type(judge)
    return f"{judge_type.__module__}.{judge_type.__qualname__}"


def _judge_str_attribute(
    judge: ReportJudge | None,
    name: str,
) -> str | None:
    if judge is None:
        return None
    value = getattr(judge, name, None)
    return value if isinstance(value, str) else None


def _judge_int_attribute(
    judge: ReportJudge | None,
    name: str,
) -> int | None:
    if judge is None:
        return None
    value = getattr(judge, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None

