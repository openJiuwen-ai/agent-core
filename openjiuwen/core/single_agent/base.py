# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Single Agent Base Class Definition

Main classes included:
 - Ability: Ability type definition
 - AbilityManager: Agent ability manager
 - BaseAgent: Single agent base class
 - AgentCallbackEvent: Hook event types
 - HookContext: Hook context data
 - HookRegistry: Hook registration and execution
 - Plugin: Plugin base class for grouped hooks

Created on: 2025-11-25
Author: huenrui1@huawei.com
"""
from __future__ import annotations

from abc import abstractmethod, ABC
from typing import (
    List,
    Any,
    AsyncIterator,
    Union,
    Optional
)
from pydantic import BaseModel

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.schema.event import InputEvent
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.controller.base import Controller
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.session import Session
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
from openjiuwen.core.single_agent.middleware.base import AgentCallbackEvent, AgentCallbackContext, AgentMiddleware, \
    AnyAgentCallback
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.controller.schema.controller_output import ControllerOutputChunk, ControllerOutput
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.common.exception.errors import build_error, BaseError
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.skills import GitHubTree


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
        self._agent_callback_manager = AgentCallbackManager()
        # 延迟导入以避免循环依赖：skills -> runner -> single_agent -> skills
        from openjiuwen.core.single_agent.skills import SkillUtil
        if hasattr(self._config, "sys_operation_id") and self._config.sys_operation_id is not None:
            self._skill_util = SkillUtil(self._config.sys_operation_id)

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
    def agent_callback_manager(self) -> AgentCallbackManager:
        """Access the hook registry for advanced registration."""
        return self._agent_callback_manager

    async def register_skill(self, skill_path: Union[str, List[str]]):
        """Register a skill"""
        await self._skill_util.register_skills(skill_path, self)

    async def register_remote_skills(self, skills_dir: str, github_tree: GitHubTree, token: str = ""):
        """Register remote skills"""
        await self._skill_util.register_remote_skills(skills_dir, github_tree, token=token)

    def register_callback(
        self,
        event: AgentCallbackEvent,
        callback: AnyAgentCallback,
        priority: int = 100
    ) -> 'BaseAgent':
        """Register an agent callback.

        Args:
            event: agent callback event type
            callback: Callback function (sync or async)
            priority: Execution priority (lower = runs first)

        Returns:
            self for chaining
        """
        self._agent_callback_manager.register_callback(event, callback, priority)
        return self

    def register_middleware(self, middleware: AgentMiddleware) -> 'BaseAgent':
        """Register a middleware instance.

        Args:
            middleware: MiddleWare to register

        Returns:
            self for chaining
        """
        self._agent_callback_manager.register_middleware(middleware)
        return self

    async def _execute_callbacks(
        self,
        event: AgentCallbackEvent,
        inputs: Any = None,
        iteration: int = 0,
        **extra
    ) -> AgentCallbackContext:
        """Execute callbacks for a given event.

        This method should be called by subclasses at appropriate points
        in their execution flow.

        Args:
            event: The agent callback event
            inputs: Original inputs
            iteration: Current iteration number
            **extra: Additional context data

        Returns:
            AgentCallbackContext with potential modifications
        """
        ctx = AgentCallbackContext(
            agent=self,
            event=event,
            inputs=inputs,
            iteration=iteration,
            extra=extra
        )
        return await self._agent_callback_manager.execute(event, ctx)

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
