# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ReActAgent Implementation

ReAct (Reasoning + Acting) paradigm Agent implementation

Created on: 2025-11-25
Author: huenrui1@huawei.com
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from pydantic import Field, BaseModel

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
# foundation.llm types for context_engine compatibility (to be removed after
# context_engine adapts to llm1)
from openjiuwen.core.foundation.llm import (
    BaseMessage as LegacyBaseMessage,
    HumanMessage as LegacyHumanMessage,
    AIMessage as LegacyAIMessage,
    ToolMessage as LegacyToolMessage,
)
# llm1 types (new interface)
from openjiuwen.core.foundation.llm1 import (
    Model,
    BaseMessage,
    AssistantMessage,
)
from openjiuwen.core.foundation.llm1.schema.config import (
    ModelConfig,
    ModelClientConfig
)
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.memory import LongTermMemory, MemoryScopeConfig
from openjiuwen.core.session.session import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.agent import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class ReActAgentConfig(BaseModel):
    """ReActAgent Configuration Class

    Attributes:
        max_iterations: Maximum number of ReAct loop iterations
    """
    mem_scope_id: str = Field(default="", description="Memory scope ID")
    # New LLM configuration using llm1 interfaces
    model_config_obj: Optional[ModelConfig] = Field(
        default=None,
        description="Model configuration"
    )
    model_client_config: Optional[ModelClientConfig] = Field(
        default=None,
        description="Model client configuration"
    )
    prompt_template_name: str = Field(
        default="",
        description="Prompt template name"
    )
    prompt_template: List[BaseMessage] = Field(
        default_factory=list,
        description="Prompt template messages"
    )
    context_window_limit: int = Field(
        default=20,
        description="Context window limit"
    )
    max_iterations: int = Field(default=5, description="Maximum iterations")

    model_config = {"extra": "allow"}

    def configure_model(
            self,
            model_config: ModelConfig
    ) -> 'ReActAgentConfig':
        """Configure model parameters

        Args:
            model_config: Model configuration object

        Returns:
            self (supports chaining)
        """
        self.model_config_obj = model_config
        return self

    def configure_model_client(
            self,
            model_client_config: ModelClientConfig
    ) -> 'ReActAgentConfig':
        """Configure model client

        Args:
            model_client_config: Model client configuration object

        Returns:
            self (supports chaining)
        """
        self.model_client_config = model_client_config
        return self

    def configure_prompt(self, prompt_name: str) -> 'ReActAgentConfig':
        """Configure prompt template name

        Args:
            prompt_name: Prompt template name

        Returns:
            self (supports chaining)
        """
        self.prompt_template_name = prompt_name
        return self

    def configure_prompt_template(
            self,
            prompt_template: List[BaseMessage]
    ) -> 'ReActAgentConfig':
        """Configure prompt template directly

        Args:
            prompt_template: Prompt template messages (List[BaseMessage])

        Returns:
            self (supports chaining)
        """
        self.prompt_template = prompt_template
        return self

    def configure_context_limit(self, limit: int) -> 'ReActAgentConfig':
        """Configure context window limit

        Args:
            limit: Context window limit (message count)

        Returns:
            self (supports chaining)
        """
        self.context_window_limit = limit
        return self

    def configure_mem_scope(self, mem_scope_id: str) -> 'ReActAgentConfig':
        """Configure memory scope ID

        Args:
            mem_scope_id: Memory scope ID

        Returns:
            self (supports chaining)
        """
        self.mem_scope_id = mem_scope_id
        return self

    def configure_max_iterations(self, max_iterations: int) -> 'ReActAgentConfig':
        """Configure maximum iterations

        Args:
            max_iterations: Maximum number of ReAct loop iterations

        Returns:
            self (supports chaining)
        """
        self.max_iterations = max_iterations
        return self


class ReActAgent(BaseAgent):
    """ReAct paradigm Agent implementation
    ReAct loop: Reasoning -> Acting -> Observation -> Repeat

    Input format (compatible with legacy):
        {"query": "user question", "conversation_id": "session_123"}

    Output format (compatible with legacy):
        invoke: {"output": "response content", "result_type": "answer|error"}
        stream: yields OutputSchema objects
    """

    def __init__(
            self,
            card: AgentCard,
    ):
        """Initialize ReActAgent

        Args:
            card: Agent card (required)
        """
        self.config = self._create_default_config()
        self.context_engine = ContextEngine(
            ContextEngineConfig(
                default_window_message_num=self.config.context_window_limit
            )
        )
        self._llm = None
        self._init_memory_scope()
        super().__init__(card)

    def _init_memory_scope(self) -> None:
        """Initialize memory scope (subclass can override configuration)"""
        if self.config.mem_scope_id:
            LongTermMemory().set_scope_config(
                self.config.mem_scope_id,
                MemoryScopeConfig()
            )

    def _create_default_config(self) -> ReActAgentConfig:
        """Create default configuration"""
        return ReActAgentConfig()

    def configure(self, config: ReActAgentConfig) -> 'BaseAgent':
        """Set configuration

        Args:
            config: ReActAgentConfig configuration object

        Returns:
            self (supports chaining)

        Note:
            After config update, context_engine and memory_scope
            will be updated accordingly
        """
        old_config = self.config
        self.config = config

        # Reset LLM if model config changed
        if (old_config.model_client_config != config.model_client_config or
                old_config.model_config_obj != config.model_config_obj):
            self._llm = None

        # Update context_engine if context window limit changed
        if old_config.context_window_limit != config.context_window_limit:
            self.context_engine = ContextEngine(
                ContextEngineConfig(
                    default_window_message_num=config.context_window_limit
                )
            )

        # Update memory_scope if memory scope ID changed
        if old_config.mem_scope_id != config.mem_scope_id:
            self._init_memory_scope()

        return self

    def _get_llm(self) -> Model:
        """Get LLM instance (lazy initialization)

        Returns:
            Model instance

        Raises:
            ValueError: If model_client_config is not configured
        """
        if self._llm is None:
            if self.config.model_client_config is None:
                raise ValueError(
                    "model_client_config is required. "
                    "Use configure_model_client() to set it."
                )
            self._llm = Model(
                model_client_config=self.config.model_client_config,
                model_config=self.config.model_config_obj
            )
        return self._llm

    async def _call_llm(
        self,
        messages: List,
        tools: Optional[List[ToolInfo]] = None
    ) -> AssistantMessage:
        """Call LLM with messages and optional tools

        Args:
            messages: Message list (BaseMessage or dict)
            tools: Optional tool definitions (List[ToolInfo])

        Returns:
            AssistantMessage from LLM
        """
        llm = self._get_llm()
        return await llm.ainvoke(
            messages=messages,
            tools=tools
        )

    async def invoke(
            self,
            inputs: Any,
            session: Optional[Session] = None
    ) -> Dict[str, Any]:
        """Execute ReAct process

        Args:
            inputs: User input, supports the following formats:
                - dict: {"query": "...", "conversation_id": "..."}
                - str: Used directly as query
            session: Session object (optional)

        Returns:
            Dict with output and result_type
        """
        # Normalize inputs
        if isinstance(inputs, dict):
            user_input = inputs.get("query")
            if user_input is None:
                raise ValueError("Input dict must contain 'query'")
        elif isinstance(inputs, str):
            user_input = inputs
        else:
            raise ValueError("Input must be dict with 'query' or str")

        # Create session if not provided
        if session is None:
            from openjiuwen.core.session.session import Session as SessionImpl
            session = SessionImpl()

        # Get or create model context
        context = await self.context_engine.create_context(session=session)

        # Add user message to context (convert to legacy type for context_engine)
        await context.add_messages(LegacyHumanMessage(content=user_input))

        # Build system messages from prompt template
        # Convert to legacy type for context_engine compatibility
        system_messages = [
            LegacyBaseMessage(role=msg.role, content=msg.content)
            for msg in self.config.prompt_template
            if msg.role == "system"
        ]

        # Get tool info from _ability_kit
        tools = self.list_tool_info()

        # ReAct loop
        for iteration in range(self.config.max_iterations):
            logger.info(
                f"ReAct iteration {iteration + 1}/{self.config.max_iterations}"
            )

            # Get context window with system messages and tools
            context_window = await context.get_context_window(
                system_messages=system_messages,
                tools=tools if tools else None,
                window_size=self.config.context_window_limit
            )

            # Call LLM with messages and tools from context window
            ai_message = await self._call_llm(
                context_window.get_messages(),
                context_window.get_tools() or None
            )

            # Convert AssistantMessage to legacy AIMessage for context storage
            # (to be removed after context_engine adapts to llm1)
            ai_msg_for_context = LegacyAIMessage(
                content=ai_message.content,
                tool_calls=ai_message.tool_calls
            )
            await context.add_messages(ai_msg_for_context)

            # Check for tool calls
            if ai_message.tool_calls:
                # Log tool calls
                for tool_call in ai_message.tool_calls:
                    logger.info(
                        f"Executing tool: {tool_call.name} "
                        f"with args: {tool_call.arguments}"
                    )

                # Execute tools using _execute_ability (supports parallel)
                # llm1.ToolCall is duck-type compatible with foundation.tool.ToolCall
                results = await self._execute_ability(
                    ai_message.tool_calls, session
                )

                # Process results and add tool messages to context
                for (result, tool_msg) in results:
                    logger.info(f"Tool result: {result}")
                    await context.add_messages(tool_msg)
            else:
                # No tool calls, return AI response
                return {
                    "output": ai_message.content,
                    "result_type": "answer"
                }

        # Max iterations reached
        return {
            "output": "Max iterations reached without completion",
            "result_type": "error"
        }

    async def stream(
            self,
            inputs: Any,
            session: Optional[Session] = None,
            stream_modes: Optional[List[StreamMode]] = None
    ) -> AsyncIterator[Any]:
        """Stream execute ReAct process

        Args:
            inputs: User input, supports the following formats:
                - dict (legacy): {"query": "...", "conversation_id": "..."}
                - dict (new): {"user_input": "...", "session_id": "..."}
                - str: Used directly as user_input
            session: Session object (optional)
            stream_modes: Stream output modes (optional)

        Yields:
            Legacy compatible format - OutputSchema objects or final result dict
        """
        # Determine if we own the stream
        own_stream = session is None

        # Store final result for yielding
        final_result_holder = {"result": None}

        async def stream_process():
            try:
                final_result = await self.invoke(inputs, session)
                final_result_holder["result"] = final_result
                # Write to session stream if available
                if session is not None and hasattr(session, 'write_stream'):
                    await session.write_stream(OutputSchema(
                        type="answer",
                        index=0,
                        payload={
                            "output": final_result,
                            "result_type": "answer"
                        }
                    ))
            except Exception as e:
                logger.error(f"ReActAgent stream error: {e}")
                final_result_holder["result"] = {
                    "output": str(e),
                    "result_type": "error"
                }

        task = asyncio.create_task(stream_process())

        # If we own's stream, read from session's stream iterator
        if own_stream and session is not None and hasattr(session, 'stream_iterator'):
            async for result in session.stream_iterator():
                yield result

        await task

        # Yield final result
        if final_result_holder["result"] is not None:
            yield final_result_holder["result"]


__all__ = [
    "ReActAgent",
    "ReActAgentConfig",
]
