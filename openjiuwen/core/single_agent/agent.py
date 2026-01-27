"""Single Agent Base Class Definition

Main classes included:
 - Ability: Ability type definition
 - AbilityManager: Agent ability manager
 - BaseAgent: Single agent base class

Created on: 2025-11-25
Author: huenrui1@huawei.com
"""
from __future__ import annotations

import asyncio
import json
from abc import abstractmethod, ABC
from typing import List, Any, AsyncIterator, Union, Optional, Tuple, Dict, TYPE_CHECKING
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

# Ability type definition
Ability = Union[ToolCard, WorkflowCard, AgentCard, McpServerConfig]


class AbilityManager:
    """Agent Ability Manager

    Responsibilities:
    - Store available ability Cards for Agent (metadata only, no instances)
    - Provide add/remove/query interfaces for abilities
    - Convert Cards to ToolInfo for LLM usage
    - Execute ability calls (get instances from ResourceManager)
    """

    def __init__(self):
        self._tools: Dict[str, ToolCard] = {}
        self._workflows: Dict[str, WorkflowCard] = {}
        self._agents: Dict[str, AgentCard] = {}
        self._mcp_servers: Dict[str, McpServerConfig] = {}

    def add(self, ability: Ability) -> None:
        """Add an ability

        Args:
            ability: Ability Card to add
        """
        if isinstance(ability, ToolCard):
            self._tools[ability.name] = ability
        elif isinstance(ability, WorkflowCard):
            self._workflows[ability.name] = ability
        elif isinstance(ability, AgentCard):
            self._agents[ability.name] = ability
        elif isinstance(ability, McpServerConfig):
            self._mcp_servers[ability.server_name] = ability
        else:
            logger.warning(f"Unknown ability type: {type(ability)}")

    def remove(self, name: str) -> Optional[Ability]:
        """Remove an ability by name

        Args:
            name: Ability name to remove

        Returns:
            Removed ability Card, or None if not found
        """
        if name in self._tools:
            return self._tools.pop(name)
        if name in self._workflows:
            return self._workflows.pop(name)
        if name in self._agents:
            return self._agents.pop(name)
        if name in self._mcp_servers:
            return self._mcp_servers.pop(name)
        return None

    def get(self, name: str) -> Optional[Ability]:
        """Get an ability Card by name

        Args:
            name: Ability name

        Returns:
            Ability Card, or None if not found
        """
        if name in self._tools:
            return self._tools[name]
        if name in self._workflows:
            return self._workflows[name]
        if name in self._agents:
            return self._agents[name]
        if name in self._mcp_servers:
            return self._mcp_servers[name]
        return None

    def list(self) -> List[Ability]:
        """List all ability Cards

        Returns:
            List of all ability Cards
        """
        abilities: List[Ability] = []
        abilities.extend(self._tools.values())
        abilities.extend(self._workflows.values())
        abilities.extend(self._agents.values())
        abilities.extend(self._mcp_servers.values())
        return abilities

    async def list_tool_info(
            self,
            names: Optional[List[str]] = None,
            mcp_server_name: Optional[str] = None
    ) -> List[ToolInfo]:
        """Get ToolInfo list (for LLM usage)

        Args:
            names: Filter by ability names (optional)
            mcp_server_name: Filter by MCP server name (optional)

        Returns:
            List of ToolInfo objects for LLM
        """
        tool_infos: List[ToolInfo] = []

        # Convert ToolCards to ToolInfo
        for name, tool_card in self._tools.items():
            if names is None or name in names:
                tool_info = ToolInfo(
                    name=tool_card.name,
                    description=tool_card.description or "",
                    parameters=tool_card.input_params or {}
                )
                tool_infos.append(tool_info)

        # Convert WorkflowCards to ToolInfo
        for name, workflow_card in self._workflows.items():
            if names is None or name in names:
                tool_info = ToolInfo(
                    name=workflow_card.name,
                    description=workflow_card.description or "",
                    parameters=workflow_card.input_params or {}
                )
                tool_infos.append(tool_info)

        # Convert AgentCards to ToolInfo
        for name, agent_card in self._agents.items():
            if names is None or name in names:
                # Build parameters from input_params
                params = {"type": "object", "properties": {}, "required": []}
                if hasattr(agent_card, 'input_params'):
                    for param in agent_card.input_params:
                        params["properties"][param.name] = {
                            "type": param.type,
                            "description": param.description or ""
                        }
                        if getattr(param, 'required', False):
                            params["required"].append(param.name)

                tool_info = ToolInfo(
                    name=agent_card.name,
                    description=agent_card.description or "",
                    parameters=params
                )
                tool_infos.append(tool_info)

        # Handle MCP servers if needed
        for mcp_server_name, mcp_server in self._mcp_servers.items():
            mcp_server_id = mcp_server.server_id
            from openjiuwen.core.runner import Runner
            if names is None:
                mcp_tool_infos = await Runner.resource_mgr.get_mcp_tool_infos(server_id=mcp_server_id)
                for mcp_tool in mcp_tool_infos:
                    mcp_tool_name = mcp_tool.name
                    mcp_tool_id = f'{mcp_server_id}.{mcp_server_name}.{mcp_tool_name}'
                    self._tools[mcp_tool.name] = ToolCard(id=mcp_tool_id, name=mcp_tool_name,
                                                          description=mcp_tool.description)
                    tool_infos.append(mcp_tool)

        return tool_infos

    async def execute(
            self,
            tool_call: ToolCall,
            session: Session
    ) -> Tuple[Any, ToolMessage]:
        """Execute an ability call

        Get instance from Runner.resource_mgr by card info, execute and return

        Args:
            tool_call: Tool call from LLM
            session: Session instance

        Returns:
            (result, ToolMessage) tuple
        """
        from openjiuwen.core.runner import Runner

        tool_name = tool_call.name

        # Parse arguments
        try:
            tool_args = (
                json.loads(tool_call.arguments)
                if isinstance(tool_call.arguments, str)
                else tool_call.arguments
            )
        except (json.JSONDecodeError, AttributeError):
            tool_args = {}

        result = None
        error_msg = None

        # Check ability type and execute accordingly
        if tool_name in self._tools:
            # Execute Tool - get instance from Runner.resource_mgr
            tool_card = self._tools[tool_name]
            tool_id = tool_card.id or tool_card.name
            tool = Runner.resource_mgr.get_tool(tool_id=tool_id)
            if tool:
                try:
                    result = await tool.invoke(tool_args)
                except Exception as e:
                    error_msg = f"Tool execution error: {str(e)}"
                    logger.error(error_msg)
            else:
                error_msg = f"Tool instance not found in resource_mgr: {tool_id}"

        elif tool_name in self._workflows:
            # Execute Workflow - get instance from Runner.resource_mgr
            workflow_card = self._workflows[tool_name]
            workflow_id = workflow_card.id or workflow_card.name
            workflow = await Runner.resource_mgr.get_workflow(id=workflow_id)
            if workflow:
                try:
                    result = await workflow.invoke(tool_args, session)
                except Exception as e:
                    error_msg = f"Workflow execution error: {str(e)}"
                    logger.error(error_msg)
            else:
                error_msg = (
                    f"Workflow instance not found in resource_mgr: {workflow_id}"
                )

        elif tool_name in self._agents:
            # Execute sub-Agent - get instance from Runner.resource_mgr
            agent_card = self._agents[tool_name]
            agent_id = agent_card.id or agent_card.name
            agent = await Runner.resource_mgr.get_agent(id=agent_id)
            if agent:
                try:
                    result = await agent.invoke(tool_args)
                except Exception as e:
                    error_msg = f"Agent execution error: {str(e)}"
                    logger.error(error_msg)
            else:
                error_msg = (
                    f"Agent instance not found in resource_mgr: {agent_id}"
                )

        elif tool_name in self._mcp_servers:
            # Execute MCP tool
            # TODO: Get MCP tool from MCP server
            error_msg = f"MCP tool execution not yet implemented: {tool_name}"

        else:
            # Fallback: try to get tool from Runner.resource_mgr by name
            tool = Runner.resource_mgr.get_tool(id=tool_name)
            if tool:
                try:
                    result = await tool.invoke(tool_args)
                except Exception as e:
                    error_msg = f"Tool execution error: {str(e)}"
                    logger.error(error_msg)
            else:
                error_msg = f"Ability not found in resource_mgr: {tool_name}"

        # Build ToolMessage
        content = str(result) if result is not None else (error_msg or "")
        tool_message = ToolMessage(
            content=content,
            tool_call_id=tool_call.id
        )

        return result, tool_message


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

    # ========== Configuration Interface ==========
    @abstractmethod
    def configure(self, config) -> 'BaseAgent':
        """Set configuration"""
        pass

    # ========== Ability Management Interface ==========
    @property
    def ability_manager(self) -> AbilityManager:
        return self._ability_manager

    def add_ability(self, ability: Union[Ability, List[Ability]]) -> 'BaseAgent':
        """Add an ability

        Args:
            ability: Ability Card or list (ToolCard/WorkflowCard/AgentCard/McpServerConfig)

        Returns:
            self (supports chaining)
        """
        abilities = [ability] if not isinstance(ability, list) else ability
        for ab in abilities:
            self._ability_manager.add(ab)
        return self

    def remove_ability(self, name: Union[str, List[str]]) -> 'BaseAgent':
        """Remove an ability

        Args:
            name: Ability name or list

        Returns:
            self (supports chaining)
        """
        names = [name] if isinstance(name, str) else name
        for n in names:
            self._ability_manager.remove(n)
        return self

    def get_ability(self, name: str) -> Optional[Ability]:
        """Get an ability Card

        Args:
            name: Ability name

        Returns:
            Ability Card, or None if not found
        """
        return self._ability_manager.get(name)

    def list_abilities(self) -> List[Ability]:
        """List all ability Cards

        Returns:
            List of ability Cards
        """
        return self._ability_manager.list()

    async def list_tool_info(
            self,
            names: Optional[List[str]] = None
    ) -> List[ToolInfo]:
        """Get ToolInfo list for LLM usage

        Args:
            names: Filter by ability names (optional)

        Returns:
            List of ToolInfo objects
        """
        return await self._ability_manager.list_tool_info(names=names)

    # ========== Query Interface ==========
    def get_tool_info(self) -> ToolInfo:
        """Convert current Agent to ToolInfo (for use as sub-agent)

        Returns:
            ToolInfo representing this agent
        """
        # Build parameters from agent card's input_params
        params = {"type": "object", "properties": {}, "required": []}
        if hasattr(self.card, 'input_params'):
            for param in self.card.input_params:
                params["properties"][param.name] = {
                    "type": param.type,
                    "description": getattr(param, 'description', "") or ""
                }
                if getattr(param, 'required', False):
                    params["required"].append(param.name)

        return ToolInfo(
            name=self.card.name,
            description=self.card.description or "",
            parameters=params
        )

    # ========== Execution Interface ==========

    async def _execute_ability(
            self,
            tool_calls: Union[ToolCall, List[ToolCall]],
            session: Session
    ) -> List[Tuple[Any, ToolMessage]]:
        """Execute ability calls (supports parallel execution)

        Args:
            tool_calls: Single tool call or list of tool calls
            session: Session instance

        Returns:
            List of (result, ToolMessage) tuples
        """
        # Convert single tool_call to list
        if not isinstance(tool_calls, list):
            tool_calls = [tool_calls]

        # Execute all tool calls in parallel
        tasks = [
            self._ability_manager.execute(tool_call, session)
            for tool_call in tool_calls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        final_results: List[Tuple[Any, ToolMessage]] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # Handle exception
                error_msg = f"Ability execution error: {str(result)}"
                logger.error(error_msg)
                tool_message = ToolMessage(
                    content=error_msg,
                    tool_call_id=tool_calls[i].id
                )
                final_results.append((None, tool_message))
            else:
                final_results.append(result)

        return final_results

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

            # Call controller.invoke
            return await self.controller.invoke(
                inputs=input_event,
                session=session,
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

            # Convert inputs to InputEvent
            input_event = InputEvent.from_user_input(user_input=inputs)

            # Forward directly to Controller.stream()
            async for chunk in self.controller.stream(
                inputs=input_event,
                session=session,
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
