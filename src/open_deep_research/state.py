"""Graph state definitions and data structures for the Deep Research agent."""

import operator
from typing import Annotated, Literal, Optional

from langchain_core.messages import MessageLikeRepresentation
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


###################
# Structured Outputs
###################
class ConductResearch(BaseModel):
    """Call this tool to conduct research on a specific topic."""
    research_topic: str = Field(
        description="The topic to research. Should be a single topic, and should be described in high detail (at least a paragraph).",
    )

class ResearchComplete(BaseModel):
    """Call this tool to indicate that the research is complete."""


ResearchTerminationReason = Literal[
    "no_tool_calls",
    "research_complete",
    "research_tool_limit",
    "model_turn_limit",
    "budget_exhausted",
]

SupervisorTerminationReason = Literal[
    "no_tool_calls",
    "research_complete",
    "delegation_limit",
    "model_turn_limit",
    "budget_exhausted",
]

class Summary(BaseModel):
    """Research summary with key findings."""
    
    summary: str
    key_excerpts: str

class ClarifyWithUser(BaseModel):
    """Model for user clarification requests."""
    
    need_clarification: bool = Field(
        description="Whether the user needs to be asked a clarifying question.",
    )
    question: str = Field(
        description="A question to ask the user to clarify the report scope",
    )
    verification: str = Field(
        description="Verify message that we will start research after the user has provided the necessary information.",
    )

class ResearchQuestion(BaseModel):
    """Research question and brief for guiding research."""
    
    research_brief: str = Field(
        description="A research question that will be used to guide the research.",
    )


###################
# State Definitions
###################

def override_reducer(current_value, new_value):
    """Reducer function that allows overriding values in state."""
    if isinstance(new_value, dict) and new_value.get("type") == "override":
        return new_value.get("value", new_value)
    else:
        return operator.add(current_value, new_value)
    
class AgentInputState(MessagesState):
    """InputState is only 'messages'."""

class AgentState(MessagesState):
    """Main agent state containing messages and research data."""
    
    supervisor_messages: Annotated[list[MessageLikeRepresentation], override_reducer]
    research_brief: Optional[str]
    raw_notes: Annotated[list[str], override_reducer] = []
    notes: Annotated[list[str], override_reducer] = []
    final_report: str
    budget_exhausted: bool = False
    supervisor_model_turns: int = 0
    supervisor_research_calls: int = 0
    supervisor_reflection_calls: int = 0
    supervisor_termination_reason: Optional[SupervisorTerminationReason] = None
    unexecuted_research_calls: int = 0
    researcher_termination_reasons: Annotated[
        list[ResearchTerminationReason], override_reducer
    ] = []
    unexecuted_research_tool_calls: int = 0

class SupervisorState(TypedDict):
    """State for the supervisor that manages research tasks."""
    
    supervisor_messages: Annotated[list[MessageLikeRepresentation], override_reducer]
    research_brief: str
    notes: Annotated[list[str], override_reducer] = []
    research_iterations: int = 0
    supervisor_model_turns: int = 0
    supervisor_research_calls: int = 0
    supervisor_reflection_calls: int = 0
    supervisor_termination_reason: Optional[SupervisorTerminationReason]
    unexecuted_research_calls: int = 0
    researcher_termination_reasons: Annotated[
        list[ResearchTerminationReason], override_reducer
    ] = []
    unexecuted_research_tool_calls: int = 0
    raw_notes: Annotated[list[str], override_reducer] = []
    budget_exhausted: bool = False

class ResearcherState(TypedDict):
    """State for individual researchers conducting research."""
    
    researcher_messages: Annotated[list[MessageLikeRepresentation], operator.add]
    # ``tool_call_iterations`` is retained as a durable compatibility mirror for
    # checkpoints created before the counters were separated.
    tool_call_iterations: int = 0
    model_turns: int = 0
    research_tool_calls: int = 0
    reflection_tool_calls: int = 0
    termination_reason: Optional[ResearchTerminationReason]
    unexecuted_tool_calls: int = 0
    research_topic: str
    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer] = []
    research_evidence_artifacts: Annotated[
        list[dict[str, object]], override_reducer
    ] = []

class ResearcherOutputState(BaseModel):
    """Output state from individual researchers."""
    
    compressed_research: str
    raw_notes: Annotated[list[str], override_reducer] = []
    research_evidence_artifacts: Annotated[
        list[dict[str, object]], override_reducer
    ] = []
    model_turns: int = 0
    research_tool_calls: int = 0
    reflection_tool_calls: int = 0
    termination_reason: Optional[ResearchTerminationReason] = None
    unexecuted_tool_calls: int = 0
