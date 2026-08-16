"""OpenCompete Bench 离线评测公共接口。."""

from opencompete.evaluation.competitive_dataset import (
    CompetitiveDatasetValidationError,
    CompetitiveReferenceReviewError,
    load_competitive_dataset,
)
from opencompete.evaluation.competitive_judge import (
    CompetitiveReportJudge,
    LangChainCompetitiveReportJudge,
)
from opencompete.evaluation.competitive_models import (
    COMPETITIVE_EVALUATION_CONTRACT_VERSION,
    COMPETITIVE_JUDGE_PROMPT_VERSION,
    CompetitiveBenchmarkCase,
    CompetitiveBenchmarkDataset,
    CompetitiveEvaluationBatchResult,
    CompetitiveEvaluationRequest,
    CompetitiveEvaluationResult,
)
from opencompete.evaluation.competitive_service import (
    CompetitiveEvaluationService,
    save_competitive_evaluation,
)
from opencompete.evaluation.dataset import (
    DatasetValidationError,
    DraftReferenceError,
    load_dataset,
    load_targets,
)
from opencompete.evaluation.hard_gates import evaluate_hard_gates
from opencompete.evaluation.judge import (
    DEFAULT_JUDGE_PROMPT_VERSION,
    LangChainReportJudge,
    ReportJudge,
    evaluate_with_judge,
)
from opencompete.evaluation.models import (
    EVALUATION_CONTRACT_VERSION,
    BenchmarkCase,
    BenchmarkDataset,
    CostMetrics,
    EvaluationBatchResult,
    EvaluationCaseResult,
    EvaluationRequest,
    EvaluationTarget,
    FactEvaluation,
    FactSupport,
    HardGateEvaluation,
    JudgeEvaluation,
    MetricStatus,
    RaceDimension,
    RaceDimensionScore,
    RaceEvaluation,
    benchmark_case_content_sha256,
)
from opencompete.evaluation.service import EvaluationService, save_evaluation

__all__ = [
    "BenchmarkCase",
    "BenchmarkDataset",
    "COMPETITIVE_EVALUATION_CONTRACT_VERSION",
    "COMPETITIVE_JUDGE_PROMPT_VERSION",
    "CompetitiveBenchmarkCase",
    "CompetitiveBenchmarkDataset",
    "CompetitiveDatasetValidationError",
    "CompetitiveEvaluationBatchResult",
    "CompetitiveEvaluationRequest",
    "CompetitiveEvaluationResult",
    "CompetitiveEvaluationService",
    "CompetitiveReferenceReviewError",
    "CompetitiveReportJudge",
    "CostMetrics",
    "DEFAULT_JUDGE_PROMPT_VERSION",
    "DatasetValidationError",
    "DraftReferenceError",
    "EvaluationBatchResult",
    "EvaluationCaseResult",
    "EvaluationRequest",
    "EvaluationService",
    "EvaluationTarget",
    "EVALUATION_CONTRACT_VERSION",
    "FactEvaluation",
    "FactSupport",
    "HardGateEvaluation",
    "JudgeEvaluation",
    "LangChainReportJudge",
    "LangChainCompetitiveReportJudge",
    "MetricStatus",
    "RaceDimension",
    "RaceDimensionScore",
    "RaceEvaluation",
    "ReportJudge",
    "benchmark_case_content_sha256",
    "evaluate_hard_gates",
    "evaluate_with_judge",
    "load_dataset",
    "load_competitive_dataset",
    "load_targets",
    "save_evaluation",
    "save_competitive_evaluation",
]
