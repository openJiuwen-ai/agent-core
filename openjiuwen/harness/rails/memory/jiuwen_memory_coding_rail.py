# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Document memory for Coding agents; the host supplies the provider and identity."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.memory.external.provider import MemoryProvider
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.memory.jiuwen_memory_rail import JiuwenMemoryRail

_STATIC_SECTION = "jiuwen_coding_memory"
_CONTEXT_SECTION = "jiuwen_coding_memory_context"
_SOURCE = "agent_core.jiuwen_memory_coding_rail"


@dataclass
class _Owner:
    agent: Any
    tools: dict[str, ToolCard] = field(default_factory=dict)
    context: str | None = None


class JiuwenMemoryCodingRail(DeepAgentRail):
    """Share one provider across a main agent and its coding child.

    Prompt/tool ownership and recall caches are kept per agent. The host binds
    the project's provider; tools cannot select another project. Call ``drain``
    to await pending writes; ``wait_closed`` waits for cleanup after unregister.
    """

    priority = 75
    PREFETCH_TIMEOUT = 6.0
    SYNC_TIMEOUT = 130.0

    def __init__(
        self, provider: MemoryProvider, *, user_id: str = "__default__",
        scope_id: str = "__default__", agent_id: str = "jiuwenswarm",
        session_id: str = "",
    ) -> None:
        super().__init__()
        self._provider = provider
        self._identity = dict(user_id=user_id, scope_id=scope_id,
                              agent_id=agent_id, session_id=session_id)
        self._owners: dict[int, _Owner] = {}
        self._initialized = False
        self._closed = False
        self._source = f"{_SOURCE}.{id(self):x}"
        self._static_section = f"{_STATIC_SECTION}.{id(self):x}"
        self._context_section = f"{_CONTEXT_SECTION}.{id(self):x}"
        self._init_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._writes: set[asyncio.Task] = set()
        self._cleanup: set[asyncio.Task] = set()

    def init(self, agent: Any) -> None:
        if id(agent) in self._owners:
            return
        owner = _Owner(agent)
        self._owners[id(agent)] = owner
        try:
            available = self._provider.is_available()
        except Exception:
            available = False
        if not available:
            logger.warning("[JiuwenMemoryCodingRail] provider unavailable; memory disabled")
            return
        try:
            self._register_tools(owner)
            builder = getattr(agent, "system_prompt_builder", None)
            if builder is not None:
                language = getattr(builder, "language", "cn")
                builder.add_section(PromptSection(
                    name=self._static_section,
                    content={language: self._provider.system_prompt_block()},
                    priority=55, category="memory",
                ))
        except Exception as exc:
            logger.warning("[JiuwenMemoryCodingRail] registration failed: %s", type(exc).__name__)
            self.uninit(agent)

    def _register_tools(self, owner: _Owner) -> None:
        for schema in self._provider.get_tool_schemas():
            name = schema.get("name") if isinstance(schema, dict) else None
            if not isinstance(name, str) or not name.strip():
                logger.warning("[JiuwenMemoryCodingRail] skipping tool schema without a name")
                continue
            card = ToolCard(id=f"jiuwen_coding_{name}", name=name,
                            description=schema.get("description", ""),
                            input_params=schema.get("parameters", {}))

            async def call(_name=name, _owner=owner, **kwargs):
                if id(_owner.agent) not in self._owners or not await self._initialize():
                    return json.dumps({"error": "Code memory unavailable"})
                return await self._provider.handle_tool_call(_name, kwargs)

            result = owner.agent.ability_manager.add_ability(card, LocalFunction(card=card, func=call))
            if result.added:
                owner.tools[name] = card

    def _owner(self, ctx: Any) -> _Owner | None:
        agent = getattr(ctx, "agent", None)
        owner = self._owners.get(id(agent))
        if owner is not None:
            return owner
        # Model callbacks run on DeepAgent's inner ReActAgent.
        for candidate in self._owners.values():
            if agent is not None and agent is getattr(candidate.agent, "_react_agent", None):
                return candidate
        return None

    async def _initialize(self) -> bool:
        async with self._init_lock:
            if not self._owners:
                return False
            if self._initialized:
                return True
            try:
                if not self._provider.is_available():
                    return False
                await self._provider.initialize(**self._identity)
                self._initialized = True
                self._closed = False
            except Exception as exc:
                logger.warning("[JiuwenMemoryCodingRail] initialize failed: %s", type(exc).__name__)
            return self._initialized

    async def before_invoke(self, ctx: Any) -> None:
        owner = self._owner(ctx)
        if owner is None:
            return
        owner.context = None
        if await self._initialize():
            await self.before_model_call(ctx)

    async def before_model_call(self, ctx: Any) -> None:
        owner = self._owner(ctx)
        if owner is None or not self._initialized:
            return
        if owner.context is None:
            query = JiuwenMemoryRail.resolve_user_text_for_memory(ctx)
            try:
                owner.context = await asyncio.wait_for(
                    self._provider.prefetch(
                        query, top_k=5,
                        background=JiuwenMemoryRail.is_background_run(ctx),
                        **self._identity,
                    ), timeout=self.PREFETCH_TIMEOUT,
                ) or ""
            except Exception as exc:
                logger.warning("[JiuwenMemoryCodingRail] recall failed: %s", type(exc).__name__)
                owner.context = ""
        manager = getattr(owner.agent, "prompt_attachment_manager", None)
        if manager is None:
            return
        try:
            writer = manager.bind_context(ctx)
            if owner.context:
                await writer.add_section(
                    section=self._context_section,
                    content=JiuwenMemoryRail.build_memory_context_block(owner.context),
                    kind=PromptAttachmentKind.MEMORY, source=self._source, priority=55,
                    content_kind="text/markdown",
                )
            else:
                await writer.clear_section(self._context_section)
        except ValueError as exc:
            logger.warning("[JiuwenMemoryCodingRail] attachment unavailable: %s", exc)

    async def after_invoke(self, ctx: Any) -> None:
        if self._owner(ctx) is None or not self._initialized:
            return
        if JiuwenMemoryRail.is_background_run(ctx):
            return
        query = JiuwenMemoryRail.resolve_user_text_for_memory(ctx)
        output = JiuwenMemoryRail.extract_assistant_output(ctx)
        if not query or not output:
            return

        async def sync() -> None:
            # One lock for all owners: a slow turn never overlaps the next write.
            async with self._write_lock:
                try:
                    await asyncio.wait_for(
                        self._provider.sync_turn(query, output, **self._identity),
                        timeout=self.SYNC_TIMEOUT,
                    )
                except Exception as exc:
                    logger.warning("[JiuwenMemoryCodingRail] sync failed: %s", type(exc).__name__)

        task = asyncio.create_task(sync())
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def drain(self) -> None:
        """Wait for writes already submitted by any owner."""
        if self._writes:
            await asyncio.gather(*tuple(self._writes), return_exceptions=True)

    def uninit(self, agent: Any) -> None:
        owner = self._owners.pop(id(agent), None)
        if owner is None:
            return
        for name, card in owner.tools.items():
            try:
                if agent.ability_manager.get(name) is card:
                    agent.ability_manager.remove_ability(name)
            except Exception as exc:
                logger.warning("[JiuwenMemoryCodingRail] tool cleanup failed: %s", type(exc).__name__)
        builder = getattr(agent, "system_prompt_builder", None)
        if builder is not None:
            try:
                builder.remove_section(self._static_section)
            except Exception as exc:
                logger.warning("[JiuwenMemoryCodingRail] prompt cleanup failed: %s", type(exc).__name__)

        async def cleanup() -> None:
            manager = getattr(agent, "prompt_attachment_manager", None)
            if manager is not None and id(agent) not in self._owners:
                try:
                    await manager.clear_source(source=self._source)
                except Exception as exc:
                    logger.warning(
                        "[JiuwenMemoryCodingRail] attachment cleanup failed: %s", type(exc).__name__,
                    )
            if self._owners:
                return
            await self.drain()
            async with self._init_lock:
                # A hot reload may have mounted this same instance again.
                if self._owners or self._closed:
                    return
                try:
                    await asyncio.wait_for(self._provider.shutdown(), timeout=10.0)
                except Exception as exc:
                    logger.warning("[JiuwenMemoryCodingRail] shutdown failed: %s", type(exc).__name__)
                finally:
                    self._initialized = False
                    self._closed = True

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(cleanup())
        else:
            task = loop.create_task(cleanup())
            self._cleanup.add(task)
            task.add_done_callback(self._cleanup.discard)

    @property
    def attached_agents(self) -> tuple[Any, ...]:
        """Owners currently sharing this provider (for host config transitions)."""
        return tuple(owner.agent for owner in self._owners.values())

    async def aclose(self) -> None:
        """Detach all owners and drain writes before replacing this provider."""
        for agent in self.attached_agents:
            unregister = getattr(agent, "unregister_rail", None)
            if callable(unregister):
                await unregister(self)
            self.uninit(agent)
        await self.wait_closed()

    async def wait_closed(self) -> None:
        """Await scheduled cleanup without blocking the event-loop thread."""
        if self._cleanup:
            await asyncio.gather(*tuple(self._cleanup), return_exceptions=True)
