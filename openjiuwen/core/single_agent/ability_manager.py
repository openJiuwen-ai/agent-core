# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
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
