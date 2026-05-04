# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail that configures and injects context engine processors."""
from __future__ import annotations

import json
from typing import List, Tuple, Union, Dict, Any

from pydantic import BaseModel

from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.foundation.llm import ModelRequestConfig
from openjiuwen.core.context_engine import (
    MessageSummaryOffloaderConfig,
    DialogueCompressorConfig,
    CurrentRoundCompressorConfig,
    FullCompactProcessorConfig,
    MicroCompactProcessorConfig,
    ToolResultBudgetProcessorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
    RoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.context.session_memory_manager import SessionMemoryConfig, SessionMemoryManager
from openjiuwen.harness.schema.state import (
    DeepAgentState,
    _SESSION_RUNTIME_ATTR,
    _SESSION_STATE_KEY,
)
from openjiuwen.harness.prompts.sections.reload import build_reload_section


class ContextProcessorRail(DeepAgentRail):
    """Rail that configures context engine processors for the agent.

    In ``init``, reads the current ``context_processors`` list from
    ``agent.react_agent._config`` and appends / replaces entries
    by processor key.

    In ``before_invoke`` and ``on_model_exception``, fixes incomplete tool context.

    Manages session memory if configured.
    """

    priority = 85

    def __init__(
            self,
            processors: Union[
                Tuple[str, BaseModel],
                Tuple[str, Dict],
                List[Tuple[str, BaseModel]],
                List[Tuple[str, Dict]],
                None,
            ] = None,
            preset: bool = True,
            session_memory: SessionMemoryConfig | Dict[str, Any] | None = None,
    ):
        """Initialize ContextProcessorRail.

        Args:
            processors: One or more (processor_key, config) pairs.
            preset: Whether to enable preset default processor config. Defaults to True.
            session_memory: Session memory configuration.
        """
        super().__init__()
        self._preset = preset
        self._user_processors: List[Tuple[str, Union[BaseModel, Dict]]] = []
        if processors is not None:
            if isinstance(processors, tuple):
                self._user_processors = [processors]
            else:
                self._user_processors = list(processors)

        self._session_memory_enabled = session_memory is not None
        self._session_memory_config: SessionMemoryConfig | None = None
        self._session_memory_mgr: SessionMemoryManager | None = None
        if isinstance(session_memory, dict):
            self._session_memory_config = SessionMemoryConfig(**session_memory)
        elif session_memory is not None:
            self._session_memory_config = session_memory
        if self._session_memory_config is not None:
            self._session_memory_mgr = SessionMemoryManager(self._session_memory_config)

        self._system_prompt_builder = None
        self._all_processors: List[Tuple[str, BaseModel]] = []

    @staticmethod
    def _merge_config_with_overrides(
            base_config: BaseModel,
            overrides: Dict,
    ) -> BaseModel:
        if not overrides:
            return base_config
        base_dict = base_config.model_dump(exclude_none=True)
        merged = {**base_dict, **overrides}
        return type(base_config)(**merged)

    @staticmethod
    def _merge_processors(
            base: List[Tuple[str, BaseModel]],
            overrides: List[Tuple[str, Union[BaseModel, Dict]]],
            model_config=None,
            model_client_config=None,
    ) -> List[Tuple[str, BaseModel]]:
        override_map: Dict[str, Union[BaseModel, Dict]] = {key: cfg for key, cfg in overrides}
        base_override_keys = {key for key, _ in base if key in override_map}

        def _build_merged_cfg(key: str, override_cfg: Union[BaseModel, Dict], base_cfg: BaseModel = None) -> BaseModel:
            if base_cfg is not None:
                if isinstance(override_cfg, dict):
                    merged_cfg = ContextProcessorRail._merge_config_with_overrides(base_cfg, override_cfg)
                else:
                    merged_cfg = override_cfg
            else:
                if isinstance(override_cfg, dict):
                    raise ValueError(
                        f"Processor '{key}' does not exist in preset and cannot create config from dict. "
                        "Please ensure this processor is included in the preset,\
                         or pass a complete BaseModel config object."
                    )
                merged_cfg = override_cfg

            if hasattr(merged_cfg, "model") and getattr(merged_cfg, "model", None) is None:
                merged_cfg.model = model_config
            if hasattr(merged_cfg, "model_client") and getattr(merged_cfg, "model_client", None) is None:
                merged_cfg.model_client = model_client_config
            return merged_cfg

        result: List[Tuple[str, BaseModel]] = []
        for key, base_cfg in base:
            if key in override_map:
                merged_cfg = _build_merged_cfg(key, override_map[key], base_cfg)
                result.append((key, merged_cfg))
            else:
                result.append((key, base_cfg))

        for key, override_cfg in overrides:
            if key not in base_override_keys:
                merged_cfg = _build_merged_cfg(key, override_cfg)
                result.append((key, merged_cfg))

        return result

    def _build_preset_processors(
            self,
            model_config=None,
            model_client_config=None,
    ) -> List[Tuple[str, BaseModel]]:
        if model_config is not None:
            model_cfg = ModelRequestConfig.model_copy(model_config)
        else:
            model_cfg = None
        if self._session_memory_enabled:
            presets: List[Tuple[str, BaseModel]] = [
                (
                    "ToolResultBudgetProcessor",
                    ToolResultBudgetProcessorConfig(),
                ),
                (
                    "MicroCompactProcessor",
                    MicroCompactProcessorConfig()
                ),
                (
                    "FullCompactProcessor",
                    FullCompactProcessorConfig(
                        model=model_config,
                        model_client=model_client_config
                    ),
                )
            ]
        else:
            presets: List[Tuple[str, BaseModel]] = [
                (
                    "MessageSummaryOffloader",
                    MessageSummaryOffloaderConfig(
                        tokens_threshold=60000,
                        large_message_threshold=60000,
                        offload_message_type=["tool"],
                        protected_tool_names=["read_file:*SKILL.md", "reload_original_context_messages"],
                        messages_to_keep=None,
                        keep_last_round=False,
                        model=model_cfg,
                        model_client=model_client_config,
                    ),
                ),
                (
                    "DialogueCompressor",
                    DialogueCompressorConfig(
                        tokens_threshold=100000,
                        messages_to_keep=10,
                        keep_last_round=False,
                        compression_target_tokens=1800,
                        model=model_cfg,
                        model_client=model_client_config,
                    ),
                ),
                (
                    "CurrentRoundCompressor",
                    CurrentRoundCompressorConfig(
                        tokens_threshold=100000,
                        messages_to_keep=3,
                        model=model_cfg,
                        model_client=model_client_config,
                    ),
                ),
                (
                    "RoundLevelCompressor",
                    RoundLevelCompressorConfig(
                        trigger_total_tokens=230000,
                        target_total_tokens=160000,
                        keep_last_round=True,
                        keep_recent_messages=6,
                        messages_to_keep=6,
                        model=model_cfg,
                        model_client=model_client_config,
                    )
                ),
            ]
        return presets

    def init(self, agent) -> None:
        """Inject / merge processors into agent.react_agent._config.context_processors."""
        config = getattr(getattr(agent, "react_agent", None), "_config", None)
        if config is None:
            return

        model_config = getattr(config, "model_config_obj", None)
        model_client_config = getattr(config, "model_client_config", None)

        if self._session_memory_config is not None and self._session_memory_mgr is not None:
            if self._session_memory_config.model is None:
                self._session_memory_config.model = model_config
            if self._session_memory_config.model_client is None:
                self._session_memory_config.model_client = model_client_config
            self._session_memory_mgr.bind_model_defaults(model_config, model_client_config)

        if self._preset:
            all_processors = self._merge_processors(
                self._build_preset_processors(model_config, model_client_config),
                self._user_processors,
                model_config=model_config,
                model_client_config=model_client_config,
            )
        else:
            all_processors = self._merge_processors(
                [],
                self._user_processors,
                model_config=model_config,
                model_client_config=model_client_config,
            )

        config.context_processors = all_processors

        self._all_processors = all_processors
        self._system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        """Clear context processors and shutdown session memory manager."""
        if self._session_memory_mgr is not None:
            self._session_memory_mgr.shutdown()

        config = getattr(getattr(agent, "react_agent", None), "_config", None)
        if config is not None:
            config.context_processors = []


        if self._system_prompt_builder is not None:
            self._system_prompt_builder.remove_section("offload")
        self._all_processors = []

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        await self.fix_incomplete_tool_context(ctx)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        self._refresh_task_state_runtime(ctx)
        await self._maybe_inject_offload_section()

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        self._refresh_task_state_runtime(ctx)
        if self._session_memory_mgr is not None:
            self._session_memory_mgr.update_inherited_system_prompt(ctx)
        await self._maybe_schedule_session_memory_update(ctx)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        self._refresh_task_state_runtime(ctx)

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        """Attempt to fix incomplete tool context when LLM call fails.

        When an LLM call fails (e.g. due to invalid context), this hook
        validates and repairs any incomplete tool_call/ToolMessage pairs
        before requesting a retry.
        """
        self._refresh_task_state_runtime(ctx)
        await self.fix_incomplete_tool_context(ctx)

    async def _maybe_schedule_session_memory_update(self, ctx: AgentCallbackContext) -> None:
        if not self._session_memory_enabled or self._session_memory_mgr is None:
            return
        await self._session_memory_mgr.maybe_schedule_update(
            ctx,
            workspace=self.workspace,
        )

    @staticmethod
    def _refresh_task_state_runtime(ctx: AgentCallbackContext) -> None:
        session = ctx.session
        if session is None:
            return
        runtime_state = getattr(session, _SESSION_RUNTIME_ATTR, None)
        if isinstance(runtime_state, DeepAgentState):
            serialized = runtime_state.to_session_dict()
        else:
            persisted_state = session.get_state(_SESSION_STATE_KEY)
            if isinstance(persisted_state, dict):
                serialized = persisted_state
            else:
                serialized = {}
        if not serialized:
            return
        stop_condition_state = serialized.get("stop_condition_state")
        if isinstance(stop_condition_state, dict):
            iteration = int(stop_condition_state.get("iteration", 0) or 0)
        else:
            iteration = int(serialized.get("iteration", 0) or 0)
        session.update_state(
            {
                "task_state": serialized,
                "iteration": iteration,
                "pending_follow_ups": serialized.get("pending_follow_ups", []),
                "plan_mode": serialized.get("plan_mode"),
            }
        )

    @staticmethod
    def _ensure_json_arguments(arguments: Any) -> str:
        """Ensure tool call arguments are valid JSON string."""
        if isinstance(arguments, dict):
            return json.dumps(arguments)
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
                if isinstance(parsed, dict):
                    return arguments
                logger.warning(f"Illegal Tool call arguments: {arguments}")
                return "{}"
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Illegal Tool call arguments: {arguments}")
                return "{}"
        return "{}"

    @staticmethod
    async def fix_incomplete_tool_context(ctx: AgentCallbackContext) -> None:
        """Validate and fix incomplete context messages before entering ReAct loop."""
        from openjiuwen.core.foundation.llm import ToolMessage, AssistantMessage

        try:
            context = ctx.context
            if context is None:
                return

            messages = context.get_messages()
            if not messages:
                return

            len_messages = len(messages)
            popped = context.pop_messages(size=len_messages)
            if not popped:
                return

            tool_message_cache = {}
            tool_id_cache = []

            async def _enqueue_tool_calls(msg: AssistantMessage) -> None:
                tool_calls = getattr(msg, "tool_calls", None)
                if not tool_calls:
                    return
                for tc in tool_calls:
                    arguments = getattr(tc, "arguments", '{}')
                    arguments = ContextProcessorRail._ensure_json_arguments(arguments)
                    if hasattr(tc, "arguments"):
                        tc.arguments = arguments
                    tool_id_cache.append({
                        "tool_call_id": getattr(tc, "id", ""),
                        "tool_name": getattr(tc, "name", ""),
                    })

            async def _flush_pending_tools() -> None:
                nonlocal tool_message_cache
                for tool_msg in tool_message_cache.values():
                    await context.add_messages(tool_msg)
                tool_message_cache = {}
                for tc in tool_id_cache:
                    await context.add_messages(ToolMessage(
                        content=f"[Tool execution interrupted] Tool {tc['tool_name']}\
                         was interrupted by user during execution, no result available.",
                        tool_call_id=tc["tool_call_id"],
                    ))
                tool_id_cache.clear()

            for msg in popped:
                if isinstance(msg, AssistantMessage):
                    if tool_id_cache:
                        logger.info("Fixed incomplete tool context with placeholder messages")
                        await _flush_pending_tools()
                    await context.add_messages(msg)
                    await _enqueue_tool_calls(msg)
                elif isinstance(msg, ToolMessage):
                    if not tool_id_cache:
                        await context.add_messages(msg)
                    elif msg.tool_call_id == tool_id_cache[0]["tool_call_id"]:
                        await context.add_messages(msg)
                        tool_id_cache.pop(0)
                    else:
                        tool_message_cache[msg.tool_call_id] = msg
                else:
                    if tool_id_cache:
                        logger.info("Fixed incomplete tool context with placeholder messages")
                        await _flush_pending_tools()
                    await context.add_messages(msg)
            if tool_id_cache:
                logger.info("Fixed incomplete tool context with placeholder messages")
                await _flush_pending_tools()
        except Exception as e:
            import traceback
            logger.warning("Failed to fix incomplete tool context: %s\n%s", e, traceback.format_exc())

    # ============================================================================
    # Offload Section Injection
    # ============================================================================

    async def _maybe_inject_offload_section(self) -> None:
        """Inject offload section if processors are configured."""
        if not self._all_processors:
            if self._system_prompt_builder is not None:
                self._system_prompt_builder.remove_section("offload")
            return

        if self._system_prompt_builder is None:
            return

        lang = self._system_prompt_builder.language or "cn"
        self._system_prompt_builder.add_section(build_reload_section(lang))
