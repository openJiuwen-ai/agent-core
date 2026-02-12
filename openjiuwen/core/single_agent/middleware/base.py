from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Union,
    Dict,
    Callable,
    Awaitable,
    TYPE_CHECKING,
)
from pydantic import BaseModel

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import BaseAgent


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
# Agent Callback Context
# =============================================================================

@dataclass
class AgentCallbackContext:
    """Context object passed to callback callbacks.

    Provides access to the agent instance, current state, and utility methods.
    This allows callbacks to read and modify agent behavior.

    Attributes:
        agent: Reference to the BaseAgent instance
        event: The callback event that triggered this callback
        inputs: Original inputs to invoke/stream
        iteration: Current iteration number (for model/tool callbacks)
        extra: Additional event-specific data
    """
    agent: 'BaseAgent'
    event: AgentCallbackEvent
    inputs: Any = None
    iteration: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


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

            async def before_model_call(self, ctx: AgentCallbackContext):
                self.call_count += 1
                print(f"[{self.log_level}] Model call #{self.call_count}")

            async def after_model_call(self, ctx: AgentCallbackContext):
                response = ctx.extra.get('response')
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
