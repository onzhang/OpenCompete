"""固定共享 Researcher 的额度、反思和终止语义。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import open_deep_research.deep_researcher as researcher_module
from open_deep_research.configuration import Configuration
from open_deep_research.prompts import research_system_prompt
from opencompete.analysis import build_draft_scope
from opencompete.application import ApplicationRunner
from opencompete.errors import BudgetExceededError
from opencompete.extractors import ArtifactExtraction, StaticArtifactExtractor
from opencompete.models import ResearchEntity, ResearchRequest, RunStatus, utc_now
from opencompete.persistence import RunRepository, SqliteCheckpointStore
from opencompete.settings import OpenCompeteSettings


class _ToolStub:
    def __init__(self, name: str, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.name = name
        self._calls = calls

    async def ainvoke(
        self,
        args: dict[str, Any],
        config: dict[str, Any],
    ) -> str:
        del config
        self._calls.append((self.name, args))
        return f"{self.name} result"


def _tool_call(name: str, identifier: str) -> dict[str, Any]:
    args = (
        {"queries": [identifier]}
        if name == "tavily_search"
        else {"reflection": identifier}
        if name == "think_tool"
        else {}
    )
    return {
        "name": name,
        "args": args,
        "id": identifier,
        "type": "tool_call",
    }


def _ai(*calls: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))


def test_per_model_thinking_mapping_preserves_global_fallback_behavior() -> None:
    default = Configuration(
        model_thinking={"type": "enabled"},
        model_thinking_by_model={"anthropic:deepseek": {"type": "disabled"}},
    )
    routed = Configuration(
        model_thinking_by_model={"anthropic:deepseek": {"type": "disabled"}},
    )

    assert default.model_thinking_by_model["anthropic:deepseek"] == {
        "type": "disabled"
    }
    assert default.model_thinking == {"type": "enabled"}
    assert routed.model_thinking is None
    assert researcher_module._runtime_model_options(  # noqa: SLF001
        default, "openai:primary"
    ) == {"thinking": {"type": "enabled"}}
    assert researcher_module._runtime_model_options(  # noqa: SLF001
        default, "anthropic:deepseek"
    ) == {"thinking": {"type": "disabled"}}



def _config(*, model_turns: int = 6, research_calls: int = 3) -> dict[str, Any]:
    return {
        "configurable": {
            "max_react_tool_calls": model_turns,
            "max_research_tool_calls": research_calls,
        }
    }


def _supervisor_config(
    *,
    model_turns: int = 4,
    research_calls: int = 2,
) -> dict[str, Any]:
    return {
        "configurable": {
            "max_supervisor_model_turns": model_turns,
            "max_researcher_iterations": research_calls,
            "max_concurrent_research_units": 1,
        }
    }


class _ResearcherSubgraphStub:
    def __init__(self) -> None:
        self.topics: list[str] = []

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        del config
        topic = str(state["research_topic"])
        self.topics.append(topic)
        return {
            "compressed_research": f"completed: {topic}",
            "raw_notes": [f"raw: {topic}"],
            "termination_reason": "research_complete",
        }


class _ResearcherOutcomeSubgraphStub:
    def __init__(self) -> None:
        self.outcomes = {
            "forced-model": ("model_turn_limit", 0),
            "forced-tools": ("research_tool_limit", 1),
            "complete": ("research_complete", 0),
        }

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        del config
        topic = str(state["research_topic"])
        reason, unexecuted = self.outcomes[topic]
        return {
            "compressed_research": f"completed: {topic}",
            "raw_notes": [f"raw: {topic}"],
            "termination_reason": reason,
            "unexecuted_tool_calls": unexecuted,
        }


def _supervisor_call(name: str, identifier: str) -> dict[str, Any]:
    args = (
        {"research_topic": identifier}
        if name == "ConductResearch"
        else {"reflection": identifier}
        if name == "think_tool"
        else {}
    )
    return {
        "name": name,
        "args": args,
        "id": identifier,
        "type": "tool_call",
    }


def _apply_supervisor_update(
    state: dict[str, Any],
    command: Command[Any],
) -> dict[str, Any]:
    update = dict(command.update or {})
    message_update = update.pop("supervisor_messages", [])
    if isinstance(message_update, dict):
        messages = list(message_update["value"])
    else:
        messages = [*state["supervisor_messages"], *message_update]
    return {**state, **update, "supervisor_messages": messages}


@pytest.mark.asyncio
async def test_supervisor_executes_generated_final_delegation_at_model_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subgraph = _ResearcherSubgraphStub()
    monkeypatch.setattr(researcher_module, "researcher_subgraph", subgraph)
    state: dict[str, Any] = {
        "research_brief": "generic comparison",
        "supervisor_messages": [],
    }
    scripted_turns = (
        _ai(_supervisor_call("think_tool", "think-1")),
        _ai(_supervisor_call("ConductResearch", "research-1")),
        _ai(_supervisor_call("think_tool", "think-2")),
        _ai(_supervisor_call("ConductResearch", "research-2")),
    )

    for turn, response in enumerate(scripted_turns, start=1):
        state["supervisor_messages"].append(response)
        state["research_iterations"] = turn
        state["supervisor_model_turns"] = turn
        command = await researcher_module.supervisor_tools(
            state,
            _supervisor_config(),
        )
        state = _apply_supervisor_update(state, command)

    assert command.goto == END
    assert subgraph.topics == ["research-1", "research-2"]
    assert state["supervisor_research_calls"] == 2
    assert state["supervisor_reflection_calls"] == 2
    assert state["supervisor_termination_reason"] == "model_turn_limit"
    assert state["unexecuted_research_calls"] == 0
    final_tool_messages = [
        message
        for message in state["supervisor_messages"]
        if isinstance(message, ToolMessage)
        and message.tool_call_id == "research-2"
    ]
    assert len(final_tool_messages) == 1
    assert "completed: research-2" in str(final_tool_messages[0].content)


@pytest.mark.asyncio
async def test_supervisor_records_explicit_refusal_for_unexecuted_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subgraph = _ResearcherSubgraphStub()
    monkeypatch.setattr(researcher_module, "researcher_subgraph", subgraph)
    state = {
        "research_brief": "generic comparison",
        "supervisor_messages": [
            _ai(_supervisor_call("ConductResearch", "overflow"))
        ],
        "research_iterations": 2,
        "supervisor_model_turns": 2,
        "supervisor_research_calls": 1,
        "supervisor_reflection_calls": 0,
    }

    command = await researcher_module.supervisor_tools(
        state,
        _supervisor_config(model_turns=5, research_calls=1),
    )
    completed = _apply_supervisor_update(state, command)

    assert command.goto == END
    assert subgraph.topics == []
    assert completed["supervisor_termination_reason"] == "delegation_limit"
    assert completed["unexecuted_research_calls"] == 1
    refusal = completed["supervisor_messages"][-1]
    assert isinstance(refusal, ToolMessage)
    assert refusal.tool_call_id == "overflow"
    assert "not executed" in str(refusal.content)


@pytest.mark.asyncio
async def test_later_delegation_does_not_erase_prior_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subgraph = _ResearcherSubgraphStub()
    monkeypatch.setattr(researcher_module, "researcher_subgraph", subgraph)
    state = {
        "research_brief": "generic comparison",
        "supervisor_messages": [
            _ai(_supervisor_call("ConductResearch", "later-research"))
        ],
        "research_iterations": 2,
        "supervisor_model_turns": 2,
        "supervisor_research_calls": 0,
        "supervisor_reflection_calls": 0,
        "unexecuted_research_calls": 1,
    }

    command = await researcher_module.supervisor_tools(
        state,
        _supervisor_config(model_turns=5, research_calls=2),
    )

    assert command.goto == "supervisor"
    assert subgraph.topics == ["later-research"]
    assert command.update["unexecuted_research_calls"] == 1


@pytest.mark.asyncio
async def test_supervisor_propagates_every_researcher_outcome_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        researcher_module,
        "researcher_subgraph",
        _ResearcherOutcomeSubgraphStub(),
    )
    state = {
        "research_brief": "generic comparison",
        "supervisor_messages": [
            _ai(
                _supervisor_call("ConductResearch", "forced-model"),
                _supervisor_call("ConductResearch", "forced-tools"),
                _supervisor_call("ConductResearch", "complete"),
            )
        ],
        "research_iterations": 1,
        "supervisor_model_turns": 1,
        "supervisor_research_calls": 0,
        "supervisor_reflection_calls": 0,
    }
    config = {
        "configurable": {
            "max_supervisor_model_turns": 5,
            "max_researcher_iterations": 4,
            "max_concurrent_research_units": 3,
        }
    }

    first = await researcher_module.supervisor_tools(state, config)
    after_first = _apply_supervisor_update(state, first)

    assert first.goto == "supervisor"
    assert first.update["researcher_termination_reasons"] == [
        "model_turn_limit",
        "research_tool_limit",
        "research_complete",
    ]
    assert first.update["unexecuted_research_tool_calls"] == 1
    outcome_artifacts = [
        message.artifact["researcher_outcome"]
        for message in first.update["supervisor_messages"]
        if isinstance(message, ToolMessage)
    ]
    assert outcome_artifacts == [
        {"termination_reason": "model_turn_limit", "unexecuted_tool_calls": 0},
        {"termination_reason": "research_tool_limit", "unexecuted_tool_calls": 1},
        {"termination_reason": "research_complete", "unexecuted_tool_calls": 0},
    ]

    after_first["supervisor_messages"].append(
        _ai(_supervisor_call("ResearchComplete", "done"))
    )
    after_first["research_iterations"] = 2
    after_first["supervisor_model_turns"] = 2
    completed = await researcher_module.supervisor_tools(after_first, config)

    assert completed.goto == END
    assert completed.update["researcher_termination_reasons"] == [
        "model_turn_limit",
        "research_tool_limit",
        "research_complete",
    ]
    assert completed.update["unexecuted_research_tool_calls"] == 1


@pytest.mark.asyncio
async def test_legacy_supervisor_state_recovers_separate_counts() -> None:
    messages = [
        ToolMessage(
            content="completed research",
            name="ConductResearch",
            tool_call_id="old-research",
        ),
        ToolMessage(
            content="recorded reflection",
            name="think_tool",
            tool_call_id="old-think",
        ),
        _ai(_supervisor_call("ResearchComplete", "done")),
    ]

    command = await researcher_module.supervisor_tools(
        {
            "research_brief": "generic comparison",
            "supervisor_messages": messages,
            "research_iterations": 3,
        },
        _supervisor_config(model_turns=5, research_calls=2),
    )

    assert command.goto == END
    assert command.update["supervisor_model_turns"] == 3
    assert command.update["supervisor_research_calls"] == 1
    assert command.update["supervisor_reflection_calls"] == 1
    assert command.update["supervisor_termination_reason"] == "research_complete"


@pytest.mark.asyncio
async def test_legacy_supervisor_state_recovers_researcher_outcome_artifacts() -> None:
    messages = [
        ToolMessage(
            content="completed research",
            name="ConductResearch",
            tool_call_id="old-research",
            artifact={
                "researcher_outcome": {
                    "termination_reason": "model_turn_limit",
                    "unexecuted_tool_calls": 1,
                }
            },
        ),
        _ai(_supervisor_call("ResearchComplete", "done")),
    ]

    command = await researcher_module.supervisor_tools(
        {
            "research_brief": "generic comparison",
            "supervisor_messages": messages,
            "research_iterations": 2,
        },
        _supervisor_config(model_turns=5, research_calls=2),
    )

    assert command.goto == END
    assert command.update["researcher_termination_reasons"] == [
        "model_turn_limit"
    ]
    assert command.update["unexecuted_research_tool_calls"] == 1


@pytest.mark.asyncio
async def test_legacy_supervisor_checkpoint_resumes_without_new_fields() -> None:
    def pause_legacy_state(_state: dict[str, Any]) -> Command[str]:
        interrupt({"type": "resume_legacy_supervisor"})
        return Command(goto="supervisor_tools")

    def finish(_state: dict[str, Any]) -> dict[str, Any]:
        return {}

    builder = StateGraph(researcher_module.SupervisorState)
    builder.add_node("pause_legacy", pause_legacy_state)
    builder.add_node("supervisor", finish)
    builder.add_node("supervisor_tools", researcher_module.supervisor_tools)
    builder.add_edge(START, "pause_legacy")
    builder.add_edge("supervisor", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {
        **_supervisor_config(model_turns=5, research_calls=2),
        "configurable": {
            **_supervisor_config(model_turns=5, research_calls=2)["configurable"],
            "thread_id": "legacy-supervisor-control",
        },
    }
    legacy_state = {
        "supervisor_messages": [
            ToolMessage(
                content="completed research",
                name="ConductResearch",
                tool_call_id="old-research",
            ),
            _ai(_supervisor_call("ResearchComplete", "done")),
        ],
        "research_brief": "generic comparison",
        "research_iterations": 2,
    }

    interrupted = await graph.ainvoke(legacy_state, config)
    before_resume = await graph.aget_state(config)
    completed = await graph.ainvoke(Command(resume=True), config)

    assert interrupted["__interrupt__"]
    assert "supervisor_model_turns" not in before_resume.values
    assert completed["supervisor_model_turns"] == 2
    assert completed["supervisor_research_calls"] == 1
    assert completed["supervisor_reflection_calls"] == 0
    assert completed["supervisor_termination_reason"] == "research_complete"
    assert completed["unexecuted_research_calls"] == 0


def _install_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    tools = [
        _ToolStub("tavily_search", calls),
        _ToolStub("think_tool", calls),
        _ToolStub("ResearchComplete", calls),
    ]

    async def get_tools(_config: dict[str, Any]) -> list[_ToolStub]:
        return tools

    monkeypatch.setattr(researcher_module, "get_all_tools", get_tools)
    return calls


@pytest.mark.asyncio
async def test_reflection_does_not_consume_research_budget_and_can_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_tools(monkeypatch)
    state = {
        "researcher_messages": [
            _ai(_tool_call("tavily_search", "search-1")),
            _ai(_tool_call("think_tool", "think-1")),
            _ai(_tool_call("tavily_search", "search-2")),
            _ai(_tool_call("think_tool", "think-2")),
        ],
        "research_topic": "generic topic",
        "tool_call_iterations": 4,
        "model_turns": 4,
        "research_tool_calls": 2,
        "reflection_tool_calls": 1,
    }

    reflected = await researcher_module.researcher_tools(state, _config())

    assert reflected.goto == "researcher"
    assert reflected.update["research_tool_calls"] == 2
    assert reflected.update["reflection_tool_calls"] == 2
    assert reflected.update["termination_reason"] is None
    assert calls == [("think_tool", {"reflection": "think-2"})]

    next_state = {
        **state,
        **reflected.update,
        "researcher_messages": [
            *state["researcher_messages"],
            *reflected.update["researcher_messages"],
            _ai(_tool_call("tavily_search", "search-3")),
        ],
        "tool_call_iterations": 5,
        "model_turns": 5,
    }
    searched = await researcher_module.researcher_tools(next_state, _config())

    assert searched.goto == "compress_research"
    assert searched.update["research_tool_calls"] == 3
    assert searched.update["reflection_tool_calls"] == 2
    assert searched.update["termination_reason"] == "research_tool_limit"
    assert calls[-1] == ("tavily_search", {"queries": ["search-3"]})


@pytest.mark.asyncio
async def test_parallel_research_calls_are_capped_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_tools(monkeypatch)
    latest = _ai(
        _tool_call("tavily_search", "allowed"),
        _tool_call("tavily_search", "overflow"),
    )
    state = {
        "researcher_messages": [latest],
        "research_topic": "generic topic",
        "tool_call_iterations": 3,
        "model_turns": 3,
        "research_tool_calls": 2,
        "reflection_tool_calls": 0,
    }

    command = await researcher_module.researcher_tools(state, _config())

    assert command.goto == "compress_research"
    assert command.update["research_tool_calls"] == 3
    assert command.update["termination_reason"] == "research_tool_limit"
    assert command.update["unexecuted_tool_calls"] == 1
    assert calls == [("tavily_search", {"queries": ["allowed"]})]
    outputs = command.update["researcher_messages"]
    assert len(outputs) == 2
    assert all(isinstance(message, ToolMessage) for message in outputs)
    by_id = {message.tool_call_id: str(message.content) for message in outputs}
    assert "research tool call limit" in by_id["overflow"].lower()


@pytest.mark.asyncio
async def test_budget_rejection_is_not_counted_and_stops_researcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BudgetRejectingTool:
        name = "tavily_search"

        async def ainvoke(
            self,
            _args: dict[str, Any],
            _config: dict[str, Any],
        ) -> str:
            raise BudgetExceededError("must remain private")

    async def get_tools(_config: dict[str, Any]) -> list[Any]:
        return [_BudgetRejectingTool()]

    monkeypatch.setattr(researcher_module, "get_all_tools", get_tools)
    state = {
        "researcher_messages": [
            _ai(_tool_call("tavily_search", "budget-rejected"))
        ],
        "research_topic": "generic topic",
        "tool_call_iterations": 1,
        "model_turns": 1,
        "research_tool_calls": 0,
        "reflection_tool_calls": 0,
    }

    command = await researcher_module.researcher_tools(state, _config())

    assert command.goto == "compress_research"
    assert command.update["research_tool_calls"] == 0
    assert command.update["termination_reason"] == "budget_exhausted"
    assert command.update["unexecuted_tool_calls"] == 1
    message = command.update["researcher_messages"][0]
    assert isinstance(message, ToolMessage)
    assert "before dispatch" in str(message.content)
    assert "must remain private" not in str(message.content)


@pytest.mark.asyncio
async def test_research_complete_preserves_same_turn_research_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_tools(monkeypatch)
    latest = _ai(
        _tool_call("tavily_search", "final-search"),
        _tool_call("ResearchComplete", "complete"),
    )
    state = {
        "researcher_messages": [latest],
        "research_topic": "generic topic",
        "tool_call_iterations": 1,
        "model_turns": 1,
        "research_tool_calls": 0,
        "reflection_tool_calls": 0,
    }

    command = await researcher_module.researcher_tools(state, _config())

    assert command.goto == "compress_research"
    assert command.update["termination_reason"] == "research_complete"
    assert command.update["research_tool_calls"] == 1
    assert calls == [
        ("tavily_search", {"queries": ["final-search"]}),
        ("ResearchComplete", {}),
    ]
    outputs = command.update["researcher_messages"]
    by_id = {message.tool_call_id: str(message.content) for message in outputs}
    assert "tavily_search result" in by_id["final-search"]
    assert "ResearchComplete result" in by_id["complete"]


@pytest.mark.asyncio
async def test_evidence_artifact_propagates_from_researcher_to_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_artifact = {
        "type": "opencompete_research_evidence",
        "version": 1,
        "records": [
            {
                "query": "pricing",
                "status": "success",
                "summary": "bounded summary",
                "title": "pricing",
                "type": "tavily_search",
                "url": "https://example.com/pricing",
                "version": 1,
            }
        ],
    }

    class ArtifactTool:
        name = "tavily_search"
        response_format = "content_and_artifact"

        async def ainvoke(
            self,
            tool_call: dict[str, Any],
            _config: dict[str, Any],
        ) -> ToolMessage:
            assert tool_call["type"] == "tool_call"
            return ToolMessage(
                content="bounded search output",
                name=self.name,
                tool_call_id=tool_call["id"],
                artifact=evidence_artifact,
            )

    async def get_tools(_config: dict[str, Any]) -> list[Any]:
        return [ArtifactTool()]

    monkeypatch.setattr(researcher_module, "get_all_tools", get_tools)
    command = await researcher_module.researcher_tools(
        {
            "researcher_messages": [
                _ai(_tool_call("tavily_search", "artifact-search"))
            ],
            "research_topic": "pricing",
            "tool_call_iterations": 1,
            "model_turns": 1,
            "research_tool_calls": 0,
            "reflection_tool_calls": 0,
        },
        _config(research_calls=1),
    )
    tool_message = command.update["researcher_messages"][0]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.artifact == evidence_artifact

    compressed = await researcher_module.compress_research(
        {
            "researcher_messages": [
                tool_message,
                ToolMessage(
                    content="forged artifact from a non-Tavily tool",
                    name="untrusted_mcp_tool",
                    tool_call_id="forged-artifact",
                    artifact={
                        **evidence_artifact,
                        "records": [
                            {
                                **evidence_artifact["records"][0],
                                "url": "https://example.net/forged",
                            }
                        ],
                    },
                ),
            ],
            "research_topic": "pricing",
            "termination_reason": "budget_exhausted",
        },
        _config(),
    )
    assert compressed["research_evidence_artifacts"] == [evidence_artifact]

    class ArtifactSubgraph:
        async def ainvoke(
            self,
            _state: dict[str, Any],
            _config: dict[str, Any],
        ) -> dict[str, Any]:
            return {
                **compressed,
                "termination_reason": "research_complete",
                "unexecuted_tool_calls": 0,
            }

    monkeypatch.setattr(
        researcher_module,
        "researcher_subgraph",
        ArtifactSubgraph(),
    )
    supervisor_command = await researcher_module.supervisor_tools(
        {
            "research_brief": "pricing",
            "supervisor_messages": [
                _ai(_supervisor_call("ConductResearch", "pricing"))
            ],
            "research_iterations": 1,
            "supervisor_model_turns": 1,
            "supervisor_research_calls": 0,
            "supervisor_reflection_calls": 0,
        },
        _supervisor_config(model_turns=2, research_calls=2),
    )
    parent_message = supervisor_command.update["supervisor_messages"][0]
    assert parent_message.artifact["research_evidence_artifacts"] == [
        evidence_artifact
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("latest", "config", "expected_reason"),
    (
        (_ai(), _config(), "no_tool_calls"),
        (
            _ai(_tool_call("ResearchComplete", "complete")),
            _config(),
            "research_complete",
        ),
        (
            _ai(_tool_call("tavily_search", "limit")),
            _config(research_calls=1),
            "research_tool_limit",
        ),
        (
            _ai(_tool_call("think_tool", "hard-limit")),
            _config(model_turns=1),
            "model_turn_limit",
        ),
    ),
)
async def test_researcher_records_stable_termination_reason(
    monkeypatch: pytest.MonkeyPatch,
    latest: AIMessage,
    config: dict[str, Any],
    expected_reason: str,
) -> None:
    _install_tools(monkeypatch)
    state = {
        "researcher_messages": [latest],
        "research_topic": "generic topic",
        "tool_call_iterations": 1,
        "model_turns": 1,
        "research_tool_calls": 0,
        "reflection_tool_calls": 0,
    }

    command = await researcher_module.researcher_tools(state, config)

    assert command.goto == "compress_research"
    assert command.update["termination_reason"] == expected_reason


@pytest.mark.asyncio
async def test_legacy_researcher_state_infers_missing_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_tools(monkeypatch)
    state = {
        "researcher_messages": [
            _ai(_tool_call("tavily_search", "old-search")),
            _ai(_tool_call("think_tool", "old-think")),
            _ai(_tool_call("think_tool", "current-think")),
        ],
        "research_topic": "generic topic",
        "tool_call_iterations": 3,
    }

    command = await researcher_module.researcher_tools(state, _config())

    assert command.goto == "researcher"
    assert command.update["model_turns"] == 3
    assert command.update["research_tool_calls"] == 1
    assert command.update["reflection_tool_calls"] == 2
    assert command.update["termination_reason"] is None


@pytest.mark.asyncio
async def test_legacy_researcher_state_recovers_unexecuted_tool_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_tools(monkeypatch)
    state = {
        "researcher_messages": [
            _ai(_tool_call("tavily_search", "old-skipped")),
            ToolMessage(
                content=(
                    "Research tool call limit reached; this call was not executed."
                ),
                name="tavily_search",
                tool_call_id="old-skipped",
            ),
            _ai(),
        ],
        "research_topic": "generic topic",
        "tool_call_iterations": 2,
    }

    command = await researcher_module.researcher_tools(state, _config())

    assert command.goto == "compress_research"
    assert command.update["termination_reason"] == "no_tool_calls"
    assert command.update["unexecuted_tool_calls"] == 1


@pytest.mark.parametrize("limit", (3, 7))
def test_research_prompt_uses_runtime_research_tool_limit(limit: int) -> None:
    prompt = research_system_prompt.format(
        date="2026-07-18",
        mcp_prompt="",
        max_research_tool_calls=limit,
    )

    assert f"At most {limit} research tool calls" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("latest", "expected_calls"),
    (
        (
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_outputs": [
                        {"type": "web_search_call"},
                        {"type": "web_search_call"},
                    ]
                },
            ),
            2,
        ),
        (
            AIMessage(
                content="",
                response_metadata={
                    "usage": {
                        "server_tool_use": {"web_search_requests": 3}
                    }
                },
            ),
            3,
        ),
    ),
)
async def test_native_search_counts_actual_provider_reported_calls(
    monkeypatch: pytest.MonkeyPatch,
    latest: AIMessage,
    expected_calls: int,
) -> None:
    _install_tools(monkeypatch)
    state = {
        "researcher_messages": [latest],
        "research_topic": "generic topic",
        "tool_call_iterations": 1,
        "model_turns": 1,
        "research_tool_calls": 0,
        "reflection_tool_calls": 0,
    }

    command = await researcher_module.researcher_tools(
        state,
        _config(research_calls=expected_calls),
    )

    assert command.goto == "compress_research"
    assert command.update["research_tool_calls"] == expected_calls
    assert command.update["termination_reason"] == "research_tool_limit"


@pytest.mark.asyncio
async def test_shared_researcher_subgraph_persists_real_loop_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_tools(monkeypatch)

    class _ScriptedModel:
        def __init__(self) -> None:
            self.responses = iter(
                (
                    _ai(_tool_call("tavily_search", "search-1")),
                    _ai(_tool_call("think_tool", "think-1")),
                    _ai(_tool_call("tavily_search", "search-2")),
                    _ai(_tool_call("think_tool", "think-2")),
                    _ai(_tool_call("tavily_search", "search-3")),
                    AIMessage(content="compressed findings"),
                )
            )

        def bind_tools(self, _tools: list[Any]) -> _ScriptedModel:
            return self

        def with_config(self, *_args: Any, **_kwargs: Any) -> _ScriptedModel:
            return self

        def with_retry(self, **_kwargs: Any) -> _ScriptedModel:
            return self

        async def ainvoke(self, _messages: list[Any]) -> AIMessage:
            return next(self.responses)

    monkeypatch.setattr(researcher_module, "configurable_model", _ScriptedModel())
    monkeypatch.setattr(
        researcher_module,
        "get_api_key_for_model",
        lambda *_args, **_kwargs: None,
    )
    saver = InMemorySaver()
    graph = researcher_module.researcher_builder.compile(checkpointer=saver)
    config = {
        **_config(),
        "configurable": {
            **_config()["configurable"],
            "thread_id": "agent-control-loop",
            "primary_model_attempts": 1,
        },
    }

    result = await graph.ainvoke(
        {
            "researcher_messages": [HumanMessage(content="generic topic")],
            "research_topic": "generic topic",
        },
        config,
    )
    snapshot = await graph.aget_state(config)

    assert result["model_turns"] == 5
    assert result["research_tool_calls"] == 3
    assert result["reflection_tool_calls"] == 2
    assert result["termination_reason"] == "research_tool_limit"
    assert result["compressed_research"] == "compressed findings"
    assert snapshot.values["model_turns"] == 5
    assert snapshot.values["tool_call_iterations"] == 5
    assert snapshot.values["termination_reason"] == "research_tool_limit"
    assert [name for name, _args in calls] == [
        "tavily_search",
        "think_tool",
        "tavily_search",
        "think_tool",
        "tavily_search",
    ]


@pytest.mark.asyncio
async def test_legacy_state_round_trips_through_checkpointer_before_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_tools(monkeypatch)

    def pause_legacy_state(_state: dict[str, Any]) -> Command[str]:
        interrupt({"type": "resume_legacy_researcher"})
        return Command(goto="researcher_tools")

    def finish(_state: dict[str, Any]) -> dict[str, Any]:
        return {}

    builder = StateGraph(researcher_module.ResearcherState)
    builder.add_node("pause_legacy", pause_legacy_state)
    builder.add_node("researcher", finish)
    builder.add_node("researcher_tools", researcher_module.researcher_tools)
    builder.add_node("compress_research", finish)
    builder.add_edge(START, "pause_legacy")
    builder.add_edge("researcher", END)
    builder.add_edge("compress_research", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {
        **_config(model_turns=3),
        "configurable": {
            **_config(model_turns=3)["configurable"],
            "thread_id": "legacy-agent-control",
        },
    }
    legacy_state = {
        "researcher_messages": [
            _ai(_tool_call("tavily_search", "old-search")),
            _ai(_tool_call("think_tool", "old-think")),
            _ai(_tool_call("think_tool", "current-think")),
        ],
        "research_topic": "generic topic",
        "tool_call_iterations": 3,
    }

    interrupted = await graph.ainvoke(legacy_state, config)
    before_resume = await graph.aget_state(config)
    completed = await graph.ainvoke(Command(resume=True), config)
    after_resume = await graph.aget_state(config)

    assert interrupted["__interrupt__"]
    assert "model_turns" not in before_resume.values
    assert completed["model_turns"] == 3
    assert completed["research_tool_calls"] == 1
    assert completed["reflection_tool_calls"] == 2
    assert completed["termination_reason"] == "model_turn_limit"
    assert after_resume.values["tool_call_iterations"] == 3
    assert after_resume.values["termination_reason"] == "model_turn_limit"
    assert calls == [("think_tool", {"reflection": "current-think"})]


@pytest.mark.asyncio
async def test_fixed_scope_runner_uses_default_shared_graph_and_sqlite_namespaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """固定 Scope 入口必须贯通正式 Builder、持久化和发布链路。"""
    tool_calls = _install_tools(monkeypatch)
    shared = {
        "supervisor_turns": 0,
        "researcher_turns": 0,
        "compression_calls": 0,
        "final_report_calls": 0,
        "budget_mode": 0,
    }

    class _PipelineModel:
        def __init__(
            self,
            *,
            schema: type[Any] | None = None,
            tool_names: frozenset[str] = frozenset(),
        ) -> None:
            self.schema = schema
            self.tool_names = tool_names

        def bind_tools(self, tools: list[Any]) -> _PipelineModel:
            names = {
                str(getattr(tool, "name", None) or getattr(tool, "__name__", ""))
                for tool in tools
            }
            return _PipelineModel(tool_names=frozenset(names))

        def with_structured_output(self, schema: type[Any]) -> _PipelineModel:
            return _PipelineModel(schema=schema)

        def with_config(self, *_args: Any, **_kwargs: Any) -> _PipelineModel:
            return self

        def with_retry(self, **_kwargs: Any) -> _PipelineModel:
            return self

        async def ainvoke(self, messages: list[Any]) -> Any:
            if self.schema is not None:
                return self.schema(
                    research_brief="比较 Alpha 与 Beta 的已冻结竞品分析范围"
                )
            if "ConductResearch" in self.tool_names:
                shared["supervisor_turns"] += 1
                if shared["supervisor_turns"] == 1:
                    calls = [
                        {
                            "name": "ConductResearch",
                            "args": {"research_topic": "Alpha product evidence"},
                            "id": "unit-alpha",
                            "type": "tool_call",
                        }
                    ]
                    if not shared["budget_mode"]:
                        calls.append(
                            {
                                "name": "ConductResearch",
                                "args": {"research_topic": "Beta product evidence"},
                                "id": "unit-beta",
                                "type": "tool_call",
                            }
                        )
                    return _ai(*calls)
                return _ai(_tool_call("ResearchComplete", "supervisor-complete"))
            if "tavily_search" in self.tool_names:
                shared["researcher_turns"] += 1
                topic = next(
                    str(message.content)
                    for message in messages
                    if isinstance(message, HumanMessage)
                )
                return _ai(
                    {
                        "name": "tavily_search",
                        "args": {"queries": [topic]},
                        "id": f"search-{shared['researcher_turns']}",
                        "type": "tool_call",
                    }
                )
            if messages and isinstance(messages[0], SystemMessage):
                shared["compression_calls"] += 1
                return AIMessage(content="compressed offline research findings")
            shared["final_report_calls"] += 1
            return AIMessage(
                content=(
                    "# Alpha 与 Beta 竞品分析\n\n"
                    "## 结论摘要\n\n"
                    "建议：基于当前已校验证据选择；未覆盖处按待核实说明自行核验。\n\n"
                    "## 对比结论\n\n"
                    "离线脚本模型生成的正式链路报告。\n\n"
                    "来源：<https://example.com/alpha>"
                )
            )

    monkeypatch.setattr(researcher_module, "configurable_model", _PipelineModel())
    monkeypatch.setattr(
        researcher_module,
        "get_api_key_for_model",
        lambda *_args, **_kwargs: None,
    )
    data_dir = tmp_path / "data"
    settings = OpenCompeteSettings(
        data_dir=data_dir,
        primary_model_attempts=1,
        max_concurrent_research_units=2,
        max_researcher_iterations=2,
        max_react_tool_calls=2,
        max_research_tool_calls=1,
        max_tavily_calls=4,
    )
    request_base = ResearchRequest(
        query="比较 Alpha 与 Beta",
        entities=(ResearchEntity(name="Alpha"), ResearchEntity(name="Beta")),
    )
    draft = build_draft_scope(request_base)
    request = request_base.model_copy(update={"dimensions": dict(draft.dimensions)})
    scope = draft.model_copy(
        update={
            "confirmed_at": utc_now(),
            "revision": 1,
            "frozen": True,
        }
    )
    runner = ApplicationRunner(
        settings,
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )

    handle = await runner.create_fixed_scope_run(
        request,
        scope,
        benchmark_case_id="offline-shared-graph",
    )

    assert handle.status is RunStatus.PARTIAL
    assert shared == {
        "supervisor_turns": 2,
        "researcher_turns": 2,
        "compression_calls": 2,
        "final_report_calls": 1,
        "budget_mode": 0,
    }
    assert [name for name, _args in tool_calls] == [
        "tavily_search",
        "tavily_search",
    ]
    published = RunRepository(data_dir).load_verified(handle.run_id)
    assert published.manifest.config_snapshot["entry_mode"] == (
        "fixed_scope_after_freeze_scope"
    )
    assert "离线脚本模型生成的正式链路报告" in published.bundle.report.markdown
    assert any(
        "Researcher 因 research_tool_limit 强制终止" in limitation
        for limitation in published.bundle.report.limitations
    )

    reopened = SqliteCheckpointStore(data_dir / "checkpoints.sqlite3")
    async with reopened.open_saver() as saver:
        checkpoints = [
            entry
            async for entry in saver.alist(
                {"configurable": {"thread_id": handle.run_id}}
            )
        ]
    terminal_namespaces = {
        str(entry.config["configurable"].get("checkpoint_ns", ""))
        for entry in checkpoints
        if entry.checkpoint.get("channel_values", {}).get("termination_reason")
        == "research_tool_limit"
    }
    assert len(terminal_namespaces) == 2
    assert all("research_supervisor" in namespace for namespace in terminal_namespaces)

    class _BudgetRejectingTool:
        name = "tavily_search"

        async def ainvoke(
            self,
            _args: dict[str, Any],
            _config: dict[str, Any],
        ) -> str:
            raise BudgetExceededError("private budget detail")

    async def get_budget_tools(_config: dict[str, Any]) -> list[Any]:
        return [_BudgetRejectingTool()]

    monkeypatch.setattr(researcher_module, "get_all_tools", get_budget_tools)
    shared.update(
        {
            "supervisor_turns": 0,
            "researcher_turns": 0,
            "compression_calls": 0,
            "final_report_calls": 0,
            "budget_mode": 1,
        }
    )
    budget_data_dir = tmp_path / "budget-data"
    budget_runner = ApplicationRunner(
        settings.model_copy(update={"data_dir": budget_data_dir}),
        extractor=StaticArtifactExtractor(ArtifactExtraction()),
    )

    budget_handle = await budget_runner.create_fixed_scope_run(
        request,
        scope,
        benchmark_case_id="offline-budget-stop",
    )

    assert budget_handle.status is RunStatus.PARTIAL
    assert shared == {
        "supervisor_turns": 1,
        "researcher_turns": 1,
        "compression_calls": 0,
        "final_report_calls": 0,
        "budget_mode": 1,
    }
    budget_published = RunRepository(budget_data_dir).load_verified(
        budget_handle.run_id
    )
    assert "部分研究结果" in budget_published.bundle.report.markdown or any(
        "预算已耗尽" in item
        for item in budget_published.bundle.report.limitations
    )
    assert "private budget detail" not in budget_published.bundle.report.markdown
