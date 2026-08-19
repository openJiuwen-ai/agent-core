# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail that configures and injects context engine processors."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple, Union

from pydantic import BaseModel

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.context.session_memory_manager import (
    SessionMemoryConfig,
    SessionMemoryManager,
)
from openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor import (
    CurrentRoundCompressorConfig as ForkedCurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressorConfig as ForkedDialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor import (
    RoundLevelCompressorConfig as ForkedRoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor import (
    SessionMemoryCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
    MessageSummaryOffloaderConfig as ForkedMessageSummaryOffloaderConfig,
)
from openjiuwen.core.context_engine.schema.config import CompressionRecallConfig
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ModelRequestConfig,
    ToolMessage,
)
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections.compression_recall import build_compression_recall_section
from openjiuwen.harness.prompts.sections.reload import build_reload_section
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.state import (
    _SESSION_RUNTIME_ATTR,
    _SESSION_STATE_KEY,
    DeepAgentState,
)

_SESSION_MEMORY_PROCESSOR_KEY = "SessionMemoryCompressor"


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
            session_memory: Deprecated, kept only for backward compatibility of
                the constructor signature. Accepted and ignored; configure
                session memory via the ``SessionMemoryCompressor`` processor
                (``SessionMemoryCompressorConfig.enabled`` / ``.memory``).

        Session memory ships in the default (forked) preset chain as a disabled
        ``SessionMemoryCompressor``; users opt in by overriding it with
        ``enabled=True``. The companion async memory updater
        (``SessionMemoryManager``) is configured via
        ``SessionMemoryCompressorConfig.memory``.
        """
        super().__init__()
        self._preset = preset
        self._user_processors: List[Tuple[str, Union[BaseModel, Dict]]] = []
        if processors is not None:
            if isinstance(processors, tuple):
                self._user_processors = [processors]
            else:
                self._user_processors = list(processors)

        self._session_memory_mgr: SessionMemoryManager | None = None

        self._system_prompt_builder = None
        self._all_processors: List[Tuple[str, BaseModel]] = []
        self._reload_enabled = False
        self._recall_enabled = False
        self._initialized = False
        # Abilities this rail actually registered, mapped from tool name to the
        # exact card that was stored. The name is the ability-manager key, while
        # the card identity tells uninit whether this rail is still the owner or
        # another rail has since taken the name over.
        self._owned_tool_cards: Dict[str, ToolCard] = {}

    def add_processors(
        self,
        processors: Union[
            Tuple[str, BaseModel],
            Tuple[str, Dict],
            List[Tuple[str, BaseModel]],
            List[Tuple[str, Dict]],
        ],
    ) -> None:
        """Add or replace processor configs before the rail is initialized."""
        if self._initialized:
            raise RuntimeError("Cannot add context processors after ContextProcessorRail.init()")

        additions = [processors] if isinstance(processors, tuple) else list(processors)
        for processor_key, processor_config in additions:
            for index, (existing_key, _) in enumerate(self._user_processors):
                if existing_key == processor_key:
                    self._user_processors[index] = (processor_key, processor_config)
                    break
            else:
                self._user_processors.append((processor_key, processor_config))

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
        override_map: Dict[str, Union[BaseModel, Dict]] = dict(overrides)
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
        context_debug_enabled: bool = False,
        context_debug_dir: str | None = None,
    ) -> List[Tuple[str, BaseModel]]:
        if model_config is not None:
            model_cfg = ModelRequestConfig.model_copy(model_config)
        else:
            model_cfg = None
        from openjiuwen.core.context_engine.processor import forked

        forked.activate()
        # The forked chain is the default preset. SessionMemoryCompressor ships
        # disabled: users opt in by overriding it with enabled=True, which also
        # starts the companion SessionMemoryManager (see init()).
        # When the unified enable_context_debug flag is on, propagate it to every
        # forked processor so threshold/span/retry/before-after records are all
        # persisted to the same debug directory.
        presets: List[Tuple[str, BaseModel]] = [
            (
                "MessageSummaryOffloader",
                ForkedMessageSummaryOffloaderConfig(
                    enable_debug_dump=context_debug_enabled,
                    debug_dump_dir=context_debug_dir,
                ),
            ),
            (
                "SessionMemoryCompressor",
                SessionMemoryCompressorConfig(
                    enabled=False,
                    memory=SessionMemoryConfig(
                        enable_debug_dump=context_debug_enabled,
                        debug_dump_dir=context_debug_dir,
                    ),
                    enable_context_debug=context_debug_enabled,
                    context_debug_dir=context_debug_dir,
                ),
            ),
            (
                "DialogueCompressor",
                ForkedDialogueCompressorConfig(
                    model=model_cfg,
                    model_client=model_client_config,
                    enable_compression_dump=context_debug_enabled,
                    compression_dump_dir=context_debug_dir,
                ),
            ),
            (
                "CurrentRoundCompressor",
                ForkedCurrentRoundCompressorConfig(
                    model=model_cfg,
                    model_client=model_client_config,
                    enable_compression_dump=context_debug_enabled,
                    compression_dump_dir=context_debug_dir,
                ),
            ),
            (
                "RoundLevelCompressor",
                ForkedRoundLevelCompressorConfig(
                    model=model_cfg,
                    model_client=model_client_config,
                    enable_compression_dump=context_debug_enabled,
                    compression_dump_dir=context_debug_dir,
                ),
            ),
        ]
        return presets

    def init(self, agent) -> None:
        """Inject / merge processors into agent.react_agent._config.context_processors."""
        self._initialized = True
        config = getattr(getattr(agent, "react_agent", None), "_config", None)
        if config is None:
            return

        model_config = getattr(config, "model_config_obj", None)
        model_client_config = getattr(config, "model_client_config", None)
        context_engine_config = getattr(config, "context_engine_config", None)
        context_debug_enabled = bool(getattr(context_engine_config, "enable_context_debug", False))
        context_debug_dir = getattr(context_engine_config, "context_debug_dir", None)

        # The engine instantiates every processor in the final list regardless
        # of its ``enabled`` flag, so the forked implementations must be
        # resolvable whenever a SessionMemoryCompressor is present. The preset
        # chain activates them in _build_preset_processors; with preset=False
        # do it here when the user registers one explicitly. The companion
        # async updater (SessionMemoryManager) only starts when the merged
        # compressor config is enabled.
        if not self._preset and any(key == _SESSION_MEMORY_PROCESSOR_KEY for key, _ in self._user_processors):
            from openjiuwen.core.context_engine.processor import forked

            forked.activate()

        if self._preset:
            all_processors = self._merge_processors(
                self._build_preset_processors(
                    model_config,
                    model_client_config,
                    context_debug_enabled=context_debug_enabled,
                    context_debug_dir=context_debug_dir,
                ),
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

        affinity_config = getattr(config, "kv_cache_affinity_config", None)
        affinity_enabled = bool(
            getattr(affinity_config, "enable_kv_cache_affinity", False)
        )
        self._apply_compressor_affinity_policy(
            all_processors,
            enabled=affinity_enabled,
        )

        self._maybe_setup_session_memory_manager(all_processors, model_config, model_client_config)

        config.context_processors = all_processors
        processor_paths = ", ".join(
            f"{name}={processor_config.__class__.__module__}.{processor_config.__class__.__qualname__}"
            for name, processor_config in all_processors
        )
        logger.info("context processors initialized: %s", processor_paths)
        for name, processor_config in all_processors:
            logger.info(
                "processor effective config: %s %s",
                name,
                self._summarize_processor_config(processor_config),
            )

        self._all_processors = all_processors
        self._system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        context_engine_config = getattr(config, "context_engine_config", None)
        recall_config = getattr(context_engine_config, "compression_recall_config", None)
        supported_compressor_present = any(
            processor_name
            in {
                "DialogueCompressor",
                "CurrentRoundCompressor",
                "RoundLevelCompressor",
                "SessionMemoryCompressor",
            }
            for processor_name, _ in all_processors
        )
        recall_requested = isinstance(recall_config, CompressionRecallConfig) and recall_config.enabled
        self._recall_enabled = recall_requested and supported_compressor_present
        if isinstance(recall_config, CompressionRecallConfig):
            logger.info(
                "compression recall effective config: %s",
                self._summarize_processor_config(recall_config),
            )
        if recall_requested and not supported_compressor_present:
            logger.warning("compression recall is enabled but no supported forked compressor is configured")
        if self._recall_enabled:
            self._protect_compression_recall_tool_results(all_processors)
            self._register_compression_recall_tool(agent)
        self._reload_enabled = bool(getattr(context_engine_config, "enable_reload", False))

    @staticmethod
    def _apply_compressor_affinity_policy(
        processors: List[Tuple[str, BaseModel]],
        *,
        enabled: bool,
    ) -> None:
        """Keep compressor affinity aligned with the owning ReActAgent."""
        supported = {
            "DialogueCompressor",
            "CurrentRoundCompressor",
            "RoundLevelCompressor",
        }
        for name, processor_config in processors:
            if name in supported and hasattr(
                processor_config,
                "enable_kv_cache_affinity",
            ):
                processor_config.enable_kv_cache_affinity = enabled

    def _maybe_setup_session_memory_manager(
        self,
        all_processors: List[Tuple[str, BaseModel]],
        model_config,
        model_client_config,
    ) -> None:
        """Create / bind the SessionMemoryManager paired with an enabled compressor."""
        memory_cfg = None
        for key, cfg in all_processors:
            if key == _SESSION_MEMORY_PROCESSOR_KEY and getattr(cfg, "enabled", False):
                memory_cfg = getattr(cfg, "memory", None)
                break
        if memory_cfg is None:
            return
        if memory_cfg.model is None:
            memory_cfg.model = model_config
        if memory_cfg.model_client is None:
            memory_cfg.model_client = model_client_config
        if self._session_memory_mgr is None:
            self._session_memory_mgr = SessionMemoryManager(memory_cfg)
        self._session_memory_mgr.bind_model_defaults(model_config, model_client_config)
        logger.info(
            "SessionMemoryManager enabled: async session memory updates active, config: %s",
            self._summarize_processor_config(memory_cfg),
        )

    @staticmethod
    def _summarize_processor_config(cfg: BaseModel) -> Dict[str, Any]:
        """Extract scalar (and nested scalar) config fields for effective-config logging."""
        skipped_fields = {"model", "model_client", "api_key"}
        summary: Dict[str, Any] = {}
        for field, value in cfg.model_dump().items():
            if field in skipped_fields:
                continue
            if isinstance(value, (bool, int, float, str)):
                summary[field] = value
            elif isinstance(value, dict):
                nested = {
                    k: v for k, v in value.items() if k not in skipped_fields and isinstance(v, (bool, int, float, str))
                }
                if nested:
                    summary[field] = nested
            elif isinstance(value, (list, tuple)) and all(isinstance(v, (bool, int, float, str)) for v in value):
                summary[field] = list(value)
        return summary

    def uninit(self, agent) -> None:
        """Clear context processors and shutdown session memory manager."""
        if self._session_memory_mgr is not None:
            self._session_memory_mgr.shutdown()
            self._session_memory_mgr = None

        config = getattr(getattr(agent, "react_agent", None), "_config", None)
        if config is not None:
            config.context_processors = []

        if self._system_prompt_builder is not None:
            self._system_prompt_builder.remove_section("offload")
            self._system_prompt_builder.remove_section("compression_recall")
        for tool_name, tool_card in list(self._owned_tool_cards.items()):
            if agent.ability_manager.get(tool_name) is not tool_card:
                # Another rail re-registered the name after this rail did and
                # now owns both the card and the live instance; tearing it down
                # here would unregister that rail's tool.
                continue
            agent.ability_manager.remove_ability(tool_name)
        self._owned_tool_cards.clear()
        self._all_processors = []
        self._reload_enabled = False
        self._recall_enabled = False
        self._initialized = False

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        await self.fix_incomplete_tool_context(ctx)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        self._refresh_task_state_runtime(ctx)
        await self._maybe_inject_offload_section()
        self._maybe_inject_compression_recall_section()

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
        if self._session_memory_mgr is None:
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
                    arguments = getattr(tc, "arguments", "{}")
                    arguments = ContextProcessorRail._ensure_json_arguments(arguments)
                    if hasattr(tc, "arguments"):
                        tc.arguments = arguments
                    tool_id_cache.append(
                        {
                            "tool_call_id": getattr(tc, "id", ""),
                            "tool_name": getattr(tc, "name", ""),
                        }
                    )

            async def _flush_pending_tools() -> None:
                nonlocal tool_message_cache
                for tool_msg in tool_message_cache.values():
                    await context.add_messages(tool_msg)
                tool_message_cache = {}
                for tc in tool_id_cache:
                    await context.add_messages(
                        ToolMessage(
                            content=f"[Tool execution interrupted] Tool {tc['tool_name']}\
                         was interrupted by user during execution, no result available.",
                            tool_call_id=tc["tool_call_id"],
                        )
                    )
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
        if self._system_prompt_builder is None:
            return

        if not self._reload_enabled or not self._all_processors:
            self._system_prompt_builder.remove_section("offload")
            return

        lang = self._system_prompt_builder.language or "cn"
        self._system_prompt_builder.add_section(build_reload_section(lang))

    def _register_compression_recall_tool(self, agent) -> None:
        ability_manager = getattr(agent, "ability_manager", None)
        add_ability = getattr(ability_manager, "add_ability", None)
        if not callable(add_ability):
            return
        from openjiuwen.harness.tools.compression_recall import CompressionRecallTool

        workspace_dir = str(getattr(self.workspace, "root_path", "") or "")
        agent_id = getattr(getattr(agent, "card", None), "id", None) or "default"
        language = getattr(self._system_prompt_builder, "language", "cn") or "cn"
        tool = CompressionRecallTool(workspace_dir, language=language, agent_id=agent_id)
        result = add_ability(tool.card, tool)
        if result.added:
            self._owned_tool_cards[tool.card.name] = tool.card

    @staticmethod
    def _protect_compression_recall_tool_results(processors: List[Tuple[str, BaseModel]]) -> None:
        for processor_name, processor_config in processors:
            if processor_name != "MessageSummaryOffloader":
                continue
            protected = getattr(processor_config, "protected_tool_names", None)
            if not isinstance(protected, list) or "recall_compressed_context" in protected:
                continue
            processor_config.protected_tool_names = [*protected, "recall_compressed_context"]

    def _maybe_inject_compression_recall_section(self) -> None:
        if self._system_prompt_builder is None:
            return
        if not self._recall_enabled:
            self._system_prompt_builder.remove_section("compression_recall")
            return
        language = self._system_prompt_builder.language or "cn"
        self._system_prompt_builder.add_section(build_compression_recall_section(language))


__all__ = [
    "ContextProcessorRail",
]
