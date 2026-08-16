"""上游子图嵌入和子图内部恢复的组合测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

from opencompete.extractors import ArtifactExtraction, StaticArtifactExtractor
from opencompete.graph import build_graph
from opencompete.models import AnalysisLevel, ResearchEntity, ResearchRequest, RunBundle
from opencompete.persistence.checkpoints import SqliteCheckpointStore


class _InterruptingResearchState(MessagesState):
    raw_notes: list[str]
    final_report: str


def _request() -> ResearchRequest:
    return ResearchRequest(
        query="比较两个产品",
        entities=(ResearchEntity(name="Alpha"), ResearchEntity(name="Beta")),
        levels=(AnalysisLevel.PRODUCT_SERVICE,),
    )


def _config() -> RunnableConfig:
    return {
        "configurable": {
            "thread_id": "run-subgraph",
            "allow_clarification": False,
            "search_api": "tavily",
            "mcp_config": None,
        }
    }


def _interrupting_research_builder(
    visits: list[str],
) -> StateGraph[
    _InterruptingResearchState,
    None,
    _InterruptingResearchState,
    _InterruptingResearchState,
]:
    def research(state: _InterruptingResearchState) -> dict[str, Any]:
        del state
        visits.append("research")
        answer = interrupt({"type": "research_confirmation"})
        return {
            "raw_notes": [],
            "final_report": f"# 已恢复研究\n\n{answer}",
        }

    builder = StateGraph(_InterruptingResearchState)
    builder.add_node("research", research)
    builder.add_edge(START, "research")
    builder.add_edge("research", END)
    return builder


@pytest.mark.asyncio
async def test_outer_saver_restores_an_interrupt_inside_embedded_subgraph(
    tmp_path: Path,
) -> None:
    visits: list[str] = []
    extractor = StaticArtifactExtractor(ArtifactExtraction())
    store = SqliteCheckpointStore(tmp_path / "checkpoints.sqlite3")
    config = _config()

    async with store.open_saver() as first_saver:
        first_process_graph = build_graph(
            first_saver,
            _interrupting_research_builder(visits),
            extractor=extractor,
        )
        await first_process_graph.ainvoke(
            {
                "run_id": "run-subgraph",
                "request": _request().model_dump(mode="json"),
            },
            config=config,
        )
        child_interrupted = await first_process_graph.ainvoke(
            Command(resume={"accepted": True, "expected_revision": 0}),
            config=config,
        )

        assert child_interrupted["__interrupt__"][0].value == {
            "type": "research_confirmation"
        }
        assert (await first_process_graph.aget_state(config, subgraphs=True)).next == (
            "odr_research",
        )

    # 关闭 SQLite 连接后重新打开并编译父 Graph；Saver 是唯一恢复真相源。
    async with store.open_saver() as second_saver:
        second_process_graph = build_graph(
            second_saver,
            _interrupting_research_builder(visits),
            extractor=extractor,
        )
        completed = await second_process_graph.ainvoke(
            Command(resume="继续研究"),
            config=config,
        )

    assert completed["final_report"].endswith("继续研究")
    assert RunBundle.model_validate(completed["bundle"]).run_id == "run-subgraph"
    assert len(visits) == 2  # interrupt 所在节点恢复时按 LangGraph 语义重放。


def test_build_does_not_need_langgraph_json(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "langgraph.json").exists()

    graph = build_graph(
        InMemorySaver(),
        _interrupting_research_builder([]),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )

    assert "odr_research" in graph.get_graph().nodes


def test_default_composes_the_upstream_builder_as_a_graph_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import opencompete.graph as graph_module

    child = _interrupting_research_builder([])
    compiled_child = child.compile()

    class RecordingBuilder:
        def __init__(self) -> None:
            self.compile_calls = 0

        def compile(self) -> Any:
            self.compile_calls += 1
            return compiled_child

    builder = RecordingBuilder()
    monkeypatch.setattr(graph_module, "deep_researcher_builder", builder)

    graph = build_graph(
        InMemorySaver(),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )

    assert builder.compile_calls == 1
    assert graph.get_graph().nodes["odr_research"].data is compiled_child
