# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Single Agent Base Class Definition

Main classes included:
 - Ability: Ability type definition
 - AbilityManager: Agent ability manager
 - BaseAgent: Single agent base class
 - HookEvent: Hook event types
 - HookContext: Hook context data
 - HookRegistry: Hook registration and execution
 - Plugin: Plugin base class for grouped hooks

Created on: 2025-11-25
Author: huenrui1@huawei.com
"""
from __future__ import annotations

import asyncio
import json
from abc import abstractmethod, ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    List,
    Any,
    AsyncIterator,
    Union,
    Optional,
    Tuple,
    Dict,
    Callable,
    Awaitable,
    TYPE_CHECKING,
)
from pydantic import BaseModel

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.schema.event import InputEvent
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.controller.base import Controller
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ToolMessage, ToolCall
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.session.session import Session
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.workflow import WorkflowCard
from openjiuwen.core.controller.schema.controller_output import ControllerOutputChunk, ControllerOutput
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.common.exception.errors import build_error, BaseError
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.single_agent.ability_manager import AbilityManager, Ability


# =============================================================================
# Hook System - Event Types
# =============================================================================

class HookEvent(str, Enum):
    """Hook event types for agent lifecycle.

    Lifecycle Hooks:
        BEFORE_INVOKE: Triggered before agent.invoke() starts
        AFTER_INVOKE: Triggered after agent.invoke() completes

    Model Interaction Hooks:
        BEFORE_MODEL_CALL: Triggered before LLM is called
        AFTER_MODEL_CALL: Triggered after LLM response is received

    Tool Execution Hooks:
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
# Hook System - Context
# =============================================================================

@dataclass
class HookContext:
    """Context object passed to hook callbacks.

    Provides access to the agent instance, current state, and utility methods.
    This allows hooks to read and modify agent behavior.

    Attributes:
        agent: Reference to the BaseAgent instance
        event: The hook event that triggered this callback
        inputs: Original inputs to invoke/stream
        iteration: Current iteration number (for model/tool hooks)
        extra: Additional event-specific data
    """
    agent: 'BaseAgent'
    event: HookEvent
    inputs: Any = None
    iteration: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Hook System - Callback Types
# =============================================================================

# Type for async hook callbacks
HookCallback = Callable[[HookContext], Awaitable[None]]

# Type for sync hooks (will be wrapped to async)
SyncHookCallback = Callable[[HookContext], None]

# Union type for any hook callback
AnyHookCallback = Union[HookCallback, SyncHookCallback]


# =============================================================================
# Hook System - Plugin Base Class
# =============================================================================

class Plugin(ABC):
    """Abstract base class for plugin-style hooks.

    Plugin provides a class-based approach to hooks, allowing for:
    - State management across multiple hook calls
    - Grouped related hooks in a single class
    - Easier testing and composition

    Example:
        ```python
        class LoggingPlugin(Plugin):
            def __init__(self, log_level: str = "INFO"):
                self.log_level = log_level
                self.call_count = 0

            async def before_model_call(self, ctx: HookContext):
                self.call_count += 1
                print(f"[{self.log_level}] Model call #{self.call_count}")

            async def after_model_call(self, ctx: HookContext):
                response = ctx.extra.get('response')
                print(f"[{self.log_level}] Response received")

        agent = MyAgent(card=card)
        agent.register_plugin(LoggingPlugin())
        ```
    """

    # Optional: Define priority (lower = runs first)
    priority: int = 100

    async def before_invoke(self, ctx: HookContext) -> None:
        """Called before agent.invoke() starts."""
        pass

    async def after_invoke(self, ctx: HookContext) -> None:
        """Called after agent.invoke() completes."""
        pass

    async def before_model_call(self, ctx: HookContext) -> None:
        """Called before LLM is invoked."""
        pass

    async def after_model_call(self, ctx: HookContext) -> None:
        """Called after LLM response is received."""
        pass

    async def before_tool_call(self, ctx: HookContext) -> None:
        """Called before a tool is executed."""
        pass

    async def after_tool_call(self, ctx: HookContext) -> None:
        """Called after a tool execution completes."""
        pass

    def get_hooks(self) -> Dict[HookEvent, HookCallback]:
        """Extract all hook methods from this plugin.

        Returns:
            Dict mapping HookEvent to the corresponding method
        """
        hooks = {}
        event_method_map = {
            HookEvent.BEFORE_INVOKE: 'before_invoke',
            HookEvent.AFTER_INVOKE: 'after_invoke',
            HookEvent.BEFORE_MODEL_CALL: 'before_model_call',
            HookEvent.AFTER_MODEL_CALL: 'after_model_call',
            HookEvent.BEFORE_TOOL_CALL: 'before_tool_call',
            HookEvent.AFTER_TOOL_CALL: 'after_tool_call',
        }

        for event, method_name in event_method_map.items():
            method = getattr(self, method_name, None)
            if method and not self._is_base_method(method_name):
                hooks[event] = method

        return hooks

    def _is_base_method(self, method_name: str) -> bool:
        """Check if method is the base class implementation (no-op)."""
        method = getattr(self.__class__, method_name, None)
        base_method = getattr(Plugin, method_name, None)
        return method is base_method


# =============================================================================
# Hook System - Registry
# =============================================================================

class HookRegistry:
    """Registry for managing and executing hooks.

    Supports both function-style and plugin-style hooks with priority ordering.

    Example:
        ```python
        registry = HookRegistry()

        # Register function hook
        async def my_hook(ctx: HookContext):
            print(f"Hook triggered: {ctx.event}")

        registry.register(HookEvent.BEFORE_INVOKE, my_hook)

        # Register plugin
        registry.register_plugin(MyPlugin())

        # Execute hooks
        ctx = HookContext(agent=agent, event=HookEvent.BEFORE_INVOKE)
        await registry.execute(HookEvent.BEFORE_INVOKE, ctx)
        ```
    """

    def __init__(self):
        self._hooks: Dict[HookEvent, List[Tuple[int, HookCallback]]] = {
            event: [] for event in HookEvent
        }
        self._plugins: List[Plugin] = []

    def register(
        self,
        event: HookEvent,
        callback: AnyHookCallback,
        priority: int = 100
    ) -> 'HookRegistry':
        """Register a hook callback for an event.

        Args:
            event: The hook event to register for
            callback: The callback function (sync or async)
            priority: Execution priority (lower = runs first)

        Returns:
            self for chaining
        """
        # Wrap sync callbacks to async
        if not asyncio.iscoroutinefunction(callback):
            original = callback

            async def async_wrapper(ctx: HookContext):
                original(ctx)
            callback = async_wrapper

        self._hooks[event].append((priority, callback))
        # Sort by priority
        self._hooks[event].sort(key=lambda x: x[0])
        return self

    def register_plugin(self, plugin: Plugin) -> 'HookRegistry':
        """Register a plugin instance.

        Args:
            plugin: Plugin instance

        Returns:
            self for chaining
        """
        self._plugins.append(plugin)

        # Extract and register hooks from plugin
        for event, callback in plugin.get_hooks().items():
            self.register(event, callback, plugin.priority)

        return self

    def unregister(self, event: HookEvent, callback: AnyHookCallback) -> bool:
        """Unregister a hook callback.

        Args:
            event: The hook event
            callback: The callback to remove

        Returns:
            True if callback was found and removed
        """
        original_len = len(self._hooks[event])
        self._hooks[event] = [
            (p, cb) for p, cb in self._hooks[event]
            if cb != callback
        ]
        return len(self._hooks[event]) < original_len

    def clear(self, event: Optional[HookEvent] = None) -> None:
        """Clear hooks.

        Args:
            event: Specific event to clear, or None to clear all
        """
        if event:
            self._hooks[event] = []
        else:
            for e in HookEvent:
                self._hooks[e] = []

    def has_hooks(self, event: HookEvent) -> bool:
        """Check if any hooks are registered for an event.

        Args:
            event: The hook event to check

        Returns:
            True if hooks are registered
        """
        return len(self._hooks[event]) > 0

    async def execute(
        self,
        event: HookEvent,
        ctx: HookContext
    ) -> HookContext:
        """Execute all hooks for an event.

        Args:
            event: The hook event
            ctx: The hook context

        Returns:
            The (potentially modified) context
        """
        for priority, callback in self._hooks[event]:
            try:
                await callback(ctx)
            except Exception as e:
                logger.error(f"Hook error for {event.value}: {e}", exc_info=True)
                raise
        return ctx


# =============================================================================
# BaseAgent
# =============================================================================

class BaseAgent(ABC):
    """Single Agent Base Class

    Design principles:
    - Card is required (defines what the Agent is)
    - Config is optional (defines how the Agent runs)
    - All configuration methods support chaining

    Attributes:
        card: Agent card (required)
        _ability_manager: Ability manager
    """
    _config = None

    def __init__(
            self,
            card: AgentCard,
    ):
        """Initialize Agent

        Args:
            card: Agent card (required)
        """
        self.card = card
        self._ability_manager = AbilityManager()
        self._hook_registry = HookRegistry()

    # ========== Configuration Interface ==========
    @abstractmethod
    def configure(self, config) -> 'BaseAgent':
        """Set configuration"""
        pass

    @property
    def config(self):
        """get config"""
        return self._config

    @property
    def ability_manager(self) -> AbilityManager:
        return self._ability_manager

    @property
    def hook_registry(self) -> HookRegistry:
        """Access the hook registry for advanced registration."""
        return self._hook_registry

    # ========== Hook Interface ==========
    def register_hook(
        self,
        event: HookEvent,
        callback: AnyHookCallback,
        priority: int = 100
    ) -> 'BaseAgent':
        """Register a hook callback.

        Args:
            event: Hook event type
            callback: Callback function (sync or async)
            priority: Execution priority (lower = runs first)

        Returns:
            self for chaining

        Example:
            ```python
            async def my_hook(ctx: HookContext):
                print(f"Event: {ctx.event}, Iteration: {ctx.iteration}")

            agent.register_hook(HookEvent.BEFORE_MODEL_CALL, my_hook)
            ```
        """
        self._hook_registry.register(event, callback, priority)
        return self

    def register_plugin(self, plugin: Plugin) -> 'BaseAgent':
        """Register a plugin instance.

        Args:
            plugin: Plugin to register

        Returns:
            self for chaining

        Example:
            ```python
            class MyPlugin(Plugin):
                async def before_invoke(self, ctx: HookContext):
                    print("Starting task...")

            agent.register_plugin(MyPlugin())
            ```
        """
        self._hook_registry.register_plugin(plugin)
        return self

    async def _execute_hooks(
        self,
        event: HookEvent,
        inputs: Any = None,
        iteration: int = 0,
        **extra
    ) -> HookContext:
        """Execute hooks for a given event.

        This method should be called by subclasses at appropriate points
        in their execution flow.

        Args:
            event: The hook event
            inputs: Original inputs
            iteration: Current iteration number
            **extra: Additional context data

        Returns:
            HookContext with potential modifications
        """
        ctx = HookContext(
            agent=self,
            event=event,
            inputs=inputs,
            iteration=iteration,
            extra=extra
        )
        return await self._hook_registry.execute(event, ctx)

    @abstractmethod
    async def invoke(
            self,
            inputs: Any,
            session: Optional[Session] = None,
    ) -> Any:
        """Batch execution (can pass config at runtime to override)

        Args:
            inputs: Agent input, supports the following formats:
                - dict: Must contain "user_input" and "session_id"
                   e.g.: {"user_input": "xxx", "session_id": "session_123"}
                - str: Used directly as user_input, requires session or other way to get session_id
            session: Session object (optional, will be created from session_id in inputs if not provided)

        Returns:
            Agent output result
        """
        ...

    @abstractmethod
    async def stream(
            self,
            inputs: Any,
            session: Optional[Session] = None,
            stream_modes: Optional[List[StreamMode]] = None
    ) -> AsyncIterator[Any]:
        """Stream execution (can pass config at runtime to override)

        Args:
            inputs: Agent input, supports the following formats:
                - dict: Must contain "user_input" and "session_id"
                   e.g.: {"user_input": "xxx", "session_id": "session_123"}
                - str: Used directly as user_input, requires session or other way to get session_id
            session: Session object (optional, will be created from session_id in inputs if not provided)
            stream_modes: Stream output modes (optional)

        Yields:
            Agent stream output result
        """
        ...


class ControllerAgent(BaseAgent):
    """ControlleAgent

    Agent implementation built on top of Controller, used to handle complex
    event-driven tasks. Supports advanced features such as task scheduling and
    event handling.
    """

    def __init__(self, card: AgentCard, controller: Controller, config: Optional[ControllerConfig] = None):
        """Initialize ControllerAgent

        Args:
            card: Agent card defining the Agent identity and capabilities
            controller: Controller instance responsible for event handling and
                task scheduling
        """
        super().__init__(card=card)
        self._config = self._create_default_config() if config is None else config
        self.context_engine = ContextEngine(
            ContextEngineConfig()
        )
        self._controller = controller
        self._initialize_controller()

    def _initialize_controller(self):
        """Initialize controller

        Pass Agent configuration, abilities, context engine and other
        information to the Controller to ensure it can access all Agent
        capabilities.
        """
        self._controller.init(
            card=self.card,
            config=self._config,
            ability_manager=self._ability_manager,
            context_engine=self.context_engine
        )

    def _create_default_config(self) -> ControllerConfig:
        """Create default configuration"""
        return ControllerConfig()

    def configure(self, config: Union[dict, BaseModel]) -> 'BaseAgent':
        """Set uconfiguration

        Args:
            config: configuration object or dict

        Returns:
            self (supports chaining)
        """
        if isinstance(config, dict):
            self._config = ControllerConfig(**{**self._config.model_dump(), **config})
        else:
            self._config = config
        self._controller.config = self._config
        return self

    @property
    def controller(self):
        """Get controller"""
        return self._controller

    async def release_session(self, session_id: str):
        """Release session resources

        Args:
            session_id: session ID
        """
        if self.controller.event_queue:
            await self.controller.event_queue.unsubscribe(
                agent_id=self.card.id,
                session_id=session_id
            )
        from openjiuwen.core.runner import Runner
        await Runner().release(session_id=session_id)

    async def invoke(
        self,
        inputs: Union[str, dict, 'InputEvent'],
        session: Optional[Session] = None,
        **kwargs
    ) -> ControllerOutput:
        """Batch execution using controller

        Args:
            inputs: user input, supports the following formats:
                - str: used directly as user input text
                - dict: dict containing user input
                - InputEvent: pre-constructed input event object
            session: session object
            **kwargs: additional parameters

        Returns:
            ControllerOutput: controller output result

        Note:
            - Calls self._controller.invoke
            - During execution, AbilityManager state and Controller state are
              saved to the Session
            - On recovery, AbilityManager state and Controller state are
              restored from the Session
        """
        try:
            if not self.controller:
                raise RuntimeError(
                    f"{self.__class__.__name__} has no controller, "
                    "subclass should create controller before invocation"
                )

            if session is None:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="session is required",
                )

            # Convert inputs to InputEvent
            input_event = InputEvent.from_user_input(user_input=inputs)

            from openjiuwen.core.session.agent import Session as AgentSession
            if isinstance(session, AgentSession):
                agent_session = getattr(session, "_inner")
            else:
                agent_session = session

            # Call controller.invoke
            return await self.controller.invoke(
                inputs=input_event,
                session=agent_session,
                **kwargs
            )

        except BaseError:
            raise

        except Exception as e:
            logger.error(f"ControllerAgent invoke error: {e}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=str(e),
                cause=e
            ) from e

    async def stream(
            self,
            inputs: Union[str, dict, 'InputEvent'],
            session: Optional[Session] = None,
            stream_modes: Optional[List[StreamMode]] = None,
            **kwargs
    ) -> AsyncIterator[ControllerOutputChunk]:
        """Stream execution using controller

        Args:
            inputs: user input
            session: session object (optional)
            stream_modes: list of stream output modes (optional)
            **kwargs: additional parameters

        Yields:
            ControllerOutputChunk: controller output chunk

        Note:
            - Calls self.controller.stream, which manages its own lifecycle
            - During execution, AbilityManager state and Controller state are
              saved to the Session
            - Controller handles state saving and restoration internally
        """
        try:
            if not self.controller:
                raise RuntimeError(
                    f"{self.__class__.__name__} has no controller, "
                    "subclass should create controller before invocation"
                )

            if session is None:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="session is required",
                )
            from openjiuwen.core.session.agent import Session as AgentSession
            if isinstance(session, AgentSession):
                agent_session = getattr(session, "_inner")
            else:
                agent_session = session

            # Convert inputs to InputEvent
            input_event = InputEvent.from_user_input(user_input=inputs)

            # Forward directly to Controller.stream()
            async for chunk in self.controller.stream(
                inputs=input_event,
                session=agent_session,
                stream_modes=stream_modes,
                **kwargs
            ):
                yield chunk

        except BaseError:
            raise

        except Exception as e:
            logger.error(f"ControllerAgent stream error: {e}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=str(e),
                cause=e
            ) from e
