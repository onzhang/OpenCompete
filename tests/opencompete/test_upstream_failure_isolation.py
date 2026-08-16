"""固定上游并行研究的失败隔离行为。"""

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import open_deep_research.deep_researcher as researcher_module
from opencompete.errors import BudgetExceededError


class _ResearchGraphStub:
    """返回一个成功研究单元，并让另一个单元失败。"""

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        del config
        topic = state["research_topic"]
        if topic == "failure":
            raise RuntimeError("sensitive provider detail")
        return {
            "compressed_research": "successful finding",
            "raw_notes": ["successful raw note"],
        }


class _TransientResearchGraphStub(_ResearchGraphStub):
    """模拟单次调用重试耗尽后的临时错误。"""

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if state["research_topic"] == "failure":
            error_type = type(
                "InternalServerError",
                (Exception,),
                {"status_code": 502},
            )
            raise error_type("temporary upstream detail")
        return await super().ainvoke(state, config)


class _BudgetStoppingResearchGraphStub(_ResearchGraphStub):
    """一个单元完成，另一个报告发送前全局预算耗尽。"""

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if state["research_topic"] == "failure":
            return {
                "compressed_research": "budget-limited finding",
                "raw_notes": [],
                "termination_reason": "budget_exhausted",
            }
        return await super().ainvoke(state, config)


class _BudgetRaisingResearchGraphStub(_ResearchGraphStub):
    """模拟压缩模型也因全局预算耗尽而无法返回 Researcher 输出。"""

    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if state["research_topic"] == "failure":
            raise BudgetExceededError("private budget detail")
        return await super().ainvoke(state, config)


def test_supervisor_preserves_successful_parallel_research(monkeypatch) -> None:
    """同批单元失败不能丢掉已经成功的研究结果。"""
    monkeypatch.setattr(
        researcher_module,
        "researcher_subgraph",
        _ResearchGraphStub(),
    )
    latest = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ConductResearch",
                "args": {"research_topic": "success"},
                "id": "call-success",
                "type": "tool_call",
            },
            {
                "name": "ConductResearch",
                "args": {"research_topic": "failure"},
                "id": "call-failure",
                "type": "tool_call",
            },
        ],
    )
    state = {
        "supervisor_messages": [latest],
        "research_brief": "compare two products",
        "research_iterations": 1,
        "raw_notes": [],
        "notes": [],
    }
    config = {
        "configurable": {
            "max_concurrent_research_units": 2,
            "max_researcher_iterations": 3,
            "research_model": "openai:gpt-4.1",
        }
    }

    command = asyncio.run(researcher_module.supervisor_tools(state, config))

    assert command.goto == "supervisor"
    messages = command.update["supervisor_messages"]
    assert all(isinstance(message, ToolMessage) for message in messages)
    by_id = {message.tool_call_id: message.content for message in messages}
    assert by_id["call-success"] == "successful finding"
    assert "failed" in str(by_id["call-failure"]).lower()
    assert "sensitive provider detail" not in str(by_id["call-failure"])
    assert command.update["raw_notes"] == ["successful raw note"]


def test_supervisor_isolates_exhausted_transient_research_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单个研究单元耗尽主备尝试后仍保留成功兄弟结果。"""
    monkeypatch.setattr(
        researcher_module,
        "researcher_subgraph",
        _TransientResearchGraphStub(),
    )
    latest = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ConductResearch",
                "args": {"research_topic": "success"},
                "id": "call-success",
                "type": "tool_call",
            },
            {
                "name": "ConductResearch",
                "args": {"research_topic": "failure"},
                "id": "call-failure",
                "type": "tool_call",
            },
        ],
    )
    state = {
        "supervisor_messages": [latest],
        "research_brief": "compare two products",
        "research_iterations": 1,
        "raw_notes": [],
        "notes": [],
    }
    config = {
        "configurable": {
            "max_concurrent_research_units": 2,
            "max_researcher_iterations": 3,
            "research_model": "openai:gpt-4.1",
        }
    }

    command = asyncio.run(researcher_module.supervisor_tools(state, config))

    assert command.goto == "supervisor"
    messages = command.update["supervisor_messages"]
    by_id = {message.tool_call_id: message.content for message in messages}
    assert by_id["call-success"] == "successful finding"
    assert "failed" in str(by_id["call-failure"]).lower()
    assert "temporary upstream detail" not in str(by_id["call-failure"])
    assert command.update["raw_notes"] == ["successful raw note"]


@pytest.mark.parametrize(
    "research_graph",
    (_BudgetStoppingResearchGraphStub(), _BudgetRaisingResearchGraphStub()),
)
def test_supervisor_stops_on_budget_and_preserves_successful_sibling(
    monkeypatch: pytest.MonkeyPatch,
    research_graph: _ResearchGraphStub,
) -> None:
    monkeypatch.setattr(
        researcher_module,
        "researcher_subgraph",
        research_graph,
    )
    latest = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ConductResearch",
                "args": {"research_topic": "success"},
                "id": "call-success",
                "type": "tool_call",
            },
            {
                "name": "ConductResearch",
                "args": {"research_topic": "failure"},
                "id": "call-budget",
                "type": "tool_call",
            },
        ],
    )
    state = {
        "supervisor_messages": [latest],
        "research_brief": "compare two products",
        "research_iterations": 1,
        "raw_notes": [],
        "notes": [],
    }
    config = {
        "configurable": {
            "max_concurrent_research_units": 2,
            "max_researcher_iterations": 3,
            "research_model": "openai:gpt-4.1",
        }
    }

    command = asyncio.run(researcher_module.supervisor_tools(state, config))

    assert command.goto == "__end__"
    assert command.update["raw_notes"] == ["successful raw note"]
    assert "successful finding" in command.update["notes"]
    messages = command.update["supervisor_messages"]
    by_id = {message.tool_call_id: str(message.content) for message in messages}
    assert by_id["call-success"] == "successful finding"
    assert "private budget detail" not in by_id["call-budget"]
