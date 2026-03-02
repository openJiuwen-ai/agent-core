from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Union,
    Dict,
    List,
    Optional,
    Callable,
    Awaitable,
    TypedDict,
    TYPE_CHECKING,
)

from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.session.agent import Session

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import BaseAgent
    from openjiuwen.core.foundation.llm import AssistantMessage


# =============================================================================
# Agent Callback Event Types
# =============================================================================
class AgentCallbackEvent(str, Enum):
    """Agent callback event types for agent lifecycle.

    Lifecycle Callbacks:
        BEFORE_INVOKE: Triggered before agent.invoke() starts
        AFTER_INVOKE: Triggered after agent.invoke() completes

    Model Interaction Callbacks:
        BEFORE_MODEL_CALL: Triggered before LLM is called
        AFTER_MODEL_CALL: Triggered after LLM response is received

    Tool Execution Callbacks:
        BEFORE_TOOL_CALL: Triggered before a tool is executed
        AFTER_TOOL_CALL: Triggered after a tool execution completes
    """
    BEFORE_INVOKE = "before_invoke"
    AFTER_INVOKE = "after_invoke"
    BEFORE_MODEL_CALL = "before_model_call"
    AFTER_MODEL_CALL = "after_model_call"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"


# =============================================================================
# Event-Specific Input TypedDicts
# =============================================================================

class _InvokeInputsRequired(TypedDict):
    """Required keys shared by before/after invoke inputs."""
    query: str


class BeforeInvokeInputs(_InvokeInputsRequired, total=False):
    """Inputs for BEFORE_INVOKE.

    Middleware may mutate in-place to influence the upcoming invocation.
    - query:           (required) User input string
    - conversation_id: (optional) Session / conversation identifier
    """
    conversation_id: str


class AfterInvokeInputs(_InvokeInputsRequired, total=False):
    """Inputs for AFTER_INVOKE (invoke params + final result).

    - query:           (required) Original user input
    - conversation_id: (optional) Session / conversation identifier
    - result:          (required after invoke) {"output": str, "result_type": str}
    """
    conversation_id: str
    result: Dict[str, Any]


class BeforeModelCallInputs(TypedDict):
    """Inputs for BEFORE_MODEL_CALL.

    Middleware may mutate messages or tools in-place before the LLM call.
    - messages: Current conversation message list
    - tools:    Available tool schemas
    """
    messages: List[Any]
    tools: List[Any]


class AfterModelCallInputs(TypedDict):
    """Inputs for AFTER_MODEL_CALL (model call params + LLM response).

    - messages: Conversation messages that were sent to the LLM
    - tools:    Tool schemas that were sent to the LLM
    - response: AssistantMessage returned by the LLM
    """
    messages: List[Any]
    tools: List[Any]
    response: AssistantMessage


class BeforeToolCallInputs(TypedDict):
    """Inputs for BEFORE_TOOL_CALL.

    Middleware may rewrite tool_name or tool_args in-place before execution.
    - tool_name: Name of the tool to be called
    - tool_args: Arguments dict to pass to the tool
    """
    tool_name: str
    tool_args: Dict[str, Any]


class AfterToolCallInputs(TypedDict):
    """Inputs for AFTER_TOOL_CALL (tool params + execution result).

    - tool_name:   Name of the tool that was called
    - tool_args:   Arguments that were passed to the tool
    - tool_result: Raw execution result from the tool
    - tool_msg:    ToolMessage to be committed to context
                   (middleware may reformat in-place)
    """
    tool_name: str
    tool_args: Dict[str, Any]
    tool_result: Any
    tool_msg: Any


# =============================================================================
# Agent Callback Context (Base)
# =============================================================================

@dataclass
class AgentCallbackContext:
    """Base context object passed to middleware callbacks.

    Provides access to the agent instance and current runtime state.
    Use the event-specific subclasses for strongly-typed inputs:
        BeforeInvokeContext, AfterInvokeContext,
        BeforeModelCallContext, AfterModelCallContext,
        BeforeToolCallContext, AfterToolCallContext.

    Attributes:
        agent:   Reference to the BaseAgent instance
        event:   The callback event that triggered this callback
        inputs:  Event-specific parameters (see subclass for typed access)
        config:  Runtime configuration passed to invoke/stream
        session: Current Session object
        context: Current ModelContext (conversation history and cache)
    """
    agent: 'BaseAgent'
    event: AgentCallbackEvent
    inputs: Any = field(default_factory=dict)
    config: Any = None
    session: Optional[Session] = None
    context: Optional[ModelContext] = None


# =============================================================================
# Event-Specific Context Subclasses
# =============================================================================

@dataclass
class BeforeInvokeContext(AgentCallbackContext):
    """Context for BEFORE_INVOKE: fired before agent.invoke() starts.

    Middleware may mutate inputs.query or inputs.conversation_id in-place.
    """
    event: AgentCallbackEvent = field(default=AgentCallbackEvent.BEFORE_INVOKE)
    inputs: BeforeInvokeInputs = field(default_factory=dict)  # type: ignore[assignment]


@dataclass
class AfterInvokeContext(AgentCallbackContext):
    """Context for AFTER_INVOKE: fired after agent.invoke() completes.

    inputs.result holds the final answer:
        {"output": str, "result_type": "answer" | "error"}
    """
    event: AgentCallbackEvent = field(default=AgentCallbackEvent.AFTER_INVOKE)
    inputs: AfterInvokeInputs = field(default_factory=dict)  # type: ignore[assignment]


@dataclass
class BeforeModelCallContext(AgentCallbackContext):
    """Context for BEFORE_MODEL_CALL: fired before the LLM is invoked.

    Middleware may rewrite inputs.messages or inputs.tools in-place
    to influence the upcoming model call.
    """
    event: AgentCallbackEvent = field(default=AgentCallbackEvent.BEFORE_MODEL_CALL)
    inputs: BeforeModelCallInputs = field(default_factory=dict)  # type: ignore[assignment]


@dataclass
class AfterModelCallContext(AgentCallbackContext):
    """Context for AFTER_MODEL_CALL: fired after LLM response is received.

    inputs.response is the AssistantMessage from the LLM.
    Middleware may inspect or replace it in-place.
    """
    event: AgentCallbackEvent = field(default=AgentCallbackEvent.AFTER_MODEL_CALL)
    inputs: AfterModelCallInputs = field(default_factory=dict)  # type: ignore[assignment]


@dataclass
class BeforeToolCallContext(AgentCallbackContext):
    """Context for BEFORE_TOOL_CALL: fired before a tool is executed.

    Middleware may rewrite inputs.tool_name or inputs.tool_args in-place.
    """
    event: AgentCallbackEvent = field(default=AgentCallbackEvent.BEFORE_TOOL_CALL)
    inputs: BeforeToolCallInputs = field(default_factory=dict)  # type: ignore[assignment]


@dataclass
class AfterToolCallContext(AgentCallbackContext):
    """Context for AFTER_TOOL_CALL: fired after tool execution completes.

    inputs.tool_result is the raw execution result.
    inputs.tool_msg is the ToolMessage that will be committed to context
    (middleware may reformat it in-place before it is saved).
    """
    event: AgentCallbackEvent = field(default=AgentCallbackEvent.AFTER_TOOL_CALL)
    inputs: AfterToolCallInputs = field(default_factory=dict)  # type: ignore[assignment]


# =============================================================================
# Agent Callback Types
# =============================================================================

# Type for async hook callbacks
AgentCallback = Callable[[AgentCallbackContext], Awaitable[None]]

# Type for sync hooks (will be wrapped to async)
SyncAgentCallback = Callable[[AgentCallbackContext], None]

# Union type for any hook callback
AnyAgentCallback = Union[AgentCallback, SyncAgentCallback]


# =============================================================================
# Agent Middleware Base Class
# =============================================================================

class AgentMiddleware(ABC):
    """Abstract base class for agent middlewares.

    Agent middleware provides a class-based approach to callbacks, allowing for:
    - State management across multiple callback calls
    - Grouped related callbacks in a single class
    - Easier testing and composition

    Example:
        ```python
        class LoggingMiddleware(AgentMiddleware):
            def __init__(self, log_level: str = "INFO"):
                self.log_level = log_level
                self.call_count = 0

            async def before_model_call(self, ctx: BeforeModelCallContext):
                self.call_count += 1
                print(f"[{self.log_level}] Model call #{self.call_count}")

            async def after_model_call(self, ctx: AfterModelCallContext):
                response = ctx.inputs["response"]
                print(f"[{self.log_level}] Response received")

        agent = MyAgent(card=card)
        agent.register_middleware(LoggingMiddleware())
        ```
    """

    # Optional: Define priority (lower = runs first)
    priority: int = 100

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Called before agent.invoke() starts."""
        pass

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Called after agent.invoke() completes."""
        pass

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Called before LLM is invoked."""
        pass

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Called after LLM response is received."""
        pass

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Called before a tool is executed."""
        pass

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Called after a tool execution completes."""
        pass

    def get_callbacks(self) -> Dict[AgentCallbackEvent, AgentCallback]:
        """Extract all callbacks methods from this middleware.

        Returns:
            Dict mapping AgentCallbackEvent to the corresponding method
        """
        callbacks = {}
        event_method_map = {
            AgentCallbackEvent.BEFORE_INVOKE: 'before_invoke',
            AgentCallbackEvent.AFTER_INVOKE: 'after_invoke',
            AgentCallbackEvent.BEFORE_MODEL_CALL: 'before_model_call',
            AgentCallbackEvent.AFTER_MODEL_CALL: 'after_model_call',
            AgentCallbackEvent.BEFORE_TOOL_CALL: 'before_tool_call',
            AgentCallbackEvent.AFTER_TOOL_CALL: 'after_tool_call',
        }

        for event, method_name in event_method_map.items():
            method = getattr(self, method_name, None)
            if method and not self._is_base_method(method_name):
                callbacks[event] = method

        return callbacks

    def _is_base_method(self, method_name: str) -> bool:
        """Check if method is the base class implementation (no-op)."""
        method = getattr(self.__class__, method_name, None)
        base_method = getattr(AgentMiddleware, method_name, None)
        return method is base_method
