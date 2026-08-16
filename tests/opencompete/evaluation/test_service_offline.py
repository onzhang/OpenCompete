"""不联网评测服务测试。"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from opencompete.application import ApplicationRunner
from opencompete.evaluation.dataset import DraftReferenceError
from opencompete.evaluation.judge import LangChainReportJudge
from opencompete.evaluation.models import (
    EVALUATION_CONTRACT_VERSION,
    EvaluationRequest,
    EvaluationTarget,
    FactEvaluation,
    FactSupport,
    JudgeEvaluation,
    MetricStatus,
    RaceDimension,
    RaceDimensionScore,
    RaceEvaluation,
    benchmark_case_content_sha256,
)
from opencompete.evaluation.service import EvaluationService
from opencompete.models import RunManifest, RunStatus
from opencompete.persistence.repository import RunRepository
from opencompete.settings import OpenCompeteSettings


class FakeJudge:
    """返回固定结构化评分且不具备任何搜索能力。"""

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, case, bundle) -> JudgeEvaluation:
        self.calls += 1
        return JudgeEvaluation(
            race=RaceEvaluation(
                status=MetricStatus.COMPLETED,
                dimensions=tuple(
                    RaceDimensionScore(
                        dimension=dimension,
                        score=0.75,
                        reason="离线测试评分",
                    )
                    for dimension in RaceDimension
                ),
            ),
            fact=FactEvaluation(
                status=MetricStatus.COMPLETED,
                support_ratio=1.0,
                relationships=(
                    FactSupport(
                        claim_id=bundle.claims[0].claim_id,
                        evidence_id=bundle.evidence[0].evidence_id,
                        supported=True,
                        reason="证据直接支持结论",
                    ),
                ),
            ),
        )


class InvalidFactJudge(FakeJudge):
    """返回悬空 FACT 关系，用于验证 Judge 输出也会失败关闭。"""

    async def evaluate(self, case, bundle) -> JudgeEvaluation:
        result = await super().evaluate(case, bundle)
        return result.model_copy(
            update={
                "fact": FactEvaluation(
                    status=MetricStatus.COMPLETED,
                    support_ratio=1.0,
                    relationships=(
                        FactSupport(
                            claim_id=bundle.claims[0].claim_id,
                            evidence_id="evd-missing",
                            supported=True,
                            reason="错误的悬空关系",
                        ),
                    ),
                )
            }
        )


def _completed_judgement(bundle) -> JudgeEvaluation:
    return JudgeEvaluation(
        race=RaceEvaluation(
            status=MetricStatus.COMPLETED,
            dimensions=tuple(
                RaceDimensionScore(
                    dimension=dimension,
                    score=0.75,
                    reason="固定结构化测试评分",
                )
                for dimension in RaceDimension
            ),
        ),
        fact=FactEvaluation(
            status=MetricStatus.COMPLETED,
            support_ratio=1.0,
            relationships=tuple(
                FactSupport(
                    claim_id=claim.claim_id,
                    evidence_id=evidence_id,
                    supported=True,
                    reason="固定证据支持结论",
                )
                for claim in bundle.claims
                for evidence_id in claim.evidence_ids
            ),
        ),
    )


def _install_recording_langchain_model(monkeypatch, bundle):
    recorded: dict[str, object] = {}

    class StructuredRunnable:
        async def ainvoke(self, messages):
            recorded["messages"] = messages
            return _completed_judgement(bundle)

    class ChatModel:
        def with_structured_output(self, schema):
            recorded["schema"] = schema
            return StructuredRunnable()

    def fake_init_chat_model(*, model, **kwargs):
        recorded["model"] = model
        recorded["model_kwargs"] = kwargs
        return ChatModel()

    monkeypatch.setattr(
        "opencompete.evaluation.judge.init_chat_model",
        fake_init_chat_model,
    )
    return recorded


def _published_repository(tmp_path: Path, valid_bundle) -> RunRepository:
    repository = RunRepository(tmp_path)
    repository.initialize(
        RunManifest(
            run_id=valid_bundle.run_id,
            status=RunStatus.DRAFT,
            scope_revision=0,
            artifact_revision=0,
        )
    )
    repository.publish(
        valid_bundle,
        RunManifest(
            run_id=valid_bundle.run_id,
            status=valid_bundle.status,
            scope_revision=valid_bundle.scope.revision,
            artifact_revision=valid_bundle.artifact_revision,
            budget_summary=valid_bundle.budget,
        ),
    )
    return repository


@pytest.mark.asyncio
async def test_no_judge_persists_not_run_without_fake_scores(
    tmp_path: Path, benchmark_case, valid_bundle, monkeypatch
) -> None:
    repository = _published_repository(tmp_path, valid_bundle)
    service = EvaluationService(repository=repository)
    monkeypatch.setattr(
        "opencompete.evaluation.service.load_dataset",
        lambda _path, allow_draft=False: type(
            "Dataset",
            (),
            {
                "dataset_id": "offline-test",
                "version": "1.0.0",
                "cases": (benchmark_case,),
            },
        )(),
    )
    request = EvaluationRequest(
        dataset=Path("unused.json"),
        targets=(
            EvaluationTarget(
                case_id=benchmark_case.case_id,
                run_id=valid_bundle.run_id,
            ),
        ),
    )

    batch = await service.evaluate(request)

    result = batch.results[0]
    assert result.race.status is MetricStatus.NOT_RUN
    assert result.fact.status is MetricStatus.NOT_RUN
    assert "未配置" in result.race.reason
    assert result.race.dimensions == ()
    assert result.fact.support_ratio is None
    assert "total_score" not in result.model_dump()
    saved = tmp_path / "runs" / valid_bundle.run_id / "evaluations"
    assert len(list(saved.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_judge_reads_existing_bundle_without_modifying_it(
    tmp_path: Path, benchmark_case, valid_bundle, monkeypatch
) -> None:
    repository = _published_repository(tmp_path, valid_bundle)
    before = (tmp_path / "runs" / valid_bundle.run_id / "bundle.json").read_bytes()
    judge = FakeJudge()
    service = EvaluationService(repository=repository, judge=judge)
    monkeypatch.setattr(
        "opencompete.evaluation.service.load_dataset",
        lambda _path, allow_draft=False: type(
            "Dataset",
            (),
            {
                "dataset_id": "offline-test",
                "version": "1.0.0",
                "cases": (benchmark_case,),
            },
        )(),
    )

    batch = await service.evaluate(
        EvaluationRequest(
            dataset=Path("unused.json"),
            targets=(
                EvaluationTarget(
                    case_id=benchmark_case.case_id,
                    run_id=valid_bundle.run_id,
                ),
            ),
        )
    )

    assert judge.calls == 1
    assert batch.results[0].race.status is MetricStatus.COMPLETED
    assert (tmp_path / "runs" / valid_bundle.run_id / "bundle.json").read_bytes() == before


@pytest.mark.asyncio
async def test_invalid_judge_relationship_is_persisted_as_failed_not_a_score(
    tmp_path: Path, benchmark_case, valid_bundle, monkeypatch
) -> None:
    repository = _published_repository(tmp_path, valid_bundle)
    service = EvaluationService(repository=repository, judge=InvalidFactJudge())
    monkeypatch.setattr(
        "opencompete.evaluation.service.load_dataset",
        lambda _path, allow_draft=False: type(
            "Dataset",
            (),
            {
                "dataset_id": "offline-test",
                "version": "1.0.0",
                "cases": (benchmark_case,),
            },
        )(),
    )

    batch = await service.evaluate(
        EvaluationRequest(
            dataset=Path("unused.json"),
            targets=(
                EvaluationTarget(
                    case_id=benchmark_case.case_id,
                    run_id=valid_bundle.run_id,
                ),
            ),
        )
    )

    assert batch.results[0].race.status is MetricStatus.FAILED
    assert batch.results[0].fact.status is MetricStatus.FAILED
    assert batch.results[0].fact.support_ratio is None


@pytest.mark.asyncio
async def test_langchain_report_judge_uses_all_fixed_inputs_without_tools_or_search(
    benchmark_case, valid_bundle, monkeypatch
) -> None:
    recorded = _install_recording_langchain_model(monkeypatch, valid_bundle)
    judge = LangChainReportJudge(
        model="openai:test-judge",
        max_tokens=4321,
        prompt_version="opencompete-race-fact-v1",
    )

    result = await judge.evaluate(benchmark_case, valid_bundle)

    assert result.race.status is MetricStatus.COMPLETED
    assert result.fact.status is MetricStatus.COMPLETED
    assert recorded["model"] == "openai:test-judge"
    assert recorded["model_kwargs"] == {
        "max_tokens": 4321,
        "timeout": 300.0,
        "max_retries": 0,
    }
    assert recorded["schema"] is JudgeEvaluation
    messages = recorded["messages"]
    serialized = "\n".join(str(message.content) for message in messages)
    untrusted_json = str(messages[1].content).split(
        "<untrusted_evaluation_data>\n",
        maxsplit=1,
    )[1].rsplit("\n</untrusted_evaluation_data>", maxsplit=1)[0]
    payload = json.loads(untrusted_json)
    assert payload["benchmark"]["fixed_prompt"] == benchmark_case.prompt
    assert (
        payload["benchmark"]["reference_report"]
        == benchmark_case.reference_report
    )
    assert payload["benchmark"]["race_criteria"]["comprehensiveness"][0] == (
        benchmark_case.criteria.race.comprehensiveness[0]
    )
    assert payload["benchmark"]["fact_criteria"][0]["description"] == (
        benchmark_case.criteria.fact[0].description
    )
    assert payload["target"]["report_markdown"] == valid_bundle.report.markdown
    assert payload["target"]["claims"][0]["text"] == valid_bundle.claims[0].text
    assert payload["target"]["evidence"][0]["excerpt"] == (
        valid_bundle.evidence[0].excerpt
    )
    assert "<untrusted_evaluation_data>" in serialized
    assert "不可信数据" in serialized
    assert "禁止调用工具" in serialized
    assert "禁止搜索" in serialized


def test_case_content_hash_covers_all_reproducible_case_inputs(benchmark_case) -> None:
    original = benchmark_case_content_sha256(benchmark_case)

    variants = (
        benchmark_case.model_copy(update={"prompt": benchmark_case.prompt + "\n变更"}),
        benchmark_case.model_copy(
            update={
                "reference_report": benchmark_case.reference_report + "\n变更",
            }
        ),
        benchmark_case.model_copy(
            update={
                "criteria": benchmark_case.criteria.model_copy(
                    update={
                        "race": benchmark_case.criteria.race.model_copy(
                            update={"depth": ("变更后的深度标准",)}
                        )
                    }
                )
            }
        ),
        benchmark_case.model_copy(
            update={
                "sources": (
                    benchmark_case.sources[0].model_copy(
                        update={"title": "变更后的来源标题"}
                    ),
                )
            }
        ),
        benchmark_case.model_copy(update={"as_of": date(2026, 7, 14)}),
    )

    assert len(original) == 64
    assert all(benchmark_case_content_sha256(case) != original for case in variants)


@pytest.mark.asyncio
async def test_application_uses_production_langchain_judge_only_when_configured(
    tmp_path: Path, benchmark_case, valid_bundle, monkeypatch
) -> None:
    repository = _published_repository(tmp_path, valid_bundle)
    before = (tmp_path / "runs" / valid_bundle.run_id / "bundle.json").read_bytes()
    recorded = _install_recording_langchain_model(monkeypatch, valid_bundle)
    monkeypatch.setattr(
        "opencompete.evaluation.service.load_dataset",
        lambda _path, allow_draft=False: type(
            "Dataset",
            (),
            {
                "dataset_id": "offline-test",
                "version": "1.0.0",
                "cases": (benchmark_case,),
            },
        )(),
    )
    runner = ApplicationRunner(
        OpenCompeteSettings(
            data_dir=tmp_path,
            judge_model="openai:test-judge",
            judge_model_max_tokens=4321,
            judge_prompt_version="opencompete-race-fact-v1",
        ),
        repository=repository,
    )

    batch = await runner.evaluate_dataset(
        EvaluationRequest(
            dataset=Path("unused.json"),
            targets=(
                EvaluationTarget(
                    case_id=benchmark_case.case_id,
                    run_id=valid_bundle.run_id,
                ),
            ),
        )
    )

    result = batch.results[0]
    assert result.race.status is MetricStatus.COMPLETED
    assert result.fact.status is MetricStatus.COMPLETED
    assert result.evaluation_contract_version == EVALUATION_CONTRACT_VERSION
    assert result.case_content_sha256 == benchmark_case_content_sha256(benchmark_case)
    assert result.judge_model == "openai:test-judge"
    assert result.judge_max_tokens == 4321
    assert result.judge_prompt_version == "opencompete-race-fact-v1"
    assert recorded["schema"] is JudgeEvaluation
    assert (tmp_path / "runs" / valid_bundle.run_id / "bundle.json").read_bytes() == before
    evaluation_path = next(
        (tmp_path / "runs" / valid_bundle.run_id / "evaluations").glob("*.json")
    )
    persisted = json.loads(evaluation_path.read_text(encoding="utf-8"))
    assert persisted["case_content_sha256"] == result.case_content_sha256
    assert persisted["judge_model"] == "openai:test-judge"


@pytest.mark.asyncio
async def test_application_default_evaluation_does_not_initialize_a_judge_model(
    tmp_path: Path, benchmark_case, valid_bundle, monkeypatch
) -> None:
    repository = _published_repository(tmp_path, valid_bundle)
    monkeypatch.setattr(
        "opencompete.evaluation.service.load_dataset",
        lambda _path, allow_draft=False: type(
            "Dataset",
            (),
            {
                "dataset_id": "offline-test",
                "version": "1.0.0",
                "cases": (benchmark_case,),
            },
        )(),
    )

    def forbidden_init_chat_model(**_kwargs):
        raise AssertionError("未配置 Judge 时不能初始化模型")

    monkeypatch.setattr(
        "opencompete.evaluation.judge.init_chat_model",
        forbidden_init_chat_model,
    )
    # 本仓库根目录的 opencompete.toml 配置了 judge_model；测试必须显式
    # 覆盖为 None，验证默认路径不初始化 Judge 模型。
    runner = ApplicationRunner(
        OpenCompeteSettings(data_dir=tmp_path, judge_model=None),
        repository=repository,
    )

    batch = await runner.evaluate_dataset(
        EvaluationRequest(
            dataset=Path("unused.json"),
            targets=(
                EvaluationTarget(
                    case_id=benchmark_case.case_id,
                    run_id=valid_bundle.run_id,
                ),
            ),
        )
    )

    result = batch.results[0]
    assert result.race.status is MetricStatus.NOT_RUN
    assert result.fact.status is MetricStatus.NOT_RUN
    assert result.judge_model is None
    assert result.judge_prompt_version is None


@pytest.mark.asyncio
async def test_service_rejects_draft_dataset_by_default(tmp_path: Path) -> None:
    service = EvaluationService(repository=RunRepository(tmp_path))

    with pytest.raises(DraftReferenceError):
        await service.evaluate(
            EvaluationRequest(
                dataset=Path("benchmarks/opencompete/v1"),
                targets=(
                    EvaluationTarget(
                        case_id="ocb-001-aws-lambda-vs-azure-functions",
                        run_id="missing-run",
                    ),
                ),
            )
        )


def test_evaluation_package_has_no_search_or_langsmith_imports() -> None:
    package = Path("src/opencompete/evaluation")
    forbidden = {
        "langsmith",
        "opencompete.providers",
        "open_deep_research",
        "tavily",
        "langchain_tavily",
    }
    imported: set[str] = set()
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not {
        name
        for name in imported
        if any(name == item or name.startswith(f"{item}.") for item in forbidden)
    }
