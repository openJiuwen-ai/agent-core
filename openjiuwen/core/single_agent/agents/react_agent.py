# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ReActAgent Implementation

ReAct (Reasoning + Acting) paradigm Agent implementation

Created on: 2025-11-25
Author: huenrui1@huawei.com
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional, Union, Tuple

from pydantic import Field, BaseModel

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig
)
from openjiuwen.core.context_engine import (
    ContextEngine,
    ContextEngineConfig,
    ModelContext
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    Model,
    UserMessage,
    SystemMessage
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
    model_name: str = Field(default="", description="Model name")
    model_provider: str = Field(default="openai", description="Model provider")
    api_key: str = Field(default="", description="API key")
    api_base: str = Field(default="", description="API base URL")
    prompt_template_name: str = Field(
        default="",
        description="Prompt template name"
    )
    prompt_template: List[Dict] = Field(
        default_factory=list,
        description="Prompt template list"
    )

    max_iterations: int = Field(default=5, description="Maximum iterations")

    # LLM configuration objects (for Model initialization)
    model_client_config: Optional[ModelClientConfig] = Field(
        default=None,
        description="Model client configuration"
    )
    model_config_obj: Optional[ModelRequestConfig] = Field(
        default=None,
        description="Model request configuration"
    )

    sys_operation_id: Optional[str] = None

    context_engine_config: ContextEngineConfig = Field(
        default=ContextEngineConfig(
            max_context_message_num=200,
            default_window_round_num=10
        ),
        description="Context engine configuration"
    )

    context_processors: List[Tuple[str, BaseModel]] = Field(
        default=None,
        description="Context processors configuration"
    )

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

    def configure_context_engine(
            self,
            max_context_message_num: Optional[int] = 200,
            default_window_round_num: Optional[int] = 10,
            enable_reload: bool = False
    ) -> 'ReActAgentConfig':
        """
        Configure the context-engine parameters that control how conversation history
        is truncated, offloaded and reloaded.

        Parameters
        ----------
        max_context_message_num : int, optional, default 200
            Hard upper bound on the total number of messages kept in the context
            window.  `None` means no hard limit.
        default_window_round_num : int, optional, default 10
            Number of **most-recent conversation rounds** to retain (a round =
            user message → final assistant reply without tool calls).  When set,
            it takes precedence over `default_window_message_num`.  Must be > 0
            if given.
        enable_reload : bool, default False
            Whether the agent is allowed to **automatically reload** messages that
            were previously off-loaded (via hints such as `[[OFFLOAD:...]]`).
            Enable this if you want the model to retrieve long content on demand;
            disable it to keep hints as plain text.
        """
        self.context_engine_config = ContextEngineConfig(
            max_context_message_num=max_context_message_num,
            default_window_round_num=default_window_round_num,
            enable_reload=enable_reload
        )
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

    def configure_max_iterations(
            self,
            max_iterations: int
    ) -> 'ReActAgentConfig':
        """Configure maximum iterations

        Args:
            max_iterations: Maximum number of ReAct loop iterations

        Returns:
            self (supports chaining)
        """
        self.max_iterations = max_iterations
        return self

    def configure_model_client(
            self,
            provider: str,
            api_key: str,
            api_base: str,
            model_name: str,
            verify_ssl: bool = False
    ) -> 'ReActAgentConfig':
        """Configure model client for LLM initialization

        This method creates ModelClientConfig and ModelRequestConfig
        for the Model class initialization.

        Args:
            provider: Model provider name (e.g., "OpenAI", "SiliconFlow")
            api_key: API key
            api_base: API base URL
            model_name: Model name
            verify_ssl: Whether to verify SSL (default False)

        Returns:
            self (supports chaining)
        """
        self.model_provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name

        self.model_client_config = ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=api_base,
            verify_ssl=verify_ssl
        )
        self.model_config_obj = ModelRequestConfig(
            model_name=model_name
        )
        return self

    def configure_context_processors(
            self,
            processors: List[Tuple[str, BaseModel]]
    ):
        self.context_processors = processors


class ReActAgent(BaseAgent):
    """ReAct paradigm Agent implementation
    ReAct loop: Reasoning -> Acting -> Observation -> Repeat

    Input format (compatible with legacy):
        {"query": "user question", "conversation_id": "session_123"}

    Output format (compatible with legacy):
        invoke: {"output": "response content", "result_type": "answer|error"}
        stream: yields OutputSchema objects

    Note:
        This agent currently does not support Runner.run_agent().
        Use agent.invoke() directly with a session parameter.
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
            self.config.context_engine_config
        )
        self._llm = None
        self._init_memory_scope()
        # 延迟导入以避免循环依赖：skills -> runner -> single_agent -> skills
        from openjiuwen.core.skills.skill_util import SkillUtil
        self._skill_util = SkillUtil(self.config.sys_operation_id)
        super().__init__(card)

    def _init_memory_scope(self) -> None:
        """Initialize memory scope (subclass can override configuration)"""
        if self.config.mem_scope_id:
            asyncio.run(
                LongTermMemory().set_scope_config(
                    self.config.mem_scope_id,
                    MemoryScopeConfig()
                )
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
        if old_config.context_engine_config != config.context_engine_config:
            self.context_engine = ContextEngine(
                config.context_engine_config
            )

        # Update memory_scope if memory scope ID changed
        if old_config.mem_scope_id != config.mem_scope_id:
            self._init_memory_scope()

        # Reset sys operation id if changed
        if old_config.sys_operation_id != config.sys_operation_id:
            self._skill_util.set_sys_operation_id(config.sys_operation_id)

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

    async def register_skill(self, skill_path: Union[str, List[str]]):
        """Register a skill"""
        await self._skill_util.register_skills(skill_path, self)

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
        return await llm.invoke(
            model=self.config.model_name,
            messages=messages,
            tools=tools
        )

    async def _init_context(
            self,
            session: Optional[Session]
    ) -> ModelContext:
        if self.config.context_processors:
            from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
            context = await self.context_engine.create_context(
                session=session,
                processors=self.config.context_processors,
                token_counter=TiktokenCounter()
            )
        else:
            context = await self.context_engine.create_context(
                session=session
            )
        context_reloader = context.reloader_tool()
        if self.config.context_engine_config.enable_reload:
            self.add_ability(context_reloader.card)
            from openjiuwen.core.runner import Runner
            if not Runner.resource_mgr.get_tool(context_reloader.card.id):
                Runner.resource_mgr.add_tool(context_reloader)
        else:
            self.remove_ability(context_reloader.card.name)
        return context

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
            session: Session object (required for tool execution)

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

        # Get or create model context
        context = await self._init_context(session)

        # Add user message to context
        await context.add_messages(UserMessage(content=user_input))

        # Build system messages from prompt template
        # prompt_template is List[Dict], access via dict keys
        system_messages = [
            SystemMessage(role=msg["role"], content=msg["content"])
            for msg in self.config.prompt_template
            if msg.get("role") == "system"
        ]

        if len(system_messages) > 0 and self._skill_util.has_skill():
            skill_prompt = self._skill_util.get_skill_prompt()
            last_msg = system_messages[-1]
            last_msg.content = (last_msg.content or "") + "\n" + skill_prompt

        # Get tool info from _ability_manager
        tools = await self.list_tool_info()

        # ReAct loop
        for iteration in range(self.config.max_iterations):
            logger.info(
                f"ReAct iteration {iteration + 1}/{self.config.max_iterations}"
            )

            # Get context window with system messages and tools
            context_window = await context.get_context_window(
                system_messages=system_messages,
                tools=tools if tools else None,
            )

            # Call LLM with messages and tools from context window
            ai_message = await self._call_llm(
                context_window.get_messages(),
                context_window.get_tools() or None
            )

            # Add AI message to context
            ai_msg_for_context = AssistantMessage(
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
            inputs: User input (required in new version)
            session: Session object (required in new version)
            stream_modes: Stream output modes (optional)

        Yields:
            OutputSchema objects from stream_iterator
        """
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
            finally:
                # Close stream
                if session is not None and hasattr(session, 'post_run'):
                    await session.post_run()

        task = asyncio.create_task(stream_process())

        # Read from stream_iterator and yield
        if session is not None and hasattr(session, 'stream_iterator'):
            async for result in session.stream_iterator():
                yield result

        await task


__all__ = [
    "ReActAgent",
    "ReActAgentConfig",
]
