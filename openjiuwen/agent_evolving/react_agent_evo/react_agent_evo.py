# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from pydantic import Field, BaseModel

from openjiuwen.core.common.logging import logger
from openjiuwen.core.operator import Operator, LLMCallOperator, ToolCallOperator, MemoryCallOperator
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    Model,
    UserMessage,
    SystemMessage,
)
from openjiuwen.core.memory import LongTermMemory, MemoryScopeConfig
from openjiuwen.core.session.session import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent import BaseAgent
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
    prompt_template: List[Dict] = Field(default_factory=list, description="Prompt template list")
    context_window_limit: int = Field(default=20, description="Context window limit")
    max_iterations: int = Field(default=5, description="Maximum iterations")

    # LLM configuration objects (for Model initialization)
    model_client_config: Optional[ModelClientConfig] = Field(default=None, description="Model client configuration")
    model_config_obj: Optional[ModelRequestConfig] = Field(default=None, description="Model request configuration")

    sys_operation_id: Optional[str] = None

    def configure_model(self, model_name: str) -> "ReActAgentConfig":
        """Configure model name

        Args:
            model_name: Model name

        Returns:
            self (supports chaining)
        """
        self.model_name = model_name
        return self

    def configure_model_provider(self, provider: str, api_key: str, api_base: str) -> "ReActAgentConfig":
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

    def configure_prompt(self, prompt_name: str) -> "ReActAgentConfig":
        """Configure prompt template name

        Args:
            prompt_name: Prompt template name

        Returns:
            self (supports chaining)
        """
        self.prompt_template_name = prompt_name
        return self

    def configure_prompt_template(self, prompt_template: List[Dict]) -> "ReActAgentConfig":
        """Configure prompt template directly

        Args:
            prompt_template: Prompt template list, format like
                [{"role": "system", "content": "..."}]

        Returns:
            self (supports chaining)
        """
        self.prompt_template = prompt_template
        return self

    def configure_context_limit(self, limit: int) -> "ReActAgentConfig":
        """Configure context window limit

        Args:
            limit: Context window limit (message count)

        Returns:
            self (supports chaining)
        """
        self.context_window_limit = limit
        return self

    def configure_mem_scope(self, mem_scope_id: str) -> "ReActAgentConfig":
        """Configure memory scope ID

        Args:
            mem_scope_id: Memory scope ID

        Returns:
            self (supports chaining)
        """
        self.mem_scope_id = mem_scope_id
        return self

    def configure_max_iterations(self, max_iterations: int) -> "ReActAgentConfig":
        """Configure maximum iterations

        Args:
            max_iterations: Maximum number of ReAct loop iterations

        Returns:
            self (supports chaining)
        """
        self.max_iterations = max_iterations
        return self

    def configure_model_client(
        self, provider: str, api_key: str, api_base: str, model_name: str, verify_ssl: bool = False
    ) -> "ReActAgentConfig":
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
            client_provider=provider, api_key=api_key, api_base=api_base, verify_ssl=verify_ssl
        )
        self.model_config_obj = ModelRequestConfig(model=model_name)
        return self


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
            ContextEngineConfig(default_window_message_num=self.config.context_window_limit)
        )
        self._llm = None
        # Unified naming: *_op indicates evolvable Operator
        # LLM Operator uses lazy init: model_client_config/model_config_obj may only be ready after configure()
        self._llm_op: Optional[LLMCallOperator] = None
        self._tool_op: Optional[ToolCallOperator] = None
        self._memory_op: Optional[MemoryCallOperator] = None
        self._init_memory_scope()
        # Lazy import to avoid circular dependency: skills -> runner -> single_agent -> skills
        from openjiuwen.core.single_agent.skills import SkillUtil

        self._skill_util = SkillUtil(self.config.sys_operation_id)
        super().__init__(card)
        # Operator depends on ability_kit, so placed after BaseAgent initialization
        self._tool_op = ToolCallOperator(
            tool=None,
            tool_call_id="react_tool",
            tool_executor=self.ability_kit.execute,
            tool_registry=self.ability_kit,
        )
        self._memory_op = MemoryCallOperator(
            memory=None,
            memory_call_id="react_memory",
            memory_invoke=self._memory_invoke,
        )

    def _init_memory_scope(self) -> None:
        """Initialize memory scope (subclass can override configuration)"""
        if self.config.mem_scope_id:
            LongTermMemory().set_scope_config(self.config.mem_scope_id, MemoryScopeConfig())

    def _create_default_config(self) -> ReActAgentConfig:
        """Create default configuration"""
        return ReActAgentConfig()

    def configure(self, config: ReActAgentConfig) -> "BaseAgent":
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
        if (
            old_config.model_provider != config.model_provider
            or old_config.api_key != config.api_key
            or old_config.api_base != config.api_base
        ):
            self._llm = None
            self._llm_op = None

        # Update context_engine if context window limit changed
        if old_config.context_window_limit != config.context_window_limit:
            self.context_engine = ContextEngine(
                ContextEngineConfig(default_window_message_num=config.context_window_limit)
            )

        # Update memory_scope if memory scope ID changed
        if old_config.mem_scope_id != config.mem_scope_id:
            self._init_memory_scope()

        # Reset sys operation id if changed
        if old_config.sys_operation_id != config.sys_operation_id:
            self._skill_util.skill_tool_kit.sys_operation_id = config.sys_operation_id

        return self

    @staticmethod
    def _normalize_user_input(inputs: Any) -> str:
        if isinstance(inputs, dict):
            user_input = inputs.get("query")
            if user_input is None:
                raise ValueError("Input dict must contain 'query'")
            return user_input
        if isinstance(inputs, str):
            return inputs
        raise ValueError("Input must be dict with 'query' or str")

    def _on_llm_parameter_updated(self, target: str, value: Any) -> None:
        # Keep AgentConfig aligned with Operator (especially system_prompt)
        if target == "system_prompt":
            if isinstance(value, list):
                content = value
            else:
                content = [{"role": "system", "content": str(value)}]
            self.config.prompt_template = content

    async def _memory_invoke(self, inputs: Dict[str, Any]) -> List[str]:
        """MemoryCallOperator callback: adapts LongTermMemory.search_user_mem.

        Soft fail returns empty when uninitialized.
        """
        query = str(inputs.get("query", ""))
        scope_id = str(inputs.get("scope_id", ""))
        user_id = str(inputs.get("user_id", LongTermMemory.DEFAULT_VALUE))
        top_k = int(inputs.get("top_k", 3))
        if not query or not scope_id:
            return []
        mem = LongTermMemory()
        if getattr(mem, "search_manager", None) is None:
            return []
        results = await mem.search_user_mem(query=query, num=top_k, user_id=user_id, scope_id=scope_id)
        return [r.mem_info.content for r in results if r and r.mem_info and r.mem_info.content]

    def _resolve_llm_model_name(self) -> str:
        """Single source of truth: prefer model_name from ModelRequestConfig.

        Consistent with core Model construction.
        """
        model_name_from_obj = (
            getattr(self.config.model_config_obj, "model_name", None)
            if self.config.model_config_obj is not None
            else None
        )
        model_name_from_field = self.config.model_name
        return model_name_from_obj or model_name_from_field

    def _get_llm_op(self) -> LLMCallOperator:
        """LLMCallOperator for self-evolving (react_llm), syncs back to config.prompt_template via callback."""
        if self._llm_op is None:
            llm = self._get_llm()
            model_name = self._resolve_llm_model_name()
            system_prompt = getattr(self.config, "prompt_template", []) or []
            self._llm_op = LLMCallOperator(
                model_name=model_name,
                llm=llm,
                system_prompt=system_prompt,
                user_prompt="{{query}}",
                freeze_system_prompt=False,
                freeze_user_prompt=True,
                llm_call_id="react_llm",
                on_parameter_updated=self._on_llm_parameter_updated,
            )
        else:
            # prompt_template may change after configure/self-evolving:
            # ensure operator internal view aligns with config
            self._llm_op.update_system_prompt(getattr(self.config, "prompt_template", []))
        return self._llm_op

    async def _get_memory_messages(
        self, *, user_input: str, session: Optional[Session], iteration: int
    ) -> List[SystemMessage]:
        # Memory: once on first round; empty when unconfigured/uninitialized
        if iteration != 0:
            return []
        mem_op = self._memory_op
        if mem_op is None:
            return []
        mem_hits = await mem_op.invoke(
            {"query": user_input, "scope_id": self.config.mem_scope_id, "top_k": 3},
            session=session,
        )
        if not mem_hits:
            return []
        return [SystemMessage(content="Relevant memory:\n" + "\n".join(f"- {m}" for m in mem_hits))]

    def _get_skill_messages(self) -> List[SystemMessage]:
        # Skill prompt: injected as additional system message (not written to evolvable system_prompt)
        if not self._skill_util.has_skill():
            return []
        return [SystemMessage(content=self._skill_util.get_skill_prompt())]

    def get_operators(self) -> Dict[str, Operator]:
        """Returns evolvable operator registry (operator_id -> Operator)."""
        ops: Dict[str, Operator] = {}
        if self._tool_op is not None:
            ops[self._tool_op.operator_id] = self._tool_op
        if self._memory_op is not None:
            ops[self._memory_op.operator_id] = self._memory_op
        try:
            llm_op = self._get_llm_op()
            ops[llm_op.operator_id] = llm_op
        except Exception:
            # Skip llm operator when model_client_config not configured (remains importable/buildable)
            pass
        return ops

    def _get_llm(self) -> Model:
        """Get LLM instance (lazy initialization)

        Returns:
            Model instance

        Raises:
            ValueError: If model configuration is not configured
        """
        if self._llm is None:
            if self.config.model_client_config is None and self.config.model_config_obj is None:
                raise ValueError("model_client_config is required. Use configure_model_client() to set it.")
            self._llm = Model(
                model_client_config=self.config.model_client_config, model_config=self.config.model_config_obj
            )
        return self._llm

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Dict[str, Any]:
        """Execute ReAct process

        Args:
            inputs: User input, supports the following formats:
                - dict: {"query": "...", "conversation_id": "..."}
                - str: Used directly as query
            session: Session object (required for tool execution)

        Returns:
            Dict with output and result_type
        """
        user_input = self._normalize_user_input(inputs)

        # Get or create model context
        context = await self.context_engine.create_context(session=session)

        # Add user message to context (let subsequent iterations see this round's query)
        await context.add_messages(UserMessage(content=user_input))

        # Get tool info from ability kit
        tools = self.list_tool_info()

        # ReAct loop
        for iteration in range(self.config.max_iterations):
            logger.info(f"ReAct iteration {iteration + 1}/{self.config.max_iterations}")

            # Get context window (system_prompt injected by react_llm operator)
            context_window = await context.get_context_window(
                system_messages=[], tools=tools if tools else None, window_size=self.config.context_window_limit
            )

            memory_messages = await self._get_memory_messages(
                user_input=user_input, session=session, iteration=iteration
            )
            skill_messages = self._get_skill_messages()

            # Call LLM via Operator (react_llm)
            llm_op = self._get_llm_op()
            history_messages = context_window.get_messages()
            ai_message = await llm_op.invoke(
                inputs={"query": user_input, "messages": [*memory_messages, *skill_messages, *history_messages]},
                session=session,
                tools=context_window.get_tools() or None,
            )

            # Add AI message to context
            ai_msg_for_context = AssistantMessage(content=ai_message.content, tool_calls=ai_message.tool_calls)
            await context.add_messages(ai_msg_for_context)

            # Check for tool calls
            if ai_message.tool_calls:
                # Log tool calls
                for tool_call in ai_message.tool_calls:
                    logger.info(f"Executing tool: {tool_call.name} with args: {tool_call.arguments}")

                # Execute tools via Operator (react_tool)
                tool_op = self._tool_op
                if tool_op is None:
                    raise RuntimeError("react_tool operator is not initialized")
                results = await tool_op.invoke({"tool_calls": ai_message.tool_calls}, session=session)

                # Process results and add tool messages to context
                for result, tool_msg in results:
                    logger.info(f"Tool result: {result}")
                    await context.add_messages(tool_msg)
            else:
                # No tool calls, return AI response
                return {"output": ai_message.content, "result_type": "answer"}

        # Max iterations reached
        return {"output": "Max iterations reached without completion", "result_type": "error"}

    async def stream(
        self,
        inputs: Any,
        session: Optional[Session] = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        """Stream execute ReAct process

        Args:
            inputs: User input (required in new version)
            session: Session object (required in new version)
            stream_modes: Stream output modes (optional)

        Yields:
            OutputSchema objects from stream_iterator
        """
        _ = stream_modes  # reserved for future streaming modes
        final_result_holder = {"result": None}

        async def stream_process():
            try:
                final_result = await self.invoke(inputs, session)
                final_result_holder["result"] = final_result
                # Write to session stream if available
                if session is not None and hasattr(session, "write_stream"):
                    await session.write_stream(
                        OutputSchema(type="answer", index=0, payload={"output": final_result, "result_type": "answer"})
                    )
            except Exception as e:
                logger.error(f"ReActAgent stream error: {e}")
                final_result_holder["result"] = {"output": str(e), "result_type": "error"}
            finally:
                # Close stream
                if session is not None and hasattr(session, "post_run"):
                    await session.post_run()

        task = asyncio.create_task(stream_process())

        # Read from stream_iterator and yield
        if session is not None and hasattr(session, "stream_iterator"):
            async for result in session.stream_iterator():
                yield result

        await task


__all__ = [
    "ReActAgent",
    "ReActAgentConfig",
]
