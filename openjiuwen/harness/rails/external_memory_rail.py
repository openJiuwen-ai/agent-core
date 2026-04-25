# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ExternalMemoryRail — external memory adapter."""

import asyncio
import json
import time
from typing import Any, Optional, Set

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.memory.external.provider import MemoryProvider
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.external_memory import (
    build_external_memory_section,
)

EXTERNAL_MEMORY_PREFETCH_SECTION = "external_memory_prefetch"

_SYNC_BREAKER_THRESHOLD = 5
_SYNC_BREAKER_COOLDOWN = 120.0


class ExternalMemoryRail(DeepAgentRail):
    """ExternalMemoryRail — external memory adapter.
    """
    
    priority = 75
    PREFETCH_TIMEOUT = 5.0
    
    def __init__(
        self,
        provider: MemoryProvider,
        *,
        user_id: str = "__default__",
        scope_id: str = "__default__",
        session_id: str = "__default__",
    ):
        super().__init__()
        self._provider = provider
        self._user_id = user_id
        self._scope_id = scope_id
        self._session_id = session_id
        self._initialized = False
        self._owned_tool_names: Set[str] = set()
        self._owned_tool_ids: Set[str] = set()
        self.system_prompt_builder = None
        # Per-invoke prefetch cache
        self._prefetch_cache: Optional[str] = None
        self._prefetch_invoke_id: Optional[int] = None
        # Serialized sync_turn + circuit breaker
        self._sync_task: Optional[asyncio.Task] = None
        self._sync_consecutive_failures: int = 0
        self._sync_breaker_until: float = 0.0
    
    def init(self, agent) -> None:
        super().init(agent)
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._register_provider_tools(agent)
        # Inject provider's static system prompt block
        if self.system_prompt_builder:
            prompt_block = self._provider.system_prompt_block()
            if prompt_block:
                lang = getattr(self.system_prompt_builder, "language", "cn")
                section = build_external_memory_section(prompt_block, language=lang)
                if section:
                    self.system_prompt_builder.add_section(section)
    
    def uninit(self, agent) -> None:
        # Unregister tools owned by this rail from the agent
        if hasattr(agent, "ability_manager"):
            for tool_name in list(self._owned_tool_names):
                try:
                    agent.ability_manager.remove(tool_name)
                except Exception as exc:
                    logger.warning(f"[ExternalMemoryRail] remove tool '{tool_name}' failed: {exc}")
        for tool_id in list(self._owned_tool_ids):
            try:
                Runner.resource_mgr.remove_tool(tool_id)
            except Exception as exc:
                logger.warning(
                    f"[ExternalMemoryRail] Failed to remove tool '{tool_id}' "
                    f"from resource_mgr: {exc}"
                )
        self._owned_tool_ids.clear()
        self._owned_tool_names.clear()
        
        # Remove prompt sections
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.EXTERNAL_MEMORY)
            self.system_prompt_builder.remove_section(EXTERNAL_MEMORY_PREFETCH_SECTION)
            self.system_prompt_builder = None
        
        # Async shutdown via LspRail pattern
        try:
            loop = asyncio.get_running_loop()

            async def _shutdown_with_timeout():
                try:
                    await asyncio.wait_for(self._provider.shutdown(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("[ExternalMemoryRail] provider shutdown timed out")
                except Exception as e:
                    logger.warning(f"[ExternalMemoryRail] provider shutdown error: {e}")

            future = asyncio.run_coroutine_threadsafe(_shutdown_with_timeout(), loop)
            future.result(timeout=15.0)
        except RuntimeError:
            try:
                asyncio.run(self._provider.shutdown())
            except Exception as exc:
                logger.warning(f"[ExternalMemoryRail] shutdown failed: {exc}")
        except Exception as exc:
            logger.warning(f"[ExternalMemoryRail] shutdown failed: {exc}")
        
        self._initialized = False
    
    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        # Clear prefetch cache
        self._prefetch_cache = None
        self._prefetch_invoke_id = id(ctx)
        
        if not self._initialized:
            try:
                await self._provider.initialize(
                    user_id=self._user_id,
                    scope_id=self._scope_id,
                    session_id=self._session_id,
                )
                self._initialized = True
                logger.info(f"[ExternalMemoryRail] Provider '{self._provider.name}' initialized")
            except Exception as e:
                logger.error(f"[ExternalMemoryRail] Provider initialize failed: {e}")
    
    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self._initialized or self.system_prompt_builder is None:
            return
        
        # Remove old cached prefetch section
        self.system_prompt_builder.remove_section(EXTERNAL_MEMORY_PREFETCH_SECTION)
        
        # Check prefetch cache
        invoke_id = id(ctx)
        if self._prefetch_invoke_id == invoke_id and self._prefetch_cache is not None:
            raw_context = self._prefetch_cache
        else:
            # Use a unified user text resolution method to resolve query for prefetch
            query = self._resolve_user_text_for_memory(ctx)
            if not query:
                return
            try:
                raw_context = await asyncio.wait_for(
                    self._provider.prefetch(
                        query, user_id=self._user_id, scope_id=self._scope_id,
                    ),
                    timeout=self.PREFETCH_TIMEOUT,
                )
                self._prefetch_cache = raw_context
                self._prefetch_invoke_id = invoke_id
            except asyncio.TimeoutError:
                logger.warning("[ExternalMemoryRail] prefetch timeout")
                return
            except Exception as e:
                logger.error(f"[ExternalMemoryRail] prefetch failed: {e}")
                return
        
        if raw_context:
            fenced = self._build_memory_context_block(raw_context)
            lang = getattr(self.system_prompt_builder, "language", "cn")
            from openjiuwen.core.single_agent.prompts.builder import PromptSection
            section = PromptSection(
                name=EXTERNAL_MEMORY_PREFETCH_SECTION,
                content={lang: fenced},
                priority=55,
            )
            self.system_prompt_builder.add_section(section)
    
    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        if not self._initialized:
            return
        
        if self._sync_consecutive_failures >= _SYNC_BREAKER_THRESHOLD:
            if time.monotonic() < self._sync_breaker_until:
                return
            self._sync_consecutive_failures = 0
        
        # Use a unified user text resolution method to ensure consistency between prefetch and sync_turn
        query = self._resolve_user_text_for_memory(ctx)
        output = self._extract_assistant_output(ctx)
        
        # if not query or not output:
        if not query:
            return
        
        # Serialize: wait for previous sync_turn to complete
        if self._sync_task and not self._sync_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._sync_task), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[ExternalMemoryRail] Previous sync_turn task timed out after 5s, "
                    "proceeding with new sync (potential race condition)"
                )
            except Exception as exc:
                logger.warning(f"[ExternalMemoryRail] Error waiting for sync task: {exc}")
        
        async def _serialized_sync():
            try:
                await self._provider.sync_turn(
                    query, output,
                    user_id=self._user_id,
                    scope_id=self._scope_id,
                    session_id=self._session_id,
                )
                self._sync_consecutive_failures = 0
            except Exception as e:
                self._sync_consecutive_failures += 1
                if self._sync_consecutive_failures >= _SYNC_BREAKER_THRESHOLD:
                    self._sync_breaker_until = time.monotonic() + _SYNC_BREAKER_COOLDOWN
                    logger.warning(
                        f"[ExternalMemoryRail] sync_turn failed {self._sync_consecutive_failures} "
                        f"times consecutively, circuit breaker open for {_SYNC_BREAKER_COOLDOWN}s: {e}"
                    )
                else:
                    logger.warning(f"[ExternalMemoryRail] sync_turn failed: {e}")
        
        self._sync_task = asyncio.create_task(_serialized_sync())
    
    # ---- Tool registration ----
    
    def _register_provider_tools(self, agent) -> None:
        if not hasattr(agent, "ability_manager"):
            return
        try:
            schemas = self._provider.get_tool_schemas()
            for schema in schemas:
                tool_name = schema.get("name", "")
                if not tool_name:
                    continue
                
                tool_id = f"external_memory_{self._provider.name}_{tool_name}"
                tool_card = ToolCard(
                    id=tool_id,
                    name=tool_name,
                    description=schema.get("description", ""),
                    input_params=schema.get("parameters", {}),
                )
                
                captured_provider = self._provider
                captured_name = tool_name
                
                async def _tool_func(captured_name=captured_name, captured_provider=captured_provider, **kwargs):
                    result_str = await captured_provider.handle_tool_call(captured_name, kwargs)
                    try:
                        return json.loads(result_str)
                    except json.JSONDecodeError:
                        return {"result": result_str}
                
                local_func = LocalFunction(card=tool_card, func=_tool_func)
                
                existing = Runner.resource_mgr.get_tool(tool_id)
                if existing is None:
                    add_result = Runner.resource_mgr.add_tool(local_func)
                    if add_result.is_err():
                        logger.warning(f"[ExternalMemoryRail] add_tool failed: {add_result.msg()}")
                        continue
                    self._owned_tool_ids.add(tool_id)
                
                result = agent.ability_manager.add(tool_card)
                if result.added:
                    self._owned_tool_names.add(tool_name)
                    logger.info(f"[ExternalMemoryRail] Registered tool: {tool_name}")
        except Exception as e:
            logger.error(f"[ExternalMemoryRail] Failed to register provider tools: {e}")
    
    # ---- Helper methods ----
    
    @staticmethod
    def _resolve_user_text_for_memory(ctx: AgentCallbackContext) -> str:
        """Resolve user text for memory storage.
        
        Priority rules:
        1. Prefer non-empty ctx.inputs.query
        2. Fallback to last user message in ctx.inputs.messages
        3. Log warning and return empty string if both are empty
        
        Note: Should use this method consistently for prefetch and sync_turn.
        
        Args:
            ctx: AgentCallbackContext
            
        Returns:
            User text string    
        """
        # Prioritize the query field
        if hasattr(ctx.inputs, "query"):
            q = ctx.inputs.query
            if isinstance(q, str) and q.strip():
                return q.strip()
        
        # Fallback to messages field
        if hasattr(ctx.inputs, "messages"):
            messages = ctx.inputs.messages
            if messages:
                for msg in reversed(messages):
                    # Handle dict and object with role attribute
                    if isinstance(msg, dict):
                        role = msg.get("role")
                    else:
                        role = getattr(msg, "role", None)
                    
                    if role == "user":
                        if isinstance(msg, dict):
                            content = msg.get("content")
                        else:
                            content = getattr(msg, "content", None)
                        
                        if isinstance(content, str) and content.strip():
                            return content.strip()
                        if isinstance(content, list):
                            texts = [
                                p.get("text", "") 
                                for p in content 
                                if isinstance(p, dict) and p.get("type") == "text" and p.get("text", "").strip()
                            ]
                            if texts:
                                return " ".join(texts).strip()
        
        # Both query and messages are empty, log warning and return empty string
        session_id = getattr(ctx.inputs, "session_id", "unknown") if hasattr(ctx.inputs, "session_id") else "unknown"
        trace_id = id(ctx) % 10000
        logger.warning(
            f"[ExternalMemoryRail] Cannot resolve user text for memory. "
            f"session_id={session_id}, trace_id={trace_id}, "
            f"has_query={hasattr(ctx.inputs, 'query')}, "
            f"has_messages={hasattr(ctx.inputs, 'messages')}"
        )
        return ""
    
    @staticmethod
    def _extract_assistant_output(ctx: AgentCallbackContext) -> str:
        """Extract assistant output from after_invoke context.
        
        Supports multiple result structures:
        - ctx.inputs.result.output (DeepAgent common)
        - ctx.inputs.result.message.content
        - ctx.inputs.result.get("content")
        
        Args:
            ctx: AgentCallbackContext
            
        Returns:
            Assistant output string, empty string on parse failure with structured logging
        """
        if not hasattr(ctx.inputs, "result"):
            session_id = (
                getattr(ctx.inputs, "session_id", "unknown")
                if hasattr(ctx.inputs, "session_id")
                else "unknown"
            )
            
            logger.warning(
                f"[ExternalMemoryRail] Cannot extract assistant output: result missing. "
                f"session_id={session_id}"
            )
            return ""
        
        result = ctx.inputs.result
        if not isinstance(result, dict):
            return str(result) if result else ""
        
        # Try multiple output keys
        output_keys = ["output", "message", "content", "text", "response"]
        for key in output_keys:
            if key in result:
                value = result[key]
                if isinstance(value, str) and value.strip():
                    return value.strip()
                # message may be nested structure
                if isinstance(value, dict) and "content" in value:
                    content = value["content"]
                    if isinstance(content, str) and content.strip():
                        return content.strip()
        
        # Parse failure, log warning and return empty string
        session_id = getattr(ctx.inputs, "session_id", "unknown") if hasattr(ctx.inputs, "session_id") else "unknown"
        logger.warning(
            f"[ExternalMemoryRail] Cannot extract assistant output from result keys. "
            f"session_id={session_id}, available_keys={list(result.keys())}"
        )
        return ""
    
    @staticmethod
    def _build_memory_context_block(raw_context: str) -> str:
        return (
            "<memory-context>\n"
            "[System note: recalled memory context from long-term memory, NOT new user input.]\n\n"
            f"{raw_context}\n"
            "</memory-context>"
        )
    
