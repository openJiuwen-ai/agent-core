# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

"""Agent Group Base Module"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, AsyncIterator

from openjiuwen.core.agent.agent import BaseAgent
from openjiuwen.core.common.logging import logger
from openjiuwen.core.agent.agent import AgentRuntime
from openjiuwen.core.agent_group.config import AgentGroupConfig
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runtime.config import Config
from openjiuwen.core.runtime.resources_manager.resource_manager import ResourceMgr


class AgentGroupRuntime(AgentRuntime):
    """AgentGroup Runtime
    
    Inherits from openjiuwen.core.agent.agent.AgentRuntime
    Reuses all capabilities including TaskRuntime from pre_run()
    
    Why direct inheritance:
    1. AgentRuntime(config, resource_mgr) has simple constructor
    2. Already includes write_stream() method
    3. TaskRuntime from pre_run() has stream_iterator()
    """
    
    def __init__(self, config: Config = None, resource_mgr: ResourceMgr = None):
        """Initialize AgentGroupRuntime
        
        Args:
            config: Config object (optional, auto-created)
            resource_mgr: Resource manager (optional, auto-created)
        """
        # Create Config with agent_config if not provided
        if config is None:
            from openjiuwen.agent.config.base import AgentConfig
            config = Config()
            # Create virtual AgentConfig for Group Runtime
            agent_config = AgentConfig(id="agent_group_runtime")
            config.set_agent_config(agent_config)
        
        # Call parent constructor
        super().__init__(config, resource_mgr)
    
    # write_stream() already implemented in parent AgentRuntime
    # No need to redefine


class BaseGroup(ABC):
    """
    Abstract base class for implementing agent groups.

    This class provides the foundational structure and common functionality
    for managing groups of agents in a multi-agent system. It defines the
    essential interface that all concrete agent group implementations must
    follow, ensuring consistency across different group types.
    """

    def __init__(self, config: AgentGroupConfig):
        """
        Initialize the agent group.

        Args:
            config (AgentGroupConfig): The configuration object for this group.
        """
        self.config: AgentGroupConfig = config
        self.group_id = config.group_id
        self.agents: Dict[str, BaseAgent] = {}

    def add_agent(self, agent_id: str, agent: BaseAgent):
        """
        Register agent

        Args:
            agent_id: Agent unique identifier (primary key)
            agent: Agent instance

        Raises:
            ValueError: Agent ID already exists
        """
        if agent_id in self.agents:
            raise JiuWenBaseException(
                StatusCode.AGENT_GROUP_ADD_FAILED.code,
                StatusCode.AGENT_GROUP_ADD_FAILED.errmsg.format(reason="Agent ID already exists")
            )
        else:
            if self.get_agent_count() == self.config.max_agents:
                raise JiuWenBaseException(
                    StatusCode.AGENT_GROUP_ADD_FAILED.code,
                    StatusCode.AGENT_GROUP_ADD_FAILED.errmsg.format(
                        reason="Agent count exceeds max agents")
                )
            self.agents[agent_id] = agent
            
            # Auto-inject group reference to agent's controller
            # Duck typing: if controller has set_group method, inject self
            if hasattr(agent, 'controller') and agent.controller is not None:
                if hasattr(agent.controller, 'set_group'):
                    agent.controller.set_group(self)
                    logger.debug(
                        f"BaseGroup: Auto-injected group reference to "
                        f"agent '{agent_id}' controller"
                    )

    def get_agent_count(self) -> int:
        """
        Get the number of agents currently in the group.

        Returns:
            int: Number of agents in the group
        """
        return len(self.agents)
    
    @abstractmethod
    async def invoke(self, message, runtime: AgentGroupRuntime = None) -> Any:
        """
        Execute a synchronous operation on the agent group.

        This method processes message through the group of agents and returns
        the collective result. It should handle the complete execution flow
        including task distribution, agent coordination, and result aggregation.
        
        Args:
            message: Message object (for compatibility, also supports Dict for backward compatibility)
            runtime: Runtime for agent group instance
            
        Returns:
            The collective output from the agent group
        """
        raise NotImplementedError(
            f"invoke method or controller method must be implemented {self.__class__.__name__}"
        )
    
    @abstractmethod
    async def stream(self, message, runtime: AgentGroupRuntime = None) -> AsyncIterator[Any]:
        """
        Execute a streaming operation on the agent group.

        This method processes message and returns results as a stream,
        allowing for real-time or progressive output from the agent group.
        Useful for long-running operations or when intermediate results are needed.

        Args:
            message: Message object (for compatibility, also supports Dict for backward compatibility)
            runtime: Runtime for agent group instance

        Returns:
            The collective output from the agent group
        """
        raise NotImplementedError(
            f"stream method must be implemented by {self.__class__.__name__}"
        )


class ControllerGroup(BaseGroup):
    """Agent Group with Controller
    
    Design features (similar to ControllerAgent):
    1. Inherits BaseGroup, reuses agent management capabilities
    2. Holds GroupController, fully delegates message routing logic
    3. Automatically configures GroupController (via setup_from_group)
    4. invoke/stream fully delegated to group_controller
    5. Runtime lifecycle: pre_run -> controller.invoke -> post_run
    """

    def __init__(self, config: AgentGroupConfig, group_controller=None):
        """Initialize ControllerGroup
        
        Args:
            config: AgentGroup configuration object
            group_controller: Optional GroupController instance (will be auto-configured)
        
        Usage:
            # Simplest way - group_controller auto-configured
            group_controller = DefaultGroupController()  # No parameters needed
            group = ControllerGroup(config=config, group_controller=group_controller)
        """
        super().__init__(config)
        self.group_controller = group_controller

        # Initialize runtime (like BaseAgent)
        self._runtime = AgentGroupRuntime()

        # Auto-configure group_controller
        if self.group_controller is not None:
            self._setup_group_controller()

    def _setup_group_controller(self):
        """Auto-configure group_controller (inject group reference)"""
        if hasattr(self.group_controller, 'setup_from_group'):
            self.group_controller.setup_from_group(self)

    def _convert_message(self, message):
        """Convert dict to Message if needed (backward compatibility)"""
        from openjiuwen.core.agent.message.message import Message
        if isinstance(message, dict):
            return Message.create_user_message(
                content=message.get("content") or message.get("query", ""),
                conversation_id=message.get("conversation_id", "default_session")
            )
        return message

    async def invoke(self, message, runtime: AgentGroupRuntime = None) -> Any:
        """Synchronous invocation - Fully delegated to group_controller
        
        Lifecycle: pre_run -> controller.invoke -> post_run
        
        Args:
            message: Message object (carries message_type for routing)
            runtime: Runtime instance (optional, auto-created if None)
        
        Returns:
            Processing result
        """
        if not self.group_controller:
            raise RuntimeError(
                f"{self.__class__.__name__} has no group_controller"
            )

        message = self._convert_message(message)
        session_id = message.context.conversation_id if message.context else "default"

        # If runtime not provided, use self._runtime.pre_run to create task runtime
        if runtime is None:
            task_runtime = await self._runtime.pre_run(session_id=session_id)
            need_cleanup = True
        else:
            task_runtime = runtime
            need_cleanup = False

        try:
            # Fully delegate to group_controller
            result = await self.group_controller.invoke(message, task_runtime)
            return result if result is not None else {"output": "processed"}
        finally:
            if need_cleanup:
                await task_runtime.post_run()

    async def stream(self, message, runtime: AgentGroupRuntime = None) -> AsyncIterator[Any]:
        """Streaming invocation - real streaming output
        
        Design: 
        1. Background task executes group_controller.invoke
        2. group_controller.send_to_agent calls agent.stream and forwards chunks to runtime
        3. This method reads from runtime.stream_iterator() in real-time and yields
        
        Streaming data source:
        - Sub-agent streaming output forwarded via shared runtime
        - Includes __interaction__, workflow_final and all types
        
        Args:
            message: Message object (carries message_type for routing)
            runtime: Runtime instance (optional, auto-created if None)
        
        Yields:
            Streaming output from sub-agents
        """
        if not self.group_controller:
            raise RuntimeError(
                f"{self.__class__.__name__} has no group_controller"
            )

        message = self._convert_message(message)
        session_id = message.context.conversation_id if message.context else "default"

        # If runtime not provided, use self._runtime.pre_run to create task runtime
        if runtime is None:
            task_runtime = await self._runtime.pre_run(session_id=session_id)
            need_cleanup = True
        else:
            task_runtime = runtime
            need_cleanup = False

        # Background task executes group_controller.invoke
        # send_to_agent calls agent.stream and writes chunks to task_runtime
        async def run_controller():
            try:
                await self.group_controller.invoke(message, task_runtime)
            finally:
                if need_cleanup:
                    await task_runtime.post_run()

        task = asyncio.create_task(run_controller())

        # Real streaming read: get chunks from stream_iterator in real-time
        async for chunk in task_runtime.stream_iterator():
            yield chunk

        # Wait for background task to complete
        await task

