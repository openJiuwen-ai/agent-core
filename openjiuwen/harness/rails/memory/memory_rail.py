# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MemoryRail implementation for DeepAgent."""

from __future__ import annotations

from typing import Set

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.memory.lite.config import create_memory_settings
from openjiuwen.core.memory.lite.memory_tool_context import MemoryToolContext
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs
from openjiuwen.core.memory.lite.memory_tools import (
    init_memory_manager_async,
)
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentKind,
    PromptAttachmentScope,
)
from openjiuwen.harness.prompts.sections.memory import build_memory_section
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.memory import create_memory_tools


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
        is_proactive: bool = True,
    ):
        """Initialize MemoryRail.

        Args:
            embedding_config: EmbeddingConfig for embedding API (required).
        """
        super().__init__()
        self._initialized = False
        self._owned_tool_names: Set[str] = set()
        self._manager_initialized = False
        self._embedding_config = embedding_config
        self._is_proactive = is_proactive
        self.system_prompt_builder = None
        self.attachment_manager = None
        self._tool_ctx: MemoryToolContext | None = None
        self._is_read_only = False

    def init(self, agent) -> None:
        """Initialize the rail.

        Args:
            agent: DeepAgent instance.
        """
        super().init(agent)
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)
        self._register_memory_tools(agent)

    def uninit(self, agent) -> None:
        """Clean up the rail resources."""
        if hasattr(agent, "ability_manager"):
            for tool_name in list(self._owned_tool_names):
                try:
                    agent.ability_manager.remove_ability(tool_name)
                except Exception as exc:
                    logger.warning(
                        f"[MemoryRail] Failed to remove tool '{tool_name}' "
                        f"from ability_manager: {exc}"
                    )
        self._owned_tool_names.clear()
        self._initialized = False
        self._manager_initialized = False
        self._tool_ctx = None
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("memory")
            self.system_prompt_builder = None
        self.attachment_manager = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Initialize memory manager and register tools on first invoke.

        Args:
            ctx: Agent callback context.
        """
        if not self._initialized:
            await self._init_memory_manager(ctx)
            self._initialized = True
        self._is_read_only = isinstance(ctx.inputs, InvokeInputs) and (
                    ctx.inputs.is_cron() or ctx.inputs.is_heartbeat())

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Update system_prompt_builder with memory section before model call.

        Args:
            ctx: Agent callback context.
        """
        if self.system_prompt_builder is None:
            return

        self.system_prompt_builder.remove_section("memory")
        memory_section = build_memory_section(
            language=self.system_prompt_builder.language,
            read_only=self._is_read_only,
            is_proactive=self._is_proactive
        )
        if memory_section is None:
            return

        if not self._is_read_only:
            self.system_prompt_builder.add_section(memory_section)
            if self.attachment_manager is not None:
                try:
                    await self.attachment_manager.for_context(ctx).clear_section(
                        section="memory",
                        scope=PromptAttachmentScope.TURN,
                    )
                except ValueError as exc:
                    logger.warning("[MemoryRail] skip clearing memory prompt attachment: %s", exc)
            return

        if self.attachment_manager is None:
            self.system_prompt_builder.add_section(memory_section)
            return
        writer = self.attachment_manager.for_context(ctx)
        try:
            await writer.upsert_from_section(
                section=memory_section,
                scope=PromptAttachmentScope.TURN,
                kind=PromptAttachmentKind.MEMORY,
                source="agent_core.memory.policy",
                language=self.system_prompt_builder.language,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            logger.warning("[MemoryRail] skip prompt attachment section=%s: %s", memory_section.name, exc)

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
                if self._tool_ctx is not None:
                    self._tool_ctx.manager = manager
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
            agent_id = getattr(getattr(agent, "card", None), "id", None) or "default"
            language = getattr(self.system_prompt_builder, "language", "cn")

            memory_dir = str(self.workspace.get_node_path("memory") or "") if self.workspace else ""
            settings = create_memory_settings(memory_dir)
            self._tool_ctx = MemoryToolContext(
                workspace=self.workspace,
                settings=settings,
                agent_id=agent_id,
                embedding_config=self._embedding_config,
                sys_operation=self.sys_operation,
                manager=None,
                node_name="memory",
            )

            memory_tools = create_memory_tools(self._tool_ctx, language=language, agent_id=agent_id)

            for tool in memory_tools:
                try:
                    tool_card = getattr(tool, "card", None)
                    if not tool_card:
                        logger.warning("[MemoryRail] Tool has no card")
                        continue

                    result = agent.ability_manager.add_ability(tool_card, tool)
                    if result.added:
                        self._owned_tool_names.add(tool_card.name)
                        logger.info(f"[MemoryRail] Registered tool: {tool_card.name}")

                except Exception as exc:
                    logger.warning(
                        f"[MemoryRail] Failed to register tool: {exc}"
                    )

        except Exception as e:
            logger.error(f"[MemoryRail] Failed to register memory tools: {e}")


__all__ = [
    "MemoryRail",
]
