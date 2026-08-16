"""离线生命周期验收共用的假研究内核、假 Judge 和固定数据集。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

from opencompete.evaluation import (
    FactEvaluation,
    FactSupport,
    JudgeEvaluation,
    MetricStatus,
    RaceDimension,
    RaceDimensionScore,
    RaceEvaluation,
)
from opencompete.extractors import ArtifactExtraction
from opencompete.models import (
    AnalysisLevel,
    Claim,
    ClaimStatus,
    Comparison,
    Evidence,
    ResearchEntity,
    ResearchRequest,
    RunBundle,
    ScopeSpec,
    Source,
)

CASE_ID = "ocb-acceptance-alpha-beta"
RAW_NOTES = """--- SOURCE 1: Alpha 官方资料 ---
URL: https://example.com/alpha
SUMMARY: Alpha 提供稳定的核心能力和清晰的产品边界。
------------------------------
--- SOURCE 2: Beta 官方资料 ---
URL: https://example.org/beta
SUMMARY: Beta 提供可组合的核心能力和明确的适用范围。
------------------------------
"""
REPORT_MARKDOWN = (
    "# Alpha 与 Beta 竞品分析\n\n"
    "Alpha 与 Beta 都提供核心能力，但产品边界和组合方式不同。"
)


class AcceptanceResearchState(MessagesState):
    """假研究子图跨节点保存的最小状态。"""

    raw_notes: list[str]
    final_report: str


def acceptance_request() -> ResearchRequest:
    """返回范围稳定、无需网络消歧的固定研究请求。"""
    return ResearchRequest(
        query="比较 Alpha 与 Beta 的核心能力",
        entities=(ResearchEntity(name="Alpha"), ResearchEntity(name="Beta")),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
        dimensions={AnalysisLevel.PRODUCT_SERVICE: ("核心能力",)},
        exclusions=("未公开路线图",),
    )


def build_acceptance_research_graph() -> StateGraph[Any, Any, Any, Any]:
    """构建不调用模型或搜索、且可在研究节点间崩溃恢复的子图。"""

    def capture_research(
        state: AcceptanceResearchState,
    ) -> dict[str, object]:
        del state
        visit_log = os.environ.get("OPENCOMPETE_ACCEPTANCE_VISIT_LOG")
        if visit_log:
            with Path(visit_log).open("a", encoding="utf-8") as stream:
                stream.write("已捕获研究资料\n")
                stream.flush()
                os.fsync(stream.fileno())
        return {
            "messages": [AIMessage(content="已捕获离线研究资料")],
            "raw_notes": [RAW_NOTES],
        }

    def crash_once(
        state: AcceptanceResearchState,
    ) -> dict[str, object]:
        del state
        marker_value = os.environ.get("OPENCOMPETE_ACCEPTANCE_CRASH_MARKER")
        if not marker_value:
            return {}
        marker = Path(marker_value)
        if marker.exists():
            return {}
        marker.write_text("研究节点已模拟崩溃\n", encoding="utf-8")
        os._exit(73)

    def finish_report(
        state: AcceptanceResearchState,
    ) -> dict[str, object]:
        del state
        return {
            "messages": [AIMessage(content="已生成离线报告")],
            "final_report": (
                "# Alpha 与 Beta 竞品分析\n\n"
                "## 结论摘要\n\n"
                "建议：基于当前已校验结构化证据，在 Alpha 与 Beta 之间按下文条件选择；"
                "证据不足处请按「待核实」说明自行核验。\n\n"
                "Alpha 与 Beta 都提供核心能力，但产品边界和组合方式不同。\n\n"
                "来源：<https://example.com/alpha>、<https://example.org/beta>"
            ),
        }

    builder = StateGraph(AcceptanceResearchState)
    builder.add_node("capture_research", capture_research)
    builder.add_node("crash_once", crash_once)
    builder.add_node("finish_report", finish_report)
    builder.add_edge(START, "capture_research")
    builder.add_edge("capture_research", "crash_once")
    builder.add_edge("crash_once", "finish_report")
    builder.add_edge("finish_report", END)
    return builder


class AcceptanceExtractor:
    """只引用运行时已捕获 ID 的确定性结构化提取器。"""

    def __init__(self, *, partial: bool = False) -> None:
        self.partial = partial
        self.configurations: list[RunnableConfig | None] = []

    async def extract(
        self,
        *,
        scope: ScopeSpec,
        sources: tuple[Source, ...],
        evidence: tuple[Evidence, ...],
        report_markdown: str,
        config: RunnableConfig | None = None,
    ) -> ArtifactExtraction:
        del sources, report_markdown
        self.configurations.append(config)
        if not evidence:
            return ArtifactExtraction(limitations=("没有捕获到验收证据",))
        level = scope.levels[0]
        dimension = scope.dimensions[level][0]
        claims = tuple(
            Claim(
                text=f"{entity.name} 已公开说明其{dimension}",
                entity_id=entity.entity_id,
                level=level,
                dimension=dimension,
                evidence_ids=(evidence[index % len(evidence)].evidence_id,),
                status=ClaimStatus.SUPPORTED,
            )
            for index, entity in enumerate(scope.entities)
        )
        comparison = Comparison(
            entity_ids=tuple(entity.entity_id for entity in scope.entities),
            level=level,
            dimension=dimension,
            claim_ids=tuple(claim.claim_id for claim in claims),
            differences=("两者的产品边界和组合方式不同",),
            commonalities=("两者都公开提供核心能力",),
        )
        limitations = ("一个研究单元失败，已保留其余结果",) if self.partial else ()
        return ArtifactExtraction(
            claims=claims,
            comparisons=(comparison,),
            limitations=limitations,
            execution_failed=self.partial,
        )


class AcceptanceJudge:
    """对固定历史 Bundle 返回可复算 RACE/FACT 的离线 Judge。"""

    async def evaluate(
        self,
        case: object,
        bundle: RunBundle,
    ) -> JudgeEvaluation:
        del case
        dimensions = tuple(
            RaceDimensionScore(
                dimension=dimension,
                score=1.0,
                reason="固定验收报告满足该维度的任务级 Criteria。",
            )
            for dimension in RaceDimension
        )
        relationships = tuple(
            FactSupport(
                claim_id=claim.claim_id,
                evidence_id=evidence_id,
                supported=True,
                reason="固定证据直接支持对应结论。",
            )
            for claim in bundle.claims
            for evidence_id in claim.evidence_ids
        )
        return JudgeEvaluation(
            race=RaceEvaluation(
                status=MetricStatus.COMPLETED,
                dimensions=dimensions,
            ),
            fact=FactEvaluation(
                status=MetricStatus.COMPLETED,
                relationships=relationships,
                support_ratio=1.0 if relationships else None,
                reason=None if relationships else "目标没有可评的证据关系。",
            ),
        )


def write_acceptance_dataset(root: Path, expected_scope: ScopeSpec) -> Path:
    """写入仅供离线验收使用的一案例草稿数据集。"""
    dataset_root = root / "opencompete-bench"
    case_dir = dataset_root / "cases" / CASE_ID
    case_dir.mkdir(parents=True)
    _write_json(
        dataset_root / "dataset.json",
        {
            "schema_version": 1,
            "dataset_id": "opencompete-acceptance",
            "version": "1.0.0-test",
            "language": "zh-CN",
            "cases": [{"case_id": CASE_ID, "path": f"cases/{CASE_ID}"}],
        },
    )
    _write_json(
        case_dir / "case.json",
        {
            "schema_version": 1,
            "case_id": CASE_ID,
            "title": "Alpha 与 Beta 离线验收案例",
            "as_of": "2026-07-01",
            "expected_scope": expected_scope.model_dump(mode="json"),
            "reference_review": {
                "status": "draft",
                "reviewed_by": None,
                "reviewed_at": None,
                "notes": "自动化验收夹具，不冒充人工审核。",
            },
        },
    )
    _write_json(
        case_dir / "criteria.json",
        {
            "case_id": CASE_ID,
            "race": {
                "comprehensiveness": ["覆盖两个固定研究对象"],
                "depth": ["比较核心能力的差异"],
                "instruction_following": ["不讨论排除项"],
                "readability": ["使用清晰的中文结构"],
            },
            "fact": [
                {
                    "criterion_id": "fact-core-capability",
                    "description": "核心能力结论有固定来源支持",
                    "source_ids": ["bench-alpha", "bench-beta"],
                }
            ],
        },
    )
    _write_json(
        case_dir / "sources.json",
        {
            "schema_version": 1,
            "case_id": CASE_ID,
            "sources": [
                {
                    "source_id": "bench-alpha",
                    "title": "Alpha 官方资料",
                    "publisher": "Alpha",
                    "url": "https://example.com/alpha",
                    "authority": "official",
                    "as_of": "2026-07-01",
                },
                {
                    "source_id": "bench-beta",
                    "title": "Beta 官方资料",
                    "publisher": "Beta",
                    "url": "https://example.org/beta",
                    "authority": "official",
                    "as_of": "2026-07-01",
                },
            ],
        },
    )
    (case_dir / "prompt.md").write_text(
        "# 任务\n\n比较 Alpha 与 Beta 的核心能力。\n",
        encoding="utf-8",
    )
    (case_dir / "reference.md").write_text(
        "# 参考报告\n\n两者均提供核心能力，但产品边界不同。\n",
        encoding="utf-8",
    )
    return dataset_root


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
