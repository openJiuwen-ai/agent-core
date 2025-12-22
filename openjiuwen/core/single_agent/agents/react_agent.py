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
from openjiuwen.core.common.utils.message_utils import MessageUtils
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import AIMessage, ModelFactory
from openjiuwen.core.foundation.prompt import PromptTemplate
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
    model_name: str = Field(default="", description="Model name")
    model_provider: str = Field(default="openai", description="Model provider")
    api_key: str = Field(default="", description="API key")
    api_base: str = Field(default="", description="API base URL")
    prompt_template_name: str = Field(default="", description="Prompt template name")
    prompt_template: List[Dict] = Field(
        default_factory=list,
        description="Prompt template list"
    )
    context_window_limit: int = Field(default=20, description="Context window limit")
    max_iterations: int = Field(default=5, description="Maximum iterations")

    def configure_model(self, model_name: str) -> 'ReActAgentConfig':
        """Configure model name

        Args:
            model_name: Model name

        Returns:
            self (supports chaining)
        """
        self.model_name = model_name
        return self

    def configure_model_provider(
            self,
            provider: str,
            api_key: str,
            api_base: str
    ) -> 'ReActAgentConfig':
        """Configure model provider details

        Args:
            provider: Model provider name (e.g., "openai")
            api_key: API key
            api_base: API base URL

        Returns:
            self (supports chaining)
        """
        self.model_provider = provider
        self.api_key = api_key
        self.api_base = api_base
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
            prompt_template: List[Dict]
    ) -> 'ReActAgentConfig':
        """Configure prompt template directly

        Args:
            prompt_template: Prompt template list, format like
                [{"role": "system", "content": "..."}]

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
        if (old_config.model_provider != config.model_provider or
                old_config.api_key != config.api_key or
                old_config.api_base != config.api_base):
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

    def _get_llm(self):
        """Get LLM instance (lazy initialization)"""
        if self._llm is None:
            self._llm = ModelFactory().get_model(
                model_provider=self.config.model_provider,
                api_key=self.config.api_key,
                api_base=self.config.api_base,
                model_name=self.config.model_name
            )
        return self._llm

    async def _call_llm(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None
    ) -> AIMessage:
        """Call LLM with messages and optional tools

        Args:
            messages: Message list
            tools: Optional tool definitions

        Returns:
            AI message from LLM
        """
        llm = self._get_llm()
        return await llm.ainvoke(
            model_name=self.config.model_name,
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
            inputs: User input, supports to following formats:
                - dict (legacy): {"query": "...", "conversation_id": "..."}
                - dict (new): {"user_input": "...", "session_id": "..."}
                - str: Used directly as user_input
            session: Session object (optional)

        Returns:
            Dict with output and result_type
        """
        # Normalize inputs
        if isinstance(inputs, dict):
            if "query" in inputs:
                user_input = inputs["query"]
            elif "user_input" in inputs:
                user_input = inputs["user_input"]
            else:
                raise ValueError(
                    "Input dict must contain either 'query' or 'user_input'"
                )
        elif isinstance(inputs, str):
            user_input = inputs
        else:
            raise ValueError(
                "Input must be dict (with 'query' or 'user_input') or str"
            )

        # Create session if not provided
        if session is None:
            from openjiuwen.core.session.session import Session as SessionImpl
            session = SessionImpl()

        # Add user message
        await MessageUtils.add_user_message(user_input, self.context_engine, session)

        # ReAct loop
        for iteration in range(self.config.max_iterations):
            logger.info(f"ReAct iteration {iteration + 1}/{self.config.max_iterations}")

            # Get chat history
            messages = MessageUtils.get_chat_history(
                self.context_engine, session,
                self.config.context_window_limit
            )

            # Convert to message dicts
            message_dicts = []
            for msg in messages:
                if hasattr(msg, 'model_dump'):
                    msg_dict = msg.model_dump(exclude_none=True)
                elif hasattr(msg, 'dict'):
                    msg_dict = msg.dict(exclude_none=True)
                else:
                    msg_dict = msg
                message_dicts.append(msg_dict)

            # Add system prompt
            if self.config.prompt_template:
                system_prompt = PromptTemplate(
                    content=self.config.prompt_template
                )
                prompt_messages = system_prompt.to_messages()
                if hasattr(prompt_messages[0], 'model_dump'):
                    message_dicts.insert(
                        0,
                        prompt_messages[0].model_dump(exclude_none=True)
                    )
                else:
                    message_dicts.insert(0, prompt_messages[0])

            # Get tool info
            tools = session.get_tool_info()
            tool_dicts = []
            for tool in tools:
                if hasattr(tool, 'model_dump'):
                    tool_dicts.append(tool.model_dump(exclude_none=True))
                elif hasattr(tool, 'dict'):
                    tool_dicts.append(tool.dict(exclude_none=True))
                else:
                    tool_dicts.append(tool)

            # Call LLM
            ai_message = await self._call_llm(message_dicts, tool_dicts or None)

            # Add AI message
            await MessageUtils.add_ai_message(ai_message, self.context_engine, session)

            # Check for tool calls
            if ai_message.tool_calls and len(ai_message.tool_calls) > 0:
                # Execute tools
                for tool_call in ai_message.tool_calls:
                    tool_name = tool_call.name
                    tool_args = tool_call.arguments

                    if isinstance(tool_args, str):
                        import json
                        try:
                            tool_args = json.loads(tool_args)
                        except json.JSONDecodeError:
                            pass

                    logger.info(
                        f"Executing tool: {tool_name} with args: {tool_args}"
                    )

                    # Execute tool
                    result = await session.execute_tool(
                        tool_name,
                        tool_args
                    )

                    logger.info(f"Tool result: {result}")

                    # Add tool message
                    from openjiuwen.core.foundation.llm import ToolMessage
                    tool_msg = ToolMessage(
                        tool_call_id=tool_call.id or "",
                        content=str(result)
                    )
                    await MessageUtils.add_tool_message(
                        tool_msg, self.context_engine, session
                    )
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
