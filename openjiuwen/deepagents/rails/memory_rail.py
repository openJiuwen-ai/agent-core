# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MemoryRail implementation for DeepAgent."""

from __future__ import annotations

from typing import Set

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.core.memory.lite.memory_tools import (
    get_decorated_tools,
    init_memory_manager_async,
)
from openjiuwen.deepagents.prompts.sections.memory import build_memory_section
from openjiuwen.deepagents.rails.base import DeepAgentRail


class MemoryRail(DeepAgentRail):
    """Rail that integrates memory tools and injects memory usage prompts.

    This rail:
    1. Registers memory-related tools to the agent's ability_manager
    2. Injects a memory usage prompt into the system prompt via PromptSection
    3. Initializes and manages the memory index manager

    Configuration:
    - embedding_config: EmbeddingConfig for embedding API (required, passed via constructor)

    Attributes:
        priority: Execution priority (80 = medium-high).
        _initialized: Flag indicating if the rail has been initialized.
        _owned_tool_names: Set of tool names owned by this rail.
        _manager_initialized: Flag indicating if memory manager is initialized.
        _embedding_config: EmbeddingConfig for embedding API.
        _language: Language for prompt ('cn' or 'en').
    """

    priority = 80

    def __init__(
        self,
        embedding_config: EmbeddingConfig,
        language: str = "cn",
    ):
        """Initialize MemoryRail.

        Args:
            embedding_config: EmbeddingConfig for embedding API (required).
            language: Language for prompts ('cn' or 'en').
        """
        super().__init__()
        self._initialized = False
        self._owned_tool_names: Set[str] = set()
        self._owned_tool_ids: Set[str] = set()
        self._manager_initialized = False
        self._embedding_config = embedding_config
        self._language = language
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        """Initialize the rail.

        Args:
            agent: DeepAgent instance.
        """
        super().init(agent)
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._register_memory_tools(agent)

    def uninit(self, agent) -> None:
        """Clean up the rail resources."""
        if hasattr(agent, "ability_manager"):
            for tool_name in list(self._owned_tool_names):
                try:
                    agent.ability_manager.remove(tool_name)
                except Exception as exc:
                    logger.warning(
                        f"[MemoryRail] Failed to remove tool '{tool_name}' "
                        f"from ability_manager: {exc}"
                    )
        for tool_id in list(self._owned_tool_ids):
            try:
                Runner.resource_mgr.remove_tool(tool_id)
            except Exception as exc:
                f"[MemoryRail] Failed to remove tool '{tool_id}' "
                f"from resource_mgr: {exc}"
        self._owned_tool_ids.clear()

        self._owned_tool_names.clear()
        self._initialized = False
        self._manager_initialized = False
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("memory")
            self.system_prompt_builder = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Initialize memory manager and register tools on first invoke.

        Args:
            ctx: Agent callback context.
        """
        if not self._initialized:
            await self._init_memory_manager(ctx)
            self._initialized = True

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Update system_prompt_builder with memory section before model call.

        Args:
            ctx: Agent callback context.
        """
        if self.system_prompt_builder is None:
            return

        self.system_prompt_builder.remove_section("memory")
        is_read_only = isinstance(ctx.inputs, InvokeInputs) and (ctx.inputs.is_cron() or ctx.inputs.is_heartbeat())
        memory_section = build_memory_section(
            language=self._language,
            read_only=is_read_only
        )
        if memory_section is not None:
            self.system_prompt_builder.add_section(memory_section)

    async def _init_memory_manager(self, ctx: AgentCallbackContext) -> None:
        """Initialize the memory index manager.

        Args:
            ctx: Agent callback context.
        """
        agent_id = "default"

        try:
            if hasattr(ctx.agent, "card") and ctx.agent.card:
                agent_id = getattr(ctx.agent.card, "id", "default")

            manager = await init_memory_manager_async(
                workspace=self.workspace,
                agent_id=agent_id,
                embedding_config=self._embedding_config,
                sys_operation=self.sys_operation,
            )

            if manager:
                self._manager_initialized = True
                logger.info(
                    f"[MemoryRail] Memory manager initialized: "
                    f"agent_id={agent_id}"
                )
            else:
                logger.warning("[MemoryRail] Memory manager initialization failed")

        except Exception as e:
            logger.error(f"[MemoryRail] Failed to initialize memory manager: {e}")

    def _register_memory_tools(self, agent) -> None:
        """Register memory tools to the agent's ability manager.

        Args:
            agent: DeepAgent instance.
        """
        if not hasattr(agent, "ability_manager"):
            logger.warning("[MemoryRail] Agent has no ability_manager")
            return

        try:
            memory_tools = get_decorated_tools()

            for tool in memory_tools:
                try:
                    tool_card = getattr(tool, "card", None)
                    if not tool_card:
                        logger.warning(f"[MemoryRail] Tool {tool.__name__} has no card")
                        continue

                    existing_tool = Runner.resource_mgr.get_tool(tool_card.id)
                    if existing_tool is None:
                        Runner.resource_mgr.add_tool(tool)
                        self._owned_tool_ids.add(tool_card.id)

                    result = agent.ability_manager.add(tool_card)
                    if result.added:
                        self._owned_tool_names.add(tool_card.name)
                        logger.info(f"[MemoryRail] Registered tool: {tool_card.name}")

                except Exception as exc:
                    logger.warning(
                        f"[MemoryRail] Failed to register tool {tool.__name__}: {exc}"
                    )

        except Exception as e:
            logger.error(f"[MemoryRail] Failed to register memory tools: {e}")


__all__ = [
    "MemoryRail",
]
