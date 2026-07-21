# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent implementation."""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import importlib
import os
import sys
import uuid
from contextlib import suppress
import warnings
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Dict,
    List,
    Optional,
    Tuple,
    cast,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.modules.task_manager import TaskFilter
from openjiuwen.core.controller.schema.event import (
    FollowUpEvent,
    TaskInteractionEvent,
)
from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.foundation.llm import BaseMessage, SystemMessage
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
    InvokeInputs,
    RunContext,
    RunKind,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.image_modality_probe import probe_image_support
from openjiuwen.harness.rails import DeepAgentRail
from openjiuwen.harness.rails.progressive_tool_rail import ProgressiveToolRail
from openjiuwen.harness.rails.task_completion_rail import (
    TaskCompletionRail,
)
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.schema.state import (
    _SESSION_RUNTIME_ATTR,
    _SESSION_STATE_KEY,
    DeepAgentState,
)
from openjiuwen.harness.security.factory import build_permission_interrupt_rail
from openjiuwen.harness.task_loop.loop_coordinator import (
    LoopCoordinator,
)
from openjiuwen.harness.task_loop.loop_queues import (
    LoopQueues,
)
from openjiuwen.harness.task_loop.task_loop_controller import (
    TaskLoopController,
)
from openjiuwen.harness.task_loop.task_loop_event_executor import (
    DEEP_TASK_TYPE,
    build_deep_executor,
)
from openjiuwen.harness.task_loop.task_loop_event_handler import (
    TaskLoopEventHandler,
)
from openjiuwen.harness.tools import SessionToolkit, is_free_search_enabled, is_paid_search_enabled
from openjiuwen.harness.goal.manager import GoalManager
from openjiuwen.harness.goal.schema import GoalRecord, GoalStatus
from openjiuwen.harness.schema.interaction import (
    ActiveInteractionRound,
    InteractionEvent,
    InteractionPhase,
    InteractionOutputStream,
    OutputLease,
    OutputLeaseManager,
    InputDispatchMode,
    RoundOutcome,
    RoundWorkItem,
    SendInputRequest,
)
from openjiuwen.harness.task_loop.event_manager import EventManager

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.event_queue import (
        EventQueue,
    )
    from openjiuwen.harness.schema.config import SubAgentConfig

from openjiuwen.harness.prompts import (
    PromptSection,
    SystemPromptBuilder,
    resolve_language,
    resolve_mode,
)
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachmentManager,
)
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.identity import build_identity_section
from openjiuwen.harness.resources import (
    LoadRecord,
    find_expert_harness_manifest,
    load_expert_harness_spec,
)
from openjiuwen.harness.resources.expert_harness_parts import ExpertHarnessParts, ResolvedSkill
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.expert_harness_spec import ExpertHarnessSpec
from openjiuwen.harness.workspace.workspace import Workspace

# Events bridged to the inner ReActAgent.
_BRIDGE_EVENTS = frozenset(
    {
        AgentCallbackEvent.BEFORE_MODEL_CALL,
        AgentCallbackEvent.AFTER_MODEL_CALL,
        AgentCallbackEvent.ON_MODEL_EXCEPTION,
        AgentCallbackEvent.BEFORE_TOOL_CALL,
        AgentCallbackEvent.AFTER_TOOL_CALL,
        AgentCallbackEvent.ON_TOOL_EXCEPTION,
        # Fired by the inner ReActAgent at each successful iteration end, so a
        # rail registered via DeepAgent.register_rail must bridge to the inner
        # agent (same callback-manager namespace) rather than the outer one.
        AgentCallbackEvent.AFTER_REACT_ITERATION,
    }
)

# Events that stay on the outer DeepAgent only.
_OUTER_ONLY_EVENTS = frozenset(
    {
        AgentCallbackEvent.BEFORE_INVOKE,
        AgentCallbackEvent.AFTER_INVOKE,
    }
)

# Deep extension events.
_DEEP_EVENTS = frozenset(
    {
        AgentCallbackEvent.BEFORE_TASK_ITERATION,
        AgentCallbackEvent.AFTER_TASK_ITERATION,
    }
)

_SUB_AGENTS_DIR = "sub_agents"


_ROUND_BOUNDARY = object()


class DeepAgent(BaseAgent):
    """High-level agent that delegates to an internal ReActAgent."""

    def __init__(self, card: AgentCard):
        self._deep_config: Optional[DeepAgentConfig] = None
        self._react_agent: Optional[ReActAgent] = None
        self._pending_rails: List[AgentRail] = []
        self._stale_rails: List[AgentRail] = []
        self._registered_rails: List[AgentRail] = []
        self._loop_coordinator: Optional[LoopCoordinator] = None
        self._loop_controller: Optional[TaskLoopController] = None
        self._loop_session: Optional[Session] = None
        self._task_completion_rail: Optional[TaskCompletionRail] = None
        self._initialized = False
        self.system_prompt_builder: Optional[SystemPromptBuilder] = None
        self._invoke_active: bool = False
        self._stream_process_task: Optional[asyncio.Task] = None
        self._auto_invoke_scheduled: bool = False
        self._bound_session_id: Optional[str] = None
        self._load_records: dict[str, LoadRecord] = {}
        self._session_toolkit: SessionToolkit | None = None
        self._pending_harness_configs: List[str] = []
        self.prompt_attachment_manager: PromptAttachmentManager = PromptAttachmentManager()
        self._interaction_session: Optional[Session] = None
        self._interaction_phase = InteractionPhase.IDLE
        self._active_interaction_round: Optional[ActiveInteractionRound] = None
        self._event_manager = EventManager()
        self.goal_manager: Optional[GoalManager] = None
        self._interaction_output = OutputLeaseManager()
        self._interaction_supervisor_task: Optional[asyncio.Task[None]] = None
        self._interaction_forwarder_task: Optional[asyncio.Task[None]] = None
        self._interaction_round_task: Optional[asyncio.Task[None]] = None
        self._interaction_emit_tasks: set[asyncio.Task[Any]] = set()
        self._interaction_round_forwarded: Optional[asyncio.Event] = None
        self._interaction_start_lock = asyncio.Lock()
        self._interaction_control_lock = asyncio.Lock()
        self._interaction_send_lock = asyncio.Lock()
        self._interaction_wakeup = asyncio.Event()
        self._interaction_started = False
        super().__init__(card)

    def set_session_toolkit(self, toolkit: SessionToolkit | None) -> None:
        """Attach or clear the session toolkit (wired by SubagentRail async)."""
        self._session_toolkit = toolkit

    def configure(self, config: DeepAgentConfig) -> "DeepAgent":
        """Apply configuration and rebuild the internal ReActAgent."""

        self._filter_disabled_tools(config)
        if self._deep_config is None:
            self._initial_configure(config)
        else:
            self._hot_reconfigure(config)

        self._initialized = False
        return self

    @staticmethod
    def _filter_disabled_tools(config: DeepAgentConfig) -> None:
        if config.tools is None:
            return
        disabled_tool_names: set[str] = set()
        if not is_free_search_enabled():
            disabled_tool_names.add("free_search")
        if not is_paid_search_enabled():
            disabled_tool_names.add("paid_search")
        if not disabled_tool_names:
            return
        config.tools = [
            card for card in config.tools
            if not (isinstance(card, ToolCard) and card.name in disabled_tool_names)
        ]

    def _unregister_tool_resource(self, card: ToolCard) -> None:
        if card.name not in {"free_search", "paid_search"}:
            return
        if not getattr(card, "id", None):
            return
        if Runner.resource_mgr.get_tool(card.id) is None:
            return
        result = Runner.resource_mgr.remove_tool(card.id)
        if result.is_err():
            logger.warning(
                "[DeepAgent] Failed to unregister tool during hot reload: %s",
                result.msg(),
            )

    def _ensure_builtin_tool_resource(self, card: ToolCard, config: DeepAgentConfig) -> None:
        if card.name not in {"free_search", "paid_search"}:
            return
        # Builtin search tools are not shared across agents; a hit here means
        # this agent already registered the same card on a prior reconfigure.
        if Runner.resource_mgr.get_tool(card.id) is not None:
            return

        from openjiuwen.harness.tools import WebFreeSearchTool, WebPaidSearchTool

        tool_cls = WebPaidSearchTool if card.name == "paid_search" else WebFreeSearchTool
        tool = tool_cls(language=resolve_language(config.language), card=card)
        result = Runner.resource_mgr.add_tool(tool, tag=self.card.id)
        if result.is_err():
            logger.warning(
                "[DeepAgent] Failed to register free_search during hot reload: %s",
                result.msg(),
            )

    def _initial_configure(self, config: DeepAgentConfig) -> None:
        """First-time setup: persist config, create the inner ReActAgent, and queue rails."""
        self._deep_config = config
        if config.card is not None:
            self.card = config.card
            self.ability_manager.set_owner_id(self.card.id)

        self._react_agent = self._create_react_agent()
        self._queue_pending_rails(config)

    def _hot_reconfigure(self, config: DeepAgentConfig) -> None:
        """Hot-reconfigure an already-running agent without restarting it."""
        previous_config = self._deep_config
        self._deep_config = config
        if config.card is not None:
            self.card = config.card
            self.ability_manager.set_owner_id(self.card.id)

        self._hot_reload_rails(config)

        if config.model is not None:
            self._hot_reload_model(config)

        if config.tools is not None and self._react_agent is not None:
            self._hot_reload_tools(
                config,
                previous_tools=(previous_config.tools if previous_config is not None else None),
            )

        if config.system_prompt is not None and self._react_agent is not None:
            self._hot_reload_system_prompt(config)

        self._queue_pending_rails(config)
        self._sync_prompt_builder_references()

    def _hot_reload_rails(self, config: DeepAgentConfig) -> None:
        """Cycle stale rails out and prepare replacement rails during hot-reconfigure.

        config.rails is not None - partial update: only rails whose type appears
        in the new list are retired; others are retained.
        config.rails is None - full replacement: all existing rails become stale.
        config.rails == [] - nothing to replace; keep all existing rails.
        """
        if config.rails is not None:
            # Partial update: only cycle rails whose type appears in the new list.
            replacing_types = {type(r) for r in config.rails}
            retained = []
            for rail in self._registered_rails:
                if type(rail) in replacing_types:
                    self._stale_rails.append(rail)
                else:
                    retained.append(rail)
            self._registered_rails = retained
            self._pending_rails = [
                rail for rail in self._pending_rails
                if type(rail) not in replacing_types
            ]
        else:
            # Full replacement: all existing rails become stale.
            self._stale_rails.extend(self._registered_rails)
            self._registered_rails.clear()
            self._pending_rails.clear()

    def _hot_reload_model(self, config: DeepAgentConfig) -> None:
        """Apply updated model/iteration config to the live inner ReActAgent.

        Mutates a copy of the existing ReActAgentConfig with the new model
        fields, then drives the update through react_agent.configure() so that
        all side-effects (LLM reset, context-engine rebuild, etc.) fire correctly.
        """
        if self._react_agent is None:
            return
        new_react_config = self._react_agent.config.model_copy()
        if config.model is not None:
            new_react_config.model_client_config = config.model.model_client_config
            new_react_config.model_config_obj = config.model.model_config
            if config.model.model_config is not None and config.model.model_config.model_name:
                new_react_config.model_name = config.model.model_config.model_name
            # Sync direct fields so configure()'s LLM-reset condition fires correctly.
            if config.model.model_client_config is not None:
                client_cfg = config.model.model_client_config
                new_react_config.api_base = client_cfg.api_base
                new_react_config.api_key = client_cfg.api_key
                new_react_config.model_provider = str(
                    client_cfg.client_provider.value
                    if hasattr(client_cfg.client_provider, "value")
                    else client_cfg.client_provider
                )
        new_react_config.max_iterations = (
            sys.maxsize if config.enable_task_loop else config.max_iterations
        )
        if config.context_engine_config is not None:
            new_react_config.context_engine_config = config.context_engine_config
        self._react_agent.configure(new_react_config)
        self._sync_prompt_builder_references()
        logger.info("[DeepAgent] Model configuration hot reloaded")

    def _hot_reload_tools(
        self,
        config: DeepAgentConfig,
        *,
        previous_tools: Optional[List[ToolCard]] = None,
    ) -> None:
        """Sync tool cards in the shared AbilityManager during hot-reconfigure.

        Tools are matched by id: a card whose id already exists in the
        AbilityManager is left untouched.  Cards with a new id replace any
        existing entry with the same name, or are added fresh.  Tools present
        in the AbilityManager but absent from config.tools are removed.
        MCP server registrations and other ability types are not affected.
        """
        new_by_name = {card.name: card for card in (config.tools or [])}
        previous_by_name = {
            card.name: card for card in (previous_tools or []) if isinstance(card, ToolCard)
        }

        # Only remove tools that were previously managed by config.tools.
        # Rail-registered tools such as task_tool must survive hot reload.
        managed_tool_names = set(previous_by_name)
        if not managed_tool_names:
            managed_tool_names = set(new_by_name)
        stale = [name for name in managed_tool_names if name not in new_by_name]
        if stale:
            for name in stale:
                card = previous_by_name.get(name)
                if card is not None:
                    self._unregister_tool_resource(card)
            self.ability_manager.remove(stale)

        # Add or replace tools whose id has changed (or are new).
        for name, card in new_by_name.items():
            existing = self.ability_manager.get(name)
            existing_tool = existing if isinstance(existing, ToolCard) else None
            if existing_tool is not None and existing_tool.id == card.id:
                self._ensure_builtin_tool_resource(card, config)
                continue  # Same id - no update needed.
            if existing_tool is not None:
                self._unregister_tool_resource(existing_tool)
                self.ability_manager.remove(name)
            self.ability_manager.add(card)
            self._ensure_builtin_tool_resource(card, config)
        self.ability_manager.reorder_tools(list(new_by_name))

    def _hot_reload_system_prompt(self, config: DeepAgentConfig) -> None:
        """Rebuild the SystemPromptBuilder and update both agents during hot-reconfigure.

        The same builder object must be referenced by both DeepAgent.system_prompt_builder
        and react_agent.system_prompt_builder so that rails mutating the builder before
        model calls see a consistent view.
        """
        language = resolve_language(config.language)
        mode = resolve_mode(config.prompt_mode)
        prompt_builder = SystemPromptBuilder(language=language, mode=mode)
        if config.system_prompt:
            prompt_builder.add_section(PromptSection(
                name=SectionName.IDENTITY,
                content={"cn": config.system_prompt, "en": config.system_prompt},
                priority=10,
            ))
        else:
            prompt_builder.add_section(build_identity_section(language))
        prompt = prompt_builder.build()
        new_react_config = self._react_agent.config.model_copy()
        new_react_config.prompt_template = [{"role": "system", "content": prompt}]
        self._react_agent.configure(new_react_config)
        self.system_prompt_builder = prompt_builder
        self._sync_prompt_builder_references()
        logger.info("[DeepAgent] System prompt hot reloaded")

    def _sync_prompt_builder_references(self) -> None:
        """Push the current system_prompt_builder reference everywhere.

        ReActAgent.configure() may rebuild its prompt_builder, and rails can be
        registered, pending, or stale across hot-reconfigure cycles.  Keep every
        mutable prompt participant on the same builder object so callbacks never
        write to a builder different from the one used for the final model call.
        """
        builder = self.system_prompt_builder
        if builder is None:
            return

        if self._react_agent is not None:
            self._react_agent.prompt_builder = builder
            self._react_agent.system_prompt_builder = builder
            self._react_agent.prompt_attachment_manager = self.prompt_attachment_manager

        for rail in (*self._pending_rails, *self._registered_rails, *self._stale_rails):
            if hasattr(rail, "system_prompt_builder"):
                rail.system_prompt_builder = builder
            if hasattr(rail, "_system_prompt_builder"):
                setattr(rail, "_system_prompt_builder", builder)

    def apply_prompt_builder_to_react_agent(self) -> None:
        """Re-render the current system_prompt_builder into the inner ReActAgent.

        Public entry point for resource binding targets after a prompt section
        mutation: rebuilds ``react_agent.config.prompt_template`` from the
        builder's current output and reconfigures the ReActAgent, then syncs
        every prompt participant to the same builder reference via
        :meth:`_sync_prompt_builder_references`. Keeps the binding target from
        touching ``react_agent.config`` / ``react_agent.configure`` directly.
        """
        builder = self.system_prompt_builder
        if builder is None or self._react_agent is None:
            return
        new_react_config = self._react_agent.config.model_copy()
        new_react_config.prompt_template = [{"role": "system", "content": builder.build()}]
        self._react_agent.configure(new_react_config)
        self._sync_prompt_builder_references()

    def _queue_pending_rails(self, config: DeepAgentConfig) -> None:
        """Append config-driven rails to _pending_rails for lazy registration."""
        if config.rails is not None:
            self._pending_rails.extend(config.rails)

        self._task_completion_rail = None

        if config.progressive_tool_enabled:
            self._pending_rails.append(
                ProgressiveToolRail(config=config)
            )

        # Auto-inject a default TaskCompletionRail when the outer
        # task loop is enabled.  Users can override it by passing
        # their own TaskCompletionRail via add_rail() or the
        # factory's rails= argument.
        if config.enable_task_loop:
            self._pending_rails.append(TaskCompletionRail())

        if isinstance(config.permissions, dict) and config.permissions.get("enabled"):
            ws_root = None
            if config.workspace is not None:
                ws_root = Path(config.workspace.root_path).resolve()
            model_name = None
            if config.model is not None:
                model_name = getattr(config.model, "model_name", None)
            prail = build_permission_interrupt_rail(
                permissions=config.permissions,
                llm=config.model,
                model_name=model_name,
                host=config.permission_host,
                workspace_root=ws_root,
            )
            if prail is not None:
                self._pending_rails.append(prail)

    def set_react_agent(
        self,
        react_agent: Any,
        initialized: bool = False,
    ) -> "DeepAgent":
        """Inject an inner agent implementation for runtime wiring/tests."""
        self._react_agent = cast(ReActAgent, react_agent)
        self._initialized = initialized
        return self

    @property
    def is_initialized(self) -> bool:
        """Whether lazy rail initialization is completed."""
        return self._initialized

    @property
    def is_invoke_active(self) -> bool:
        """Whether is invoke_active."""
        return self._invoke_active

    @property
    def is_auto_invoke_scheduled(self) -> bool:
        """Whether is auto_invoke_scheduled."""
        return self._auto_invoke_scheduled

    def set_auto_invoke_scheduled(self, is_scheduled: bool) -> None:
        """Set auto_invoke_scheduled."""
        self._auto_invoke_scheduled = is_scheduled

    @property
    def deep_config(self) -> DeepAgentConfig:
        """deep_config carried by this agent."""
        return self._deep_config

    @property
    def react_agent(self) -> Optional[ReActAgent]:
        """The internal ReActAgent instance."""
        return self._react_agent

    def _resolve_context_session_id(self, session_id: Optional[str] = None) -> str:
        """Resolve the session id used by context inspection APIs."""
        resolved = (
            session_id
            or self._bound_session_id
            or (
                self._loop_session.get_session_id()
                if self._loop_session is not None
                else None
            )
        )
        if not resolved:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="session_id is required when no context is bound.",
            )
        return resolved

    def _get_context_or_error(
        self,
        session_id: Optional[str] = None,
        context_id: str = "default_context_id",
    ):
        """Return the inner ReAct context for a session, or raise."""
        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        resolved_session_id = self._resolve_context_session_id(session_id)
        context = self._react_agent.context_engine.get_context(
            context_id=context_id,
            session_id=resolved_session_id,
        )
        if context is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg=(
                    f"cannot find context '{context_id}' "
                    f"in session '{resolved_session_id}'"
                ),
        )
        return context

    def _get_react_config(self) -> ReActAgentConfig:
        """Return inner ReActAgent config for real and test agents."""
        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )
        config = getattr(
            self._react_agent,
            "config",
            getattr(self._react_agent, "_config", None),
        )
        if config is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="Inner ReActAgent config is not available.",
            )
        return config

    def _resolve_context_window_tokens(self) -> int:
        """Resolve the configured model context window size."""
        config = self._get_react_config().context_engine_config
        model_name = None
        if self._react_agent is not None:
            model_name = getattr(self._get_react_config(), "model_name", None)
        if config is not None and getattr(config, "model_name", None):
            model_name = config.model_name

        return ContextUtils.resolve_context_max(
            model_name=model_name,
            fallback_context_window_tokens=(
                config.context_window_tokens if config is not None else None
            ),
            model_context_window_tokens=(
                config.model_context_window_tokens if config is not None else None
            ),
        )

    def get_context_usage(
        self,
        session_id: Optional[str] = None,
        context_id: str = "default_context_id",
    ) -> Dict[str, Any]:
        """Get current context occupancy statistics.

        The underlying ``ContextStats`` already prefers the latest
        ``AssistantMessage.usage_metadata`` returned by the real model when
        present; otherwise it falls back to local token counting.
        """
        context = self._get_context_or_error(session_id, context_id)
        resolved_session_id = self._resolve_context_session_id(session_id)
        stats = context.statistic()
        context_window_tokens = self._resolve_context_window_tokens()
        usage_ratio = (
            stats.total_tokens / context_window_tokens
            if context_window_tokens > 0
            else 0.0
        )
        return {
            "session_id": resolved_session_id,
            "context_id": context.context_id(),
            "total_tokens": stats.total_tokens,
            "context_window_tokens": context_window_tokens,
            "usage_ratio": usage_ratio,
            "usage_percent": round(usage_ratio * 100, 2),
            "stats": stats.model_dump(),
        }

    def get_context_occupancy(
        self,
        session_id: Optional[str] = None,
        context_id: str = "default_context_id",
    ) -> Dict[str, Any]:
        """Alias for ``get_context_usage``."""
        return self.get_context_usage(session_id, context_id)

    def get_current_context(
        self,
        session_id: Optional[str] = None,
        context_id: str = "default_context_id",
    ) -> List[Any]:
        """Return current context messages for the given session/context."""
        context = self._get_context_or_error(session_id, context_id)
        return context.get_messages()

    @staticmethod
    def _normalize_context_messages(
        messages: Optional[List[Any] | Any] = None,
    ) -> List[BaseMessage]:
        """Normalize initial message inputs for a fresh context."""
        if messages is None:
            return []
        raw_messages = (
            messages
            if isinstance(messages, list)
            else [messages]
        )
        normalized: List[BaseMessage] = []
        for message in raw_messages:
            if isinstance(message, BaseMessage):
                normalized.append(message)
            elif isinstance(message, str):
                normalized.append(SystemMessage(content=message))
            elif isinstance(message, dict):
                normalized.append(SystemMessage(**message))
            else:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg=(
                        "messages must be a string, dict, "
                        "BaseMessage, or a list of those values."
                    ),
                )
        return normalized

    async def create_new_context_engine(
        self,
        session_id: Optional[str] = None,
        messages: Optional[List[Any] | Any] = None,
    ) -> str:
        """Create a fresh context in the inner ContextEngine and return its session id."""
        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        new_session_id = session_id or str(uuid.uuid4())
        normalized_messages = self._normalize_context_messages(
            messages
        )
        await self._react_agent.context_engine.create_context(
            session=Session(session_id=new_session_id, card=self.card),
            history_messages=normalized_messages,
        )
        return new_session_id

    async def new_context_engine(
        self,
        session_id: Optional[str] = None,
        messages: Optional[List[Any] | Any] = None,
    ) -> str:
        """Alias for ``create_new_context_engine``."""
        return await self.create_new_context_engine(
            session_id,
            messages,
        )

    @property
    def loop_coordinator(self) -> Optional[LoopCoordinator]:
        """LoopCoordinator for the outer task loop."""
        return self._loop_coordinator

    @property
    def loop_controller(self) -> Optional[TaskLoopController]:
        """LoopController for the outer task loop."""
        return self._loop_controller

    @property
    def event_queue(self) -> Optional["EventQueue"]:
        """EventQueue for the current task loop."""
        if self._loop_controller is None:
            return None
        return self._loop_controller.event_queue

    @property
    def event_handler(self) -> Optional[TaskLoopEventHandler]:
        """EventHandler for the current task loop."""
        if self._loop_controller is None:
            return None
        return cast(
            TaskLoopEventHandler,
            self._loop_controller.event_handler,
        )

    def _create_react_agent(self) -> ReActAgent:
        """Build the internal ReActAgent from current DeepAgentConfig."""
        cfg = self._deep_config
        if cfg is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="DeepAgentConfig is required. Call configure() first.",
            )

        inner_card = AgentCard(
            name=f"{self.card.name}_react",
            description=self.card.description or "",
        )

        react_config = ReActAgentConfig()
        react_config.max_iterations = (
            sys.maxsize
            if cfg.enable_task_loop
            else cfg.max_iterations
        )
        if cfg.context_engine_config is not None:
            react_config.context_engine_config = cfg.context_engine_config
        react_config.workspace = cfg.workspace
        if cfg.sys_operation is not None:
            react_config.sys_operation_id = cfg.sys_operation.id

        react_config.parallel_tool_calls = cfg.parallel_tool_calls

        language = resolve_language(cfg.language)
        mode = resolve_mode(cfg.prompt_mode)
        prompt_builder = SystemPromptBuilder(language=language, mode=mode)
        if cfg.system_prompt:
            # Wrap the provided prompt as the identity section so all
            # rails can consistently operate on prompt_builder.
            prompt_builder.add_section(PromptSection(
                name=SectionName.IDENTITY,
                content={"cn": cfg.system_prompt, "en": cfg.system_prompt},
                priority=10,
            ))
        else:
            prompt_builder.add_section(build_identity_section(language))
        prompt = prompt_builder.build()
        react_config.prompt_template = [{"role": "system", "content": prompt}]

        if cfg.model is not None:
            model = cfg.model
            if model.model_client_config is not None:
                react_config.model_client_config = model.model_client_config
            if model.model_config is not None:
                react_config.model_config_obj = model.model_config
                if model.model_config.model_name:
                    react_config.model_name = model.model_config.model_name

        agent = ReActAgent(inner_card)
        agent.configure(react_config)

        # Store builder on both agents (same object):
        #   - DeepAgent.system_prompt_builder: rails initialized on the outer
        #     agent pick it up as the public contract.
        #   - ReActAgent.system_prompt_builder: bridged BEFORE_MODEL_CALL rails
        #     mutate the same builder object before _railed_model_call builds it.
        self.system_prompt_builder = prompt_builder
        agent.prompt_builder = prompt_builder
        agent.system_prompt_builder = prompt_builder
        agent.prompt_attachment_manager = self.prompt_attachment_manager

        # Share ability manager so tools registered on DeepAgent are visible inside.
        agent.ability_manager = self.ability_manager

        # Inject pre-built Model to bypass lazy init.
        if cfg.model is not None:
            agent.set_llm(cfg.model)

        return agent

    async def _register_pending_mcps(self) -> None:
        """Register configured MCP servers before rails initialize."""
        cfg = self._deep_config
        if cfg is None or not cfg.mcps:
            return

        for mcp_config in cfg.mcps:
            existing_config = Runner.resource_mgr.get_mcp_server_config(mcp_config.server_id)
            if existing_config is None:
                result = await Runner.resource_mgr.add_mcp_server(
                    mcp_config,
                    tag=self.card.id,
                )
                if result.is_err():
                    raise result.msg()
            else:
                if existing_config.model_dump() != mcp_config.model_dump():
                    raise build_error(
                        StatusCode.RESOURCE_MCP_SERVER_ADD_ERROR,
                        server_config=mcp_config,
                        reason=(
                            f"server_id '{mcp_config.server_id}' is already registered "
                            "with a different config"
                        ),
                    )

                tag_result = Runner.resource_mgr.add_resource_tag(
                    mcp_config.server_id,
                    self.card.id,
                )
                if tag_result.is_err():
                    raise tag_result.msg()

                for tool_id in Runner.resource_mgr.get_mcp_tool_ids(mcp_config.server_id):
                    tag_result = Runner.resource_mgr.add_resource_tag(tool_id, self.card.id)
                    if tag_result.is_err():
                        raise tag_result.msg()

            self.ability_manager.add(mcp_config)

    async def _resolve_read_image_multimodal(self) -> None:
        """Probe the agent model when read_file image modality is set to auto."""
        config = self._deep_config
        if config is None or config.enable_read_image_multimodal is not None:
            return

        if config.model is None:
            logger.debug(
                "[DeepAgent] no model configured; disabling read_file image multimodal",
            )
            config.enable_read_image_multimodal = False
            return

        supported = await probe_image_support(config.model)
        if supported is None:
            logger.warning(
                "[DeepAgent] image multimodal probe inconclusive; "
                "leaving auto and degrading to metadata-only for this run",
            )
            return

        config.enable_read_image_multimodal = supported
        logger.info(
            "[DeepAgent] read_file image multimodal auto-detected: %s",
            supported,
        )

    async def _ensure_initialized(self) -> None:
        """Perform lazy async initialization."""
        if self._initialized:
            return

        # Initialize ContextVar CWD in the current asyncio Task context.
        # Each agent sets its own CWD unconditionally — ContextVar copies
        # are per-Task, so this won't affect the parent agent.
        if self._deep_config and self._deep_config.workspace:
            from openjiuwen.core.sys_operation.cwd import init_cwd

            init_root = self._deep_config.workspace.root_path or os.getcwd()
            init_cwd(
                init_root,
                workspace=self._deep_config.workspace.root_path,
            )

        await self._register_pending_mcps()

        if self._needs_workspace_init():
            await self.init_workspace()

        await self._resolve_read_image_multimodal()
        
        self._sync_prompt_builder_references()

        # Unregister stale rails left over from a previous configure() cycle.
        # Skip rails that are also in pending (same instance): they will be
        # re-initialized by the pending loop below without needing a full retire.
        for stale_rail in self._stale_rails:
            if stale_rail not in self._pending_rails:
                await self.unregister_rail(stale_rail)
        self._stale_rails.clear()

        for rail_inst in self._pending_rails:
            if isinstance(rail_inst, TaskCompletionRail):
                self._task_completion_rail = rail_inst
            if isinstance(rail_inst, DeepAgentRail):
                rail_inst.set_sys_operation(self._deep_config.sys_operation)
                rail_inst.set_workspace(self._deep_config.workspace)
            rail_inst.init(self)
            await self._register_rail_selective(rail_inst)
        self._pending_rails.clear()
        self._sync_prompt_builder_references()
        self._initialized = True

    def _needs_workspace_init(self) -> bool:
        """Check if workspace initialization is needed."""
        config = self._deep_config
        if config:
            return (
                    config.workspace is not None
                    and config.sys_operation is not None
                    and config.auto_create_workspace
            )
        return False

    async def ensure_initialized(self) -> None:
        """Perform lazy initialization. For testing only."""
        await self._ensure_initialized()

    async def init_workspace(self) -> None:
        """Initialize the workspace with directory structure and default content.

        Only creates directories/files if they don't exist, so it's safe to call
        multiple times.
        """
        from openjiuwen.harness.workspace.directory_builder import DirectoryBuilder

        if self._deep_config and self._deep_config.workspace:
            root = Path(self._deep_config.workspace.root_path)
            if (root / ".workspace").exists():
                return

            builder = DirectoryBuilder(
                sys_operation=self._deep_config.sys_operation,
                root_path=self._deep_config.workspace.root_path,
            )
            await builder.build(self._deep_config.workspace.directories)

    @property
    def loop_session(self) -> Optional[Session]:
        """The active loop session, or None if no session is running."""
        return self._loop_session

    @property
    def react_config(self) -> ReActAgentConfig:
        """React agent config. For testing only."""
        return self._react_agent.config

    def create_subagent(
        self,
        subagent_type: str,
        subsession_id: str,
        browser_capabilities: Optional[List[str]] = None,
    ) -> "DeepAgent":
        """Create a subagent instance (shared by TaskTool and SessionSpawnExecutor).

        Args:
            subagent_type: Type of subagent to create (e.g., "general-purpose").
            subsession_id: The session id for the subagent.
            browser_capabilities: Task-scoped browser capability categories.

        Returns:
            Configured DeepAgent instance.

        Raises:
            BaseError: If subagent spec not found.
        """
        spec = self._find_subagent_spec(subagent_type)
        if spec is None:
            raise build_error(
                StatusCode.DEEPAGENT_CREATE_SUBAGENT_NOT_FOUND,
                error_msg=f"Subagent spec not found: {subagent_type}",
            )

        if isinstance(spec, DeepAgent):
            logger.info("already get deepagent instance, return it")
            return spec

        if not self._deep_config.workspace or isinstance(self._deep_config.workspace, str):
            workspace_path = (
                str(Path(self._deep_config.workspace) / _SUB_AGENTS_DIR / subsession_id)
                if self._deep_config.workspace
                else str(Path(".") / _SUB_AGENTS_DIR / subsession_id)
            )
            workspace = Workspace(
                root_path=workspace_path,
                language=self._deep_config.language
            )
        else:
            workspace = Workspace(
                root_path=Path(self._deep_config.workspace.root_path) / _SUB_AGENTS_DIR / subsession_id,
                language=self._deep_config.language
            )

        create_kwargs = {
            "model": spec.model or self._deep_config.model,
            "card": spec.agent_card,
            "system_prompt": spec.system_prompt,
            "tools": spec.tools,
            "mcps": spec.mcps,
            "rails": spec.rails,
            "enable_task_loop": spec.enable_task_loop,
            "max_iterations": (
                spec.max_iterations
                if spec.max_iterations is not None
                else self._deep_config.max_iterations
            ),
            "workspace": (
                spec.workspace
                if spec.workspace is not None
                else workspace
            ),
            "skills": spec.skills,
            "backend": (
                spec.backend
                if spec.backend is not None
                else self._deep_config.backend
            ),
            "sys_operation": (
                spec.sys_operation
                if spec.sys_operation is not None and spec.workspace is not None
                else None
            ),
            "language": (
                spec.language
                if spec.language is not None
                else self._deep_config.language
            ),
            "prompt_mode": (
                spec.prompt_mode
                if spec.prompt_mode is not None
                else self._deep_config.prompt_mode
            ),
            "subagents": None,
            "enable_async_subagent": False,
            "add_general_purpose_agent": False,
            "enable_plan_mode": spec.enable_plan_mode,
            "parallel_tool_calls": spec.parallel_tool_calls,
            "restrict_to_work_dir": spec.restrict_to_work_dir or self._deep_config.restrict_to_work_dir,
        }

        if spec.factory_name:
            normalized_factory = (spec.factory_name or "").strip().lower()
            if normalized_factory in {"browser_agent", "browser_runtime"}:
                from openjiuwen.harness.subagents.browser_agent import (
                    create_browser_agent,
                )

                factory_kwargs = dict(spec.factory_kwargs or {})
                if browser_capabilities is not None:
                    factory_kwargs["browser_capabilities"] = list(browser_capabilities)
                return create_browser_agent(**create_kwargs, **factory_kwargs)
            if normalized_factory == "code_agent":
                from openjiuwen.harness.subagents.code_agent import (
                    create_code_agent,
                )

                return create_code_agent(
                    **create_kwargs,
                    **dict(spec.factory_kwargs or {}),
                )
            if normalized_factory == "research_agent":
                from openjiuwen.harness.subagents.research_agent import (
                    create_research_agent,
                )

                return create_research_agent(
                    **create_kwargs,
                    **dict(spec.factory_kwargs or {}),
                )
            if normalized_factory in {"mobile_gui_agent", "mobile_agent"}:
                from openjiuwen.harness.subagents.mobile_gui_agent import (
                    create_mobile_gui_agent,
                )

                return create_mobile_gui_agent(
                    **create_kwargs,
                    **dict(spec.factory_kwargs or {}),
                )

            raise build_error(
                StatusCode.DEEPAGENT_CREATE_SUBAGENT_NOT_FOUND,
                error_msg=f"Unsupported subagent factory: {spec.factory_name}",
            )

        from openjiuwen.harness.factory import create_deep_agent

        return create_deep_agent(**create_kwargs, **dict(spec.factory_kwargs or {}))

    def _find_subagent_spec(self, subagent_type: str) -> Optional["SubAgentConfig | DeepAgent"]:
        """Find SubAgentConfig matching subagent_type.

        Args:
            subagent_type: Type of subagent to find.

        Returns:
            Matching SubAgentConfig or DeepAgent instance, or None.
        """
        if self._deep_config is None:
            return None

        from openjiuwen.harness.schema.config import SubAgentConfig

        for spec in self._deep_config.subagents or []:
            if isinstance(spec, SubAgentConfig) and spec.agent_card.name == subagent_type:
                return spec
            if isinstance(spec, DeepAgent):
                card = getattr(spec, "card", None)
                if getattr(card, "name", None) == subagent_type:
                    return spec

        return None

    def _normalize_inputs(self, inputs: Any) -> InvokeInputs:
        """Parse user inputs into typed InvokeInputs."""
        if isinstance(inputs, dict):
            query = inputs.get("query", "")
            conversation_id = inputs.get("conversation_id")
            run = inputs.get("run", {})
            run_kind = None
            run_context = None
            if run:
                kind = run.get("kind", "normal")
                run_kind = RunKind(kind)
                context_data = run.get("context")
                if context_data:
                    _rc_fields = {f.name for f in dataclasses.fields(RunContext)}
                    extra = dict(context_data.get("extra") or {})
                    for k, v in context_data.items():
                        if k not in _rc_fields:
                            extra[k] = v
                    rc_kwargs = {
                        k: v for k, v in context_data.items()
                        if k in _rc_fields
                    }
                    rc_kwargs["extra"] = extra
                    run_context = RunContext(**rc_kwargs)
            # Merge raw_query into RunContext.extra
            # (without affecting run_kind for normal queries)
            raw_query = inputs.get("raw_query", "")
            if raw_query:
                if run_context is None:
                    run_context = RunContext(extra={"raw_query": raw_query})
                else:
                    run_context.extra["raw_query"] = raw_query
        elif isinstance(inputs, str):
            query = inputs
            conversation_id = None
            run_kind = None
            run_context = None
        elif isinstance(inputs, InteractiveInput):
            query = inputs
            conversation_id = None
            run_kind = None
            run_context = None
        else:
            raise build_error(
                StatusCode.DEEPAGENT_INPUT_PARAM_ERROR,
                error_msg="Input must be dict with 'query', str, or InteractiveInput.",
            )

        invoke_inputs = InvokeInputs(
            query=query,
            conversation_id=conversation_id,
            run_kind=run_kind,
            run_context=run_context
        )
        return invoke_inputs

    @staticmethod
    def _copy_run_context_with_raw_query(
        run_context: RunContext,
        raw_query: str,
    ) -> RunContext:
        """Return a per-round RunContext with refreshed raw_query."""
        extra = dict(run_context.extra)
        extra["raw_query"] = raw_query
        return RunContext(
            reason=run_context.reason,
            session_id=run_context.session_id,
            context_mode=run_context.context_mode,
            extra=extra,
        )

    @classmethod
    def _run_context_for_task_loop_round(
        cls,
        run_context: Optional[RunContext],
        current_query: Any,
        is_follow_up: bool,
    ) -> Optional[RunContext]:
        """Return the RunContext that should be passed to one task-loop round."""
        if run_context is None:
            return None
        if not is_follow_up:
            return run_context
        if not isinstance(current_query, str):
            return run_context
        if not current_query.strip():
            return run_context
        return cls._copy_run_context_with_raw_query(
            run_context,
            current_query,
        )

    @staticmethod
    def _to_effective_inputs(invoke_inputs: InvokeInputs) -> Dict[str, Any]:
        """Convert typed invoke inputs to ReAct input dict."""
        effective_inputs: Dict[str, Any] = {"query": invoke_inputs.query}
        if invoke_inputs.conversation_id is not None:
            effective_inputs["conversation_id"] = invoke_inputs.conversation_id
        if invoke_inputs.run_kind is not None:
            effective_inputs["run_kind"] = invoke_inputs.run_kind
        if invoke_inputs.run_context is not None:
            effective_inputs["run_context"] = invoke_inputs.run_context
        return effective_inputs

    @staticmethod
    def _is_resume_input(invoke_inputs: InvokeInputs) -> bool:
        """Return True if the query is an InteractiveInput (interrupt resume)."""
        return isinstance(invoke_inputs.query, InteractiveInput)

    @staticmethod
    def _result_from_stream_chunk(chunk: Any, output_parts: List[str]) -> Optional[Dict[str, Any]]:
        """Build an invoke-style result from emitted stream chunks."""
        chunk_type = getattr(chunk, "type", None)
        payload = getattr(chunk, "payload", None)
        if isinstance(chunk, dict):
            chunk_type = chunk.get("type", chunk_type)
            payload = chunk.get("payload", payload)

        if not isinstance(payload, dict):
            return None

        result: Optional[Dict[str, Any]] = None
        if chunk_type == "llm_output":
            content = payload.get("content")
            if isinstance(content, str):
                output_parts.append(content)
        elif chunk_type == "answer":
            result = dict(payload)
            result.setdefault("result_type", "answer")
            if "output" not in result:
                content = result.get("content")
                if isinstance(content, str):
                    output_parts.append(content)
                    result["output"] = content
                else:
                    result = None
        return result

    def add_rail(self, rail: AgentRail) -> "DeepAgent":
        """Synchronously queue a rail for registration.

        When a TaskCompletionRail is added it replaces any
        previously queued one (default or user-provided).
        """
        if isinstance(rail, TaskCompletionRail):
            # Remove any existing TaskCompletionRail (auto-default
            # or a previously queued user rail).
            self._pending_rails = [
                r for r in self._pending_rails
                if not isinstance(r, TaskCompletionRail)
            ]
        self._pending_rails.append(rail)
        return self

    def configured_rails(self) -> List[AgentRail]:
        """Return all queued + registered rails (pending first, then registered).

        Public accessor for hosts that need to copy a template agent's full rail
        set without touching DeepAgent's internal rail lists directly.
        """
        return [*self._pending_rails, *self._registered_rails]

    def find_rails_by_type(self, rail_types: tuple[type, ...]) -> List[AgentRail]:
        """Return queued + registered rails matching any of the given types.

        Public counterpart to ``strip_rails_by_type`` for callers that need to
        locate a rail instance without touching DeepAgent's internal rail lists
        directly.
        """
        if not rail_types:
            return []
        return [rail for rail in (*self._pending_rails, *self._registered_rails) if isinstance(rail, rail_types)]

    def find_rail_by_name(self, name: str) -> Optional[AgentRail]:
        """Return a pending or registered rail by its materialized class name."""
        return next(
            (
                rail
                for rail in (*self._pending_rails, *self._registered_rails)
                if type(rail).__name__ == name
            ),
            None,
        )

    def find_pending_rails_by_type(self, rail_type: type) -> List[AgentRail]:
        """Return pending rails matching the given type."""
        return [rail for rail in self._pending_rails if isinstance(rail, rail_type)]

    def is_pending_rail(self, rail: AgentRail) -> bool:
        """Return True when rail is queued for lazy registration."""
        return rail in self._pending_rails

    def is_registered_rail(self, rail: AgentRail) -> bool:
        """Return True when rail is registered in the active runtime."""
        return rail in self._registered_rails

    def remove_pending_rail(self, rail: AgentRail) -> "DeepAgent":
        """Remove a specific rail from the pending queue by object identity."""
        self._pending_rails = [queued for queued in self._pending_rails if queued is not rail]
        return self

    def strip_rails_by_type(self, rail_types: tuple[type, ...]) -> int:
        """Remove queued rails by type and mark registered ones as stale.

        This provides a public API for callers that need to replace built-in rails
        without touching DeepAgent's internal rail lists directly.
        """
        if not rail_types:
            return 0

        removed = 0
        before = len(self._pending_rails)
        self._pending_rails = [r for r in self._pending_rails if not isinstance(r, rail_types)]
        removed += before - len(self._pending_rails)

        for rail in list(self._registered_rails):
            if isinstance(rail, rail_types):
                self._stale_rails.append(rail)
                removed += 1

        return removed

    async def register_rail(self, rail: AgentRail) -> "DeepAgent":
        """Register a rail with selective routing."""
        if isinstance(rail, TaskCompletionRail):
            self._task_completion_rail = rail
        if isinstance(rail, DeepAgentRail):
            rail.set_sys_operation(self.deep_config.sys_operation)
            rail.set_workspace(self.deep_config.workspace)
        self._sync_prompt_builder_references()
        rail.init(self)
        await self._register_rail_selective(rail)
        self._sync_prompt_builder_references()
        return self

    async def unregister_rail(self, rail: AgentRail) -> "DeepAgent":
        """Unregister a rail from pending/outer/inner."""
        # Remove queued instances first so lazy-init does not re-register stale rails.
        self._pending_rails = [queued for queued in self._pending_rails if queued is not rail]
        self._registered_rails = [r for r in self._registered_rails if r is not rail]

        if isinstance(rail, TaskCompletionRail) and self._task_completion_rail is rail:
            self._task_completion_rail = None

        # Remove from outer DeepAgent.
        await self._agent_callback_manager.unregister_rail(rail, self)

        # Remove bridged callbacks from inner agent.
        if self._react_agent is not None:
            await self._react_agent.agent_callback_manager.unregister_rail(
                rail, self._react_agent
            )
        rail.uninit(self)
        return self

    async def load_expert_harness(
        self,
        path: str,
        *,
        context: BuildContext | None = None,
    ) -> LoadRecord:
        """Hot-load a file-backed ExpertHarness package.

        Reads the manifest at ``path`` into an ExpertHarnessSpec, then delegates
        to ``load_expert_harness_from_spec``.
        """
        try:
            spec = load_expert_harness_spec(path)
        except Exception as exc:
            raise build_error(
                StatusCode.DEEPAGENT_LOAD_EXPERT_HARNESS_ERROR,
                error_msg=str(exc),
                cause=exc,
            ) from exc
        return await self.load_expert_harness_from_spec(spec, context=context)

    async def load_expert_harness_from_spec(
        self,
        spec: ExpertHarnessSpec,
        *,
        context: BuildContext | None = None,
    ) -> LoadRecord:
        """Hot-load an in-memory ExpertHarnessSpec via resolve Parts + apply_hot."""
        try:
            if context is not None:
                ctx = context.derive()
                ctx.extras = dict(ctx.extras)
            else:
                ctx = BuildContext(
                    language=self.deep_config.language or "cn",
                    workspace=self.deep_config.workspace,
                    member_card_id=self.card.id,
                )
            ctx.extras.setdefault(
                "source_root",
                (spec.source.root if spec.source else ".") or ".",
            )
            ctx.extras["_parent_model"] = self.deep_config.model

            from openjiuwen.harness.resources.expert_harness_parts import (
                resolve_expert_harness_parts,
            )
            from openjiuwen.harness.expert_harness_runtime import apply_expert_harness_hot

            parts = resolve_expert_harness_parts(spec, ctx)
            source_uri = None
            if spec.source is not None:
                source_uri = spec.source.uri or spec.source.root
            record = LoadRecord(
                source_uri=source_uri,
                refs=await apply_expert_harness_hot(self, parts),
            )
            self._load_records[record.load_id] = record.model_copy(deep=True)
            return record
        except Exception as exc:
            raise build_error(
                StatusCode.DEEPAGENT_LOAD_EXPERT_HARNESS_ERROR,
                error_msg=str(exc),
                cause=exc,
            ) from exc

    async def load_expert_harness_ability(
        self,
        *,
        tools: Tool | ToolCard | list[Tool | ToolCard] | None = None,
        rails: AgentRail | list[AgentRail] | None = None,
        skills: ResolvedSkill | list[ResolvedSkill] | None = None,
    ) -> LoadRecord:
        """Hot-load pre-built tools / rails / skills onto this agent.

        Accepts already-constructed instances only. Assembles
        ``ExpertHarnessParts`` and delegates to ``apply_expert_harness_hot``.
        Unload via :meth:`unload_expert_harness`.
        """
        try:
            from openjiuwen.harness.expert_harness_runtime import apply_expert_harness_hot

            def _as_ability_list(value):
                if value is None:
                    return []
                if isinstance(value, list):
                    return [item for item in value if item is not None]
                return [value]

            parts = ExpertHarnessParts(
                tools=_as_ability_list(tools),
                rails=_as_ability_list(rails),
                skills=_as_ability_list(skills),
            )
            record = LoadRecord(refs=await apply_expert_harness_hot(self, parts))
            self._load_records[record.load_id] = record.model_copy(deep=True)
            return record
        except Exception as exc:
            raise build_error(
                StatusCode.DEEPAGENT_LOAD_EXPERT_HARNESS_ERROR,
                error_msg=str(exc),
                cause=exc,
            ) from exc

    async def unload_expert_harness(self, record: LoadRecord) -> list[str]:
        """Unload resources produced by a successful ExpertHarness load.

        Resolves ``record.load_id`` against this agent's ``_load_records`` ledger.
        Unknown / already-unloaded ids are a no-op. Only the ledger-owned refs
        are applied; the caller's ``record.refs`` are ignored.
        """
        try:
            from openjiuwen.harness.expert_harness_runtime import unapply_expert_harness_hot

            owned = self._load_records.get(record.load_id)
            if owned is None:
                return []
            labels = await unapply_expert_harness_hot(self, owned.refs)
            self._load_records.pop(record.load_id, None)
            return labels
        except Exception as exc:
            raise build_error(
                StatusCode.DEEPAGENT_LOAD_EXPERT_HARNESS_ERROR,
                error_msg=str(exc),
                cause=exc,
            ) from exc

    async def load_harness_config(self, config_path: str) -> list[str]:
        """Deprecated hot-load entry. Use :meth:`load_expert_harness` instead.

        Args:
            config_path: Path to a harness_config.yaml manifest or its parent
                directory. Resolved the same way as ``load_expert_harness``.

        Returns:
            List of human-readable resource labels (same shape the old
            implementation returned).
        """
        warnings.warn(
            "DeepAgent.load_harness_config is deprecated; "
            "use load_expert_harness instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        record = await self.load_expert_harness(config_path)
        return self._load_record_labels(record)

    async def unload_harness_config(self, config_path: str) -> list[str]:
        """Deprecated unload entry. Use :meth:`unload_expert_harness` instead.

        Locates the ``LoadRecord`` previously produced by ``load_harness_config``
        via ``_load_records`` (keyed by the resolved manifest path),
        then delegates to ``unload_expert_harness``. Returns an empty list when
        no matching record is found, mirroring the old "no-op on missing" behavior.

        Args:
            config_path: Path originally passed to ``load_harness_config``.
        """
        warnings.warn(
            "DeepAgent.unload_harness_config is deprecated; "
            "use unload_expert_harness instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        try:
            resolved = str(find_expert_harness_manifest(config_path))
        except FileNotFoundError:
            return []
        target_record: LoadRecord | None = None
        for record in self._load_records.values():
            if record.source_uri == resolved:
                target_record = record
                break
        if target_record is None:
            return []
        return await self.unload_expert_harness(target_record)

    @staticmethod
    def _load_record_labels(record: LoadRecord) -> list[str]:
        return [f"{ref.kind.value}:{ref.identity}" for ref in record.refs]

    def _runtime_extension_session_id(self) -> str:
        """Return a stable namespace key for runtime extension imports."""
        return (
            self._bound_session_id
            or self.card.id
            or self.card.name
            or "deep_agent"
        )

    def enqueue_harness_config(
        self, config_path: str,
    ) -> None:
        """Schedule a harness_config.yaml for loading on next stream() call."""
        self._pending_harness_configs.append(config_path)

    async def _drain_pending_harness_configs(
        self,
    ) -> None:
        """Load any enqueued harness configs before processing a query."""
        while self._pending_harness_configs:
            path = self._pending_harness_configs.pop(0)
            try:
                record = await self.load_expert_harness(path)
                logger.info(
                    "Auto-loaded harness config %s: %s",
                    path,
                    self._load_record_labels(record),
                )
            except Exception:
                logger.exception(
                    "Failed to load harness config: %s",
                    path,
                )

    async def _register_rail_selective(self, rail: AgentRail) -> None:
        """Route rail callbacks to the correct agent and record the rail as registered."""
        callbacks = rail.get_callbacks()

        for event, callback in callbacks.items():
            if event in _BRIDGE_EVENTS:
                if self._react_agent is not None:
                    await self._react_agent.register_callback(event, callback, rail.priority)
                continue

            if event in _OUTER_ONLY_EVENTS or event in _DEEP_EVENTS:
                await self.register_callback(event, callback, rail.priority)
                continue

            logger.warning(
                f"Unknown rail event {event}, registering on outer DeepAgent"
            )
            await self.register_callback(event, callback, rail.priority)

        self._registered_rails.append(rail)

    async def _run_single_round_invoke(
        self, ctx: AgentCallbackContext, session: Optional[Session]
    ) -> Dict[str, Any]:
        """Invoke inner ReActAgent exactly once."""
        modified = ctx.inputs
        if not isinstance(modified, InvokeInputs):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="ctx.inputs must be InvokeInputs for single-round invoke.",
            )

        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        return await self._react_agent.invoke(
            self._to_effective_inputs(modified),
            session,
        )

    async def _setup_task_loop(
        self,
        session: Session,
    ) -> Tuple[LoopCoordinator, TaskLoopController]:
        """Create or reuse Controller infrastructure for a task loop.

        Sets instance attributes and returns the key objects
        needed by the loop body.

        Args:
            session: Current session.

        Returns:
            (coordinator, controller)
        """
        session_id = session.get_session_id()

        # Reuse existing controller if session_id matches
        if (
            self._loop_controller is not None
            and self._bound_session_id == session_id
        ):
            coordinator = self._loop_coordinator
            if coordinator is None:
                evaluators = (
                    self._task_completion_rail.build_evaluators()
                    if self._task_completion_rail is not None
                    else []
                )
                coordinator = LoopCoordinator(evaluators=evaluators)
                self._loop_coordinator = coordinator
            coordinator.reset()
            return coordinator, self._loop_controller

        # New controller (first time or session switch)
        if self._loop_controller is not None:
            await self._force_cleanup_controller()

        evaluators = (
            self._task_completion_rail.build_evaluators()
            if self._task_completion_rail is not None
            else []
        )
        coordinator = LoopCoordinator(evaluators=evaluators)
        coordinator.reset()

        queues = LoopQueues()

        config = ControllerConfig(suppress_completion_signal=True)
        context_engine = ContextEngine()

        controller = TaskLoopController()
        controller.init(
            card=self.card,
            config=config,
            ability_manager=self.ability_manager,
            context_engine=context_engine,
        )

        from openjiuwen.harness.task_loop.session_spawn_executor import (
            SESSION_SPAWN_TASK_TYPE,
            build_session_spawn_executor,
        )
        # Register DEEP_TASK_TYPE and SESSION_SPAWN_TASK_TYPE executors
        controller.add_task_executor(
            DEEP_TASK_TYPE, build_deep_executor(self),
        ).add_task_executor(
            SESSION_SPAWN_TASK_TYPE, build_session_spawn_executor(self)
        )

        handler = TaskLoopEventHandler(self)
        handler.interaction_queues = queues
        controller.set_event_handler(handler)

        # Inject SessionToolkit to Handler
        toolkit = self._session_toolkit
        if toolkit is not None and hasattr(handler, "set_session_toolkit"):
            handler.set_session_toolkit(toolkit)

        self._loop_coordinator = coordinator
        self._loop_controller = controller
        self._loop_session = session

        return coordinator, controller

    # ----------------------------------------------------------------
    # State management
    # ----------------------------------------------------------------

    @staticmethod
    def _read_runtime_state(
        session: Session,
    ) -> Optional[DeepAgentState]:
        """Read the cached runtime state from session."""
        state = getattr(session, _SESSION_RUNTIME_ATTR, None)
        if state is None:
            return None
        if not isinstance(state, DeepAgentState):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg=(
                    "Invalid deepagent runtime state "
                    "type on session."
                ),
            )
        return state

    @staticmethod
    def _write_runtime_state(
        session: Session,
        state: DeepAgentState,
    ) -> None:
        """Write runtime state to session cache."""
        setattr(session, _SESSION_RUNTIME_ATTR, state)

    @staticmethod
    def _clear_runtime_state(session: Session) -> None:
        """Clear runtime state from session cache."""
        if hasattr(session, _SESSION_RUNTIME_ATTR):
            delattr(session, _SESSION_RUNTIME_ATTR)

    def load_state(self, session: Session) -> DeepAgentState:
        """Load deepagent runtime state from session.

        Returns the cached runtime object when available;
        otherwise loads from persisted session state and
        primes the cache.

        Args:
            session: Current session.

        Returns:
            Current DeepAgentState (never None).
        """
        state = self._read_runtime_state(session)
        if state is not None:
            return state
        data = session.get_state(_SESSION_STATE_KEY)
        loaded = (
            DeepAgentState.from_session_dict(data)
            if isinstance(data, dict)
            else DeepAgentState()
        )
        self._write_runtime_state(session, loaded)
        return loaded

    def save_state(
        self,
        session: Session,
        state: Optional[DeepAgentState] = None,
    ) -> None:
        """Persist deepagent state to session.

        When ``state`` is provided it becomes the new
        runtime snapshot; otherwise the currently cached
        state is persisted.

        Args:
            session: Current session.
            state: State to save; if None the cached
                state is used.
        """
        target = (
            state
            if state is not None
            else self._read_runtime_state(session)
        )
        if target is None:
            return
        self._write_runtime_state(session, target)
        session.update_state(
            {_SESSION_STATE_KEY: target.to_session_dict()}
        )

    def clear_state(
        self,
        session: Session,
        clear_persisted: bool = False,
    ) -> None:
        """Clear deepagent runtime cache from session.

        Args:
            session: Current session.
            clear_persisted: When True, also clears the
                persisted session snapshot.
        """
        self._clear_runtime_state(session)
        if clear_persisted:
            session.update_state({_SESSION_STATE_KEY: None})

    def switch_mode(self, session: Session, mode: str) -> None:
        """Switch agent mode, updating session-scoped PlanModeState.

        Args:
            session: Current session.
            mode: Target mode string — ``"plan"`` or ``"auto"``.
        """
        state = self.load_state(session)
        if state.plan_mode.mode == mode:
            state.plan_mode.pre_plan_mode = state.plan_mode.mode
            logger.info("[DeepAgent] mode in state is the same, no need to switch, mode: {}".format(mode))
            return
        state.plan_mode.pre_plan_mode = state.plan_mode.mode
        state.plan_mode.mode = mode
        self.save_state(session, state)

    def restore_mode_after_plan_exit(self, session: Session) -> None:
        """Restore the mode that was active before entering plan mode.

        Does **not** clear ``plan_slug`` so that the execution phase can
        still reference the plan file.

        Args:
            session: Current session.
        """
        state = self.load_state(session)
        state.plan_mode.mode = state.plan_mode.pre_plan_mode or "normal"
        state.plan_mode.pre_plan_mode = None
        self.save_state(session, state)

    def get_plan_file_path(self, session: Session) -> Path | None:
        """Derive the plan file path from the slug stored in session state.

        Args:
            session: Current session.

        Returns:
            Resolved ``Path`` to the plan Markdown file, or ``None`` if
            no slug has been set or the workspace is unavailable.
        """
        from openjiuwen.harness.tools import resolve_plan_file_path

        state = self.load_state(session)
        slug = state.plan_mode.plan_slug
        if not slug or not self._deep_config or not self._deep_config.workspace:
            return None
        return resolve_plan_file_path(
            self._deep_config.workspace.root_path, slug
        )

    def _has_remaining_tasks(self, session: Session) -> bool:
        """Check whether the task plan still has pending tasks."""
        st = self.load_state(session)
        if st.task_plan is None:
            return False
        return st.task_plan.get_next_task() is not None

    def _has_pending_session_spawn(self) -> bool:
        """Check if there are pending SESSION_SPAWN tasks.

        Returns:
            True if there are SUBMITTED/WORKING SESSION_SPAWN tasks.
        """
        if self._session_toolkit is None:
            return False

        pending_tasks = self._session_toolkit.list_all()
        return any(r.status == "running" for r in pending_tasks)

    async def _force_cleanup_controller(self) -> None:
        """Force cleanup of existing controller (on session switch)."""
        if self._loop_controller is None:
            return

        self._log_loop("forcing controller cleanup due to session switch")

        if self._loop_session is not None:
            try:
                await self._loop_controller.unbind_session(self._loop_session)
            except Exception as e:
                logger.warning(f"unbind_session failed during force cleanup: {e}")

        try:
            await self._loop_controller.stop()
        except Exception as e:
            logger.warning(f"controller.stop() failed during force cleanup: {e}")

        self._loop_coordinator = None
        self._loop_controller = None
        self._loop_session = None
        self._bound_session_id = None

    @staticmethod
    def _log_loop(msg: str, detail: str = "") -> None:
        """Log an outer-loop event respecting sensitivity."""
        if UserConfig.is_sensitive():
            logger.info(f"[OuterLoop] {msg}")
        else:
            logger.info(f"[OuterLoop] {msg}{detail}")

    async def schedule_auto_invoke_on_spawn_done(
        self,
        query: str,
        delay: float = 0.5,
    ) -> None:
        """Schedule delayed auto-invoke after SESSION_SPAWN completes (no active invoke).

        Called by TaskLoopEventHandler. Waits ``delay`` to merge concurrent completions,
        then invokes with a summary prompt if still idle and ``_loop_session`` is set.

        Args:
            query: The query to invoke with.
            delay: Delay in seconds to merge multiple concurrent spawn completions.
        """
        await asyncio.sleep(delay)
        self._auto_invoke_scheduled = False

        if self._invoke_active:
            return

        if self._loop_session is None:
            logger.warning("[AutoInvoke] session was cleaned up during delay, skipping")
            return

        try:
            await self.invoke(
                {"query": query},
                session=self._loop_session,
            )
        except Exception as e:
            logger.error(f"[AutoInvoke] auto-invoke failed: {e}", exc_info=True)

    async def _run_task_loop(
        self, ctx: AgentCallbackContext, session: Session,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Async generator for the outer task loop. Shared by invoke and stream.

        Args:
            ctx: Callback context with InvokeInputs.
            session: Current session.

        Yields:
            Result dict from each iteration.
        """
        modified = ctx.inputs
        if not isinstance(modified, InvokeInputs):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="ctx.inputs must be InvokeInputs for task-loop.",
            )

        coordinator, controller = await self._setup_task_loop(session)
        timeout = self._deep_config.completion_timeout if self._deep_config else 600.0

        # Only bind if session_id changed
        session_id = session.get_session_id()
        if self._bound_session_id != session_id:
            await controller.bind_session(session)
            self._bound_session_id = session_id

        try:
            current_query = modified.query
            outer_round = 0

            while coordinator.should_continue():
                outer_round += 1
                # Drain new follow-ups, merge into state buffer
                new_follow_ups = controller.drain_follow_up()
                _state = self.load_state(session)
                if new_follow_ups:
                    _state.pending_follow_ups.extend(
                        new_follow_ups
                    )
                # Pop first buffered follow-up as query
                is_follow_up = bool(
                    _state.pending_follow_ups
                )
                if _state.pending_follow_ups:
                    current_query = (
                        _state.pending_follow_ups.pop(0)
                    )
                    self.save_state(session, _state)
                round_run_context = self._run_context_for_task_loop_round(
                    modified.run_context,
                    current_query,
                    is_follow_up,
                )

                query_preview = str(current_query)[:120]
                self._log_loop(
                    f"round={outer_round} started",
                    f", query={query_preview}",
                )

                await controller.submit_round(
                    session, current_query,
                    is_follow_up=is_follow_up,
                    run_kind=modified.run_kind,
                    run_context=round_run_context,
                )
                result = await controller.wait_round_completion(timeout=timeout)

                result_type = result.get("result_type", "N/A")
                output_preview = str(result.get("output", ""))[:200]
                self._log_loop(
                    f"round={outer_round} completed, result_type={result_type}",
                    f", output={output_preview}",
                )

                yield result

                coordinator.increment_iteration()
                coordinator.set_last_result(result)
                _state = self.load_state(session)
                _state.stop_condition_state = coordinator.get_state()
                self.save_state(session, _state)

                if result.get("result_type") == "interrupt":
                    self._log_loop(
                        f"round={outer_round} interrupted"
                    )
                    break
                if coordinator.is_aborted:
                    self._log_loop(f"round={outer_round} aborted")
                    break
                if (
                    controller.has_follow_up()
                    or _state.pending_follow_ups
                ):
                    continue
                if not self._has_remaining_tasks(session):
                    self._log_loop("no remaining tasks, loop finished")
                    break

                current_query = modified.query

            stop_reason = coordinator.stop_reason
            if stop_reason:
                self._log_loop(
                    f"loop stopped by: {stop_reason}"
                )
        finally:
            # Clear stop_condition_state so the next invoke starts fresh.
            _state = self.load_state(session)
            _state.stop_condition_state = None
            self.save_state(session, _state)
            # Keep controller alive when SESSION_SPAWN tasks are pending.
            if self._has_pending_session_spawn():
                self._log_loop("pending SESSION_SPAWN tasks, controller kept alive")
            else:
                await controller.unbind_session(session)
                await controller.stop()
                self._loop_coordinator = None
                self._loop_controller = None
                self._loop_session = None
                self._bound_session_id = None
                self._log_loop("all tasks completed, controller cleaned up")

    async def _run_task_loop_invoke(
        self,
        ctx: AgentCallbackContext,
        session: Optional[Session],
    ) -> Dict[str, Any]:
        """Run the outer task loop, return last result.

        Args:
            ctx: Callback context with InvokeInputs.
            session: Current session.

        Returns:
            Result dict from the last iteration.
        """
        if session is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg=(
                    "session is required for "
                    "task-loop mode."
                ),
            )

        last_result: Dict[str, Any] = {}
        try:
            async for result in self._run_task_loop(
                ctx, session
            ):
                last_result = result
        except Exception as e:
            logger.error(f"Task loop invoke error: {e}", exc_info=True)
            raise
        return last_result

    async def _run_task_loop_stream(
        self,
        ctx: AgentCallbackContext,
        session: Optional[Session],
        stream_modes: Optional[List[StreamMode]],
    ) -> AsyncIterator[Any]:
        """Stream the outer task loop with per-token chunks.

        Uses the same coroutine pattern as ReActAgent.stream():
        background task runs _run_task_loop (which triggers
        invoke with _streaming=True), foreground reads from
        session.stream_iterator().

        Session lifecycle (pre_run/post_run) is managed by the
        caller (Runner or direct caller), not by this method.
        Only the stream emitter is closed here to send END_FRAME
        and unblock stream_iterator().

        Args:
            ctx: Callback context with InvokeInputs.
            session: Current session.
            stream_modes: Stream mode filters.

        Yields:
            OutputSchema chunks (llm_reasoning / llm_output / answer).
        """
        _ = stream_modes
        if session is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg=(
                    "session is required for "
                    "task-loop mode."
                ),
            )

        async def _stream_process() -> None:
            try:
                async for result in self._run_task_loop(ctx, session):
                    await self._write_round_result_to_stream(result, session)
            except Exception as e:
                logger.error(f"Task loop stream error: {e}", exc_info=True)
                error_result = {"output": str(e), "result_type": "error"}
                await self._write_round_result_to_stream(error_result, session)
            finally:
                # Only close the stream emitter to send END_FRAME,
                # so stream_iterator() can terminate.
                # Full session cleanup (post_run) is left to the caller.
                await session.close_stream()

        task = asyncio.create_task(_stream_process())
        self._stream_process_task = task
        try:
            async for chunk in session.stream_iterator():
                yield chunk

            # Normal completion: wait for background task
            await task
        except asyncio.CancelledError:
            # Cancel background task explicitly to speed up cleanup.
            # Without this, await task could wait for a long-running
            # operation (e.g., wait_round_completion with 600s timeout).
            await self._cancel_session_deep_tasks(session.get_session_id())
            await self._cancel_stream_process_task()
            raise
        finally:
            if self._stream_process_task is task:
                self._stream_process_task = None

    async def _write_round_result_to_stream(
        self,
        result: Dict[str, Any],
        session: Session,
    ) -> None:
        """Write a task-loop round result to the session stream.

        Delegates to the inner ReActAgent which already handles
        normal answers, HITL interrupts, and workflow interrupts
        consistently.

        Args:
            result: Result dict from the inner ReActAgent.
            session: Current session to write into.
        """
        if self._react_agent is not None:
            await self._react_agent.write_invoke_result_to_stream(
                result, session
            )

    async def _run_single_round_stream(
        self,
        ctx: AgentCallbackContext,
        session: Optional[Session],
        stream_modes: Optional[List[StreamMode]],
    ) -> AsyncIterator[Any]:
        """Stream from inner ReActAgent exactly once."""
        modified = ctx.inputs
        if not isinstance(modified, InvokeInputs):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="ctx.inputs must be InvokeInputs for single-round stream.",
            )

        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        async for chunk in self._react_agent.stream(
            self._to_effective_inputs(modified),
            session,
            stream_modes,
        ):
            yield chunk

    async def invoke(
        self,
        inputs: Any,
        session: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """Execute DeepAgent in single-round or task-loop mode."""
        await self._ensure_initialized()

        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        invoke_inputs = self._normalize_inputs(inputs)
        ctx = AgentCallbackContext(agent=self, inputs=invoke_inputs, session=session)

        self._invoke_active = True
        try:
            result: Dict[str, Any]
            async with ctx.lifecycle(
                AgentCallbackEvent.BEFORE_INVOKE,
                AgentCallbackEvent.AFTER_INVOKE,
            ):
                if (
                    self._deep_config is not None
                    and self._deep_config.enable_task_loop
                    and not self._is_resume_input(invoke_inputs)
                ):
                    result = await self._run_task_loop_invoke(ctx, session)
                else:
                    result = await self._run_single_round_invoke(ctx, session)
                invoke_inputs.result = result

            if session is not None:
                self.save_state(session)
                self.clear_state(session)
            return result
        finally:
            self._invoke_active = False

    async def stream(
        self,
        inputs: Any,
        session: Optional[Session] = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        """Stream execute DeepAgent in single-round or task-loop mode."""
        await self._ensure_initialized()
        await self._drain_pending_harness_configs()

        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        invoke_inputs = self._normalize_inputs(inputs)
        ctx = AgentCallbackContext(agent=self, inputs=invoke_inputs, session=session)

        self._invoke_active = True
        try:
            stream_result: Optional[Dict[str, Any]] = None
            stream_output_parts: List[str] = []
            async with ctx.lifecycle(
                AgentCallbackEvent.BEFORE_INVOKE,
                AgentCallbackEvent.AFTER_INVOKE,
            ):
                if (
                    self._deep_config is not None
                    and self._deep_config.enable_task_loop
                    and not self._is_resume_input(invoke_inputs)
                ):
                    async for chunk in self._run_task_loop_stream(
                        ctx, session, stream_modes
                    ):
                        chunk_result = self._result_from_stream_chunk(
                            chunk, stream_output_parts
                        )
                        if chunk_result is not None:
                            stream_result = chunk_result
                        yield chunk
                else:
                    async for chunk in self._run_single_round_stream(
                        ctx, session, stream_modes
                    ):
                        chunk_result = self._result_from_stream_chunk(
                            chunk, stream_output_parts
                        )
                        if chunk_result is not None:
                            stream_result = chunk_result
                        yield chunk

                if stream_result is None and stream_output_parts:
                    stream_result = {
                        "output": "".join(stream_output_parts),
                        "result_type": "answer",
                    }
                if stream_result is not None:
                    invoke_inputs.result = stream_result

            if session is not None:
                self.save_state(session)
                self.clear_state(session)
        finally:
            self._invoke_active = False

    async def follow_up(
        self,
        msg: str,
        task_id: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> None:
        """Publish a FollowUpEvent to the FOLLOW_UP topic.

        The event is handled concurrently with INPUT
        and does not block the current iteration.
        The handler pushes the message into
        LoopQueues.follow_up, which the outer
        loop drains after each iteration.

        Args:
            msg: Follow-up message text.
            task_id: Optional task to associate.
            session: Current session.
        """
        controller = self._loop_controller
        if controller is None:
            return
        sess = session or self._loop_session
        if sess is None:
            return
        event = FollowUpEvent.from_text(msg)
        if task_id:
            event.metadata = {"task_id": task_id}
        await controller.event_queue.publish_event_async(
            self.card.id, sess, event
        )

    async def steer(
        self,
        msg: str,
        session: Optional[Session] = None,
    ) -> None:
        """Publish a TaskInteractionEvent to TASK_INTERACTION topic.

        The event is handled concurrently with INPUT.
        The handler pushes the message into
        LoopQueues.steering, which the executor
        drains before the next inner invoke.

        Args:
            msg: Steering instruction text.
            session: Current session.
        """
        controller = self._loop_controller
        if controller is None:
            return
        sess = session or self._loop_session
        if sess is None:
            return
        from openjiuwen.core.controller.schema.dataframe import (
            TextDataFrame,
        )
        event = TaskInteractionEvent(
            interaction=[TextDataFrame(text=msg)]
        )
        await controller.event_queue.publish_event_async(
            self.card.id, sess, event
        )

    async def _cancel_stream_process_task(self) -> None:
        """Cancel the in-flight task-loop stream background task, if any."""
        task = self._stream_process_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug(
                "stream process task raised during cancel",
                exc_info=True,
            )

    async def _cancel_session_deep_tasks(self, session_id: str) -> None:
        """Cancel active DeepAgent round tasks for a session.

        Session-spawn tasks intentionally keep running after the foreground
        stream disconnects, so this only targets the current DeepAgent round.
        """
        controller = self._loop_controller
        if controller is None:
            return
        scheduler = controller.task_scheduler
        if scheduler is None or scheduler.task_manager is None:
            return

        try:
            tasks = await scheduler.task_manager.get_task(
                task_filter=TaskFilter(session_id=session_id)
            )
        except Exception as e:
            logger.warning(
                "Failed to list session tasks during stream cancel: %s",
                e,
                exc_info=True,
            )
            return

        cancellable = {
            TaskStatus.SUBMITTED,
            TaskStatus.WORKING,
        }
        for task in tasks:
            if task.task_type != DEEP_TASK_TYPE or task.status not in cancellable:
                continue
            try:
                await scheduler.cancel_task(task.task_id)
            except Exception as e:
                logger.warning(
                    "Failed to cancel deep task %s during stream cancel: %s",
                    task.task_id,
                    e,
                    exc_info=True,
                )

    async def abort(
        self,
        session: Optional[Session] = None,
    ) -> None:
        """Request immediate abort of the task loop.

        Sets the abort flag on the coordinator and
        calls on_abort() on the event handler so the
        outer loop can exit promptly. Also cancels the
        background ``_stream_process`` task when streaming,
        so consumer disconnect / interrupt stops promptly
        instead of leaving a zombie ReAct loop running.

        Args:
            session: Current session (unused).
        """
        _ = session
        coordinator = self._loop_coordinator
        controller = self._loop_controller
        if coordinator is not None and controller is not None:
            coordinator.request_abort()
            handler = cast(
                TaskLoopEventHandler,
                controller.event_handler,
            )
            await handler.on_abort()
        await self._cancel_stream_process_task()

    # ----------------------------------------------------------------
    # long-lived session
    # ----------------------------------------------------------------
    async def prepare_interaction_task_loop(
        self,
        session: Session,
    ) -> Tuple[LoopCoordinator, TaskLoopController]:
        """Prepare and bind the task-loop kernel for the long-lived interaction loop."""
        coordinator, controller = await self._setup_task_loop(session)
        session_id = session.get_session_id()
        if self._bound_session_id != session_id:
            await controller.bind_session(session)
            self._bound_session_id = session_id
        self._loop_session = session
        return coordinator, controller

    async def run_one_round(
        self,
        work: RoundWorkItem,
        task_id: str,
        session: Session,
    ) -> RoundOutcome:
        """Execute one interaction work item and optionally propose follow-up work."""
        self._invoke_active = True
        try:
            coordinator, controller = await self.prepare_interaction_task_loop(session)
            if work.reset_loop:
                coordinator.reset()
                state = self.load_state(session)
                state.stop_condition_state = None
                self.save_state(session, state)

            inputs = copy.deepcopy(work.inputs)
            if work.kind == "goal":
                inputs["run"] = {
                    "kind": "goal",
                    "context": copy.deepcopy(work.context),
                }
            invoke_inputs = self._normalize_inputs(inputs)
            ctx = AgentCallbackContext(
                agent=self, inputs=invoke_inputs, session=session
            )
            async with ctx.lifecycle(
                AgentCallbackEvent.BEFORE_INVOKE,
                AgentCallbackEvent.AFTER_INVOKE,
            ):
                await controller.submit_round(
                    session,
                    str(work.query),
                    is_follow_up=work.is_follow_up,
                    run_kind=invoke_inputs.run_kind,
                    run_context=invoke_inputs.run_context,
                    task_id=task_id,
                )
                timeout = (
                    self._deep_config.completion_timeout
                    if self._deep_config
                    else 600.0
                )
                result = await controller.wait_round_completion(timeout=timeout)
                await self._write_round_result_to_stream(result, session)
                invoke_inputs.result = result
                next_work = self._build_interaction_next_work(
                    work=work,
                    result=result,
                    session=session,
                    coordinator=coordinator,
                    controller=controller,
                )

            self.save_state(session)
            self.clear_state(session)
            return RoundOutcome(next_work=next_work)
        except Exception:
            logger.exception("[DeepAgent] interaction round execution failed")
            return RoundOutcome(
                error_code="round_execution_error",
                error_message="interaction round execution failed",
            )
        finally:
            self._invoke_active = False

    def _build_interaction_next_work(
        self,
        *,
        work: RoundWorkItem,
        result: Dict[str, Any],
        session: Session,
        coordinator: Any,
        controller: Any,
    ) -> Optional[RoundWorkItem]:
        """Decide whether another user work item should follow this round."""
        coordinator.increment_iteration()
        coordinator.set_last_result(result)
        state = self.load_state(session)
        state.stop_condition_state = coordinator.get_state()
        state.pending_follow_ups.extend(controller.drain_follow_up())
        self.save_state(session, state)

        if result.get("result_type") == "interrupt" or coordinator.is_aborted:
            return None
        if not coordinator.should_continue() or work.kind == "goal":
            return None

        state = self.load_state(session)
        if state.pending_follow_ups:
            query = state.pending_follow_ups.pop(0)
            self.save_state(session, state)
            inputs = copy.deepcopy(work.inputs)
            inputs["query"] = query
            return RoundWorkItem.user(
                request_id=work.request_id,
                inputs=inputs,
                is_follow_up=True,
                reset_loop=False,
            )
        if self._has_remaining_tasks(session):
            return RoundWorkItem.user(
                request_id=work.request_id,
                inputs=work.inputs,
                reset_loop=False,
            )
        return None

    @property
    def phase(self) -> InteractionPhase:
        return self._interaction_phase

    @property
    def interaction_started(self) -> bool:
        """Whether ``start()`` has bound this session's interaction loop."""
        return self._interaction_started

    @property
    def active_round(self) -> Optional[ActiveInteractionRound]:
        return self._active_interaction_round

    @property
    def event_manager(self) -> EventManager:
        return self._event_manager

    def has_output_stream(self) -> bool:
        return self._interaction_output.has_consumer()

    async def start(
        self,
        *,
        session: Optional[Session] = None,
    ) -> None:
        """Bind the task-loop kernel and start the passive output forwarder.

        Hosts should pass the product ``Session`` they own.  When ``session`` is
        omitted, a fresh agent session is created with id ``\"default\"`` (useful
        for unit tests and simple embeddings).
        """
        async with self._interaction_start_lock:
            if session is None:
                from openjiuwen.core.session.agent import create_agent_session

                session = create_agent_session(
                    session_id="default",
                    card=getattr(self, "card", None),
                )
                await session.pre_run(inputs={})

            sid = session.get_session_id()

            if self._interaction_started:
                if self._bound_session_id == sid:
                    return
                raise RuntimeError(
                    f"Interaction loop already bound to session {self._bound_session_id}; "
                    f"cannot bind {sid}."
                )

            self._interaction_session = session
            await self.prepare_interaction_task_loop(session)
            if self._task_completion_rail is None:
                await self.register_rail(TaskCompletionRail())

            from openjiuwen.harness.goal.store import SessionGoalStore

            self.goal_manager = GoalManager(
                store=SessionGoalStore(session),
                event_manager=self._event_manager,
                control_lock=self._interaction_control_lock,
                has_output_stream=self.has_output_stream,
                cancel_active_round=self._cancel_active_round,
                emit_event=self._emit_interaction_event,
                notify_work=self._notify_work,
            )
            self._interaction_phase = InteractionPhase.IDLE
            self._interaction_started = True
            self._interaction_forwarder_task = asyncio.create_task(
                self._forward_session_stream(), name=f"interaction_forwarder[{sid}]"
            )

            rail = self._task_completion_rail
            if rail is not None and hasattr(rail, "set_goal_manager"):
                rail.set_goal_manager(self.goal_manager)
                try:
                    rail.init(self)
                except Exception:
                    logger.exception("[DeepAgent] Failed to register goal tools")

            self._ensure_supervisor_running()
            logger.info("[DeepAgent] Started for session %s", sid)

    async def stop(self) -> None:
        """Terminate the interaction loop and wake any attached output consumer."""
        if not self._interaction_started:
            return
        self._interaction_phase = InteractionPhase.TERMINATED
        await self._interaction_output.shutdown()
        self._event_manager.discard_all_work()
        await self._cancel_active_round(reason="stop")

        for task in (self._interaction_supervisor_task, self._interaction_forwarder_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        self._interaction_supervisor_task = None
        self._interaction_forwarder_task = None

        emit_tasks = list(self._interaction_emit_tasks)
        self._interaction_emit_tasks.clear()
        for task in emit_tasks:
            if not task.done():
                task.cancel()
        for task in emit_tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task

        if self._interaction_session is not None:
            with suppress(Exception):
                await self._interaction_session.close_stream()
        controller = self.loop_controller
        if controller is not None and self._interaction_session is not None:
            with suppress(Exception):
                await controller.unbind_session(self._interaction_session)
            with suppress(Exception):
                await controller.stop()

        self._interaction_started = False
        self._active_interaction_round = None
        self._interaction_round_task = None

    async def attach_output(self) -> Optional[InteractionOutputStream]:
        """Claim the sole output reader for this interaction.

        Returns ``None`` when another consumer already holds the lease.

        If an ACTIVE goal exists, missing goal work is ensured even when this
        call does not obtain the lease, so an already-attached reader continues
        to receive goal progress (session switch / concurrent attach_goal).
        """
        if not self._interaction_started or self._interaction_phase is InteractionPhase.TERMINATED:
            raise RuntimeError("interaction_terminated")

        async with self._interaction_send_lock:
            async with self._interaction_control_lock:
                stream = await self._attach_output_locked()
                if self.goal_manager is not None:
                    record = self._load_goal_record_locked()
                    if record is not None and record.status is GoalStatus.ACTIVE:
                        self.goal_manager.ensure_active_goal_work_locked()
                        self._notify_work()
                return stream

    async def send_input(self, request: SendInputRequest) -> None:
        """Dispatch user text only; does not attach or return an output stream.

        Hosts that need to read output must call ``attach_output`` first (or
        already hold a stream).  Steer / follow-up requests typically skip
        attach and let the existing consumer receive output.
        """
        if not self._interaction_started or self._interaction_phase is InteractionPhase.TERMINATED:
            raise RuntimeError("interaction_terminated")

        async with self._interaction_send_lock:
            await self._send_user(request)

    async def _send_user(self, request: SendInputRequest) -> None:
        inputs = request.inputs
        if (
            not isinstance(inputs, dict)
            or not isinstance(inputs.get("query"), str)
            or not inputs["query"].strip()
        ):
            raise ValueError("send_input requires a non-empty inputs['query']")

        async with self._interaction_control_lock:
            loop = self.loop_controller
            try:
                mode = (
                    InputDispatchMode.FOLLOW_UP
                    if request.mode is None
                    else InputDispatchMode(request.mode)
                )
            except ValueError as exc:
                raise ValueError(f"unsupported input dispatch mode: {request.mode}") from exc

            if mode is InputDispatchMode.STEER and self._active_interaction_round is not None:
                if loop is None:
                    raise RuntimeError("active interaction round cannot accept steer without loop_controller")
                loop.enqueue_steer(str(inputs["query"]))
                self._notify_work()
                return

            keep_open = self._should_keep_interaction_open_locked()
            if keep_open:
                self._event_manager.push_user(
                    RoundWorkItem.user(
                        request_id=request.request_id,
                        inputs=inputs,
                        is_follow_up=True,
                        reset_loop=False,
                    )
                )
                self._notify_work()
                return

            # Fresh user turn.  Output lease ownership stays with the host via
            # ``attach_output``; send_input never steals or creates a stream.
            self._event_manager.push_user(
                RoundWorkItem.user(request_id=request.request_id, inputs=inputs)
            )
            self._notify_work()

    async def _attach_output_locked(self) -> Optional[InteractionOutputStream]:
        lease = await self._interaction_output.attach()
        return InteractionOutputStream(self, lease) if lease is not None else None

    async def next_output(self, lease: OutputLease) -> Optional[Any]:
        return await self._interaction_output.next_item(lease)

    async def detach_output(self, token: str, *, abort_active_round: bool) -> None:
        async with self._interaction_control_lock:
            detached = await self._interaction_output.detach(token)
            if not detached:
                return
            self._event_manager.discard_all_work()
            if abort_active_round:
                await self._cancel_active_round(reason="output_detached")

    async def _detach_current_output_locked(self, *, abort_active_round: bool) -> None:
        lease = self._interaction_output.current_lease()
        if lease is None:
            return
        if await self._interaction_output.detach(lease.token):
            self._event_manager.discard_all_work()
            if abort_active_round:
                await self._cancel_active_round(reason="output_replaced")

    def _emit_interaction_event(self, event: InteractionEvent) -> None:
        # The callback is synchronous because rails can invoke it from several
        # lifecycle hooks.  Capture the lease now so a delayed task cannot put
        # an old event into a replacement request's output stream.
        # Fire-and-forget is required (cannot await here); track the task so
        # stop() can cancel outstanding emits instead of leaking them.
        token = self._interaction_output.current_token()
        if token is not None:
            task = asyncio.create_task(
                self._interaction_output.emit(event.to_output_schema(), expected_token=token)
            )
            self._interaction_emit_tasks.add(task)
            task.add_done_callback(self._interaction_emit_tasks.discard)

    async def cancel_round(
        self,
        *,
        reason: str,
    ) -> bool:
        """Cancel the current active round without stopping the interaction loop.

        Works for both user and goal rounds. Does not modify GoalRecord: an
        ACTIVE goal stays ACTIVE until ``goal_manager.pause/clear`` (or
        evaluation reaches a terminal status). Hosts that need to stop the
        in-flight attempt while keeping the persistent goal should call this
        (or close the output stream with ``abort_active_round=True``).
        """
        active = self._active_interaction_round
        if active is None:
            return False
        await self._cancel_active_round(reason=reason)
        return True

    async def _cancel_active_round(
        self,
        *,
        reason: str,
        expected_run_kind: Optional[str] = None,
        expected_goal_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
    ) -> None:
        active = self._active_interaction_round
        if active is None:
            return
        if expected_run_kind is not None and active.run_kind != expected_run_kind:
            return
        if expected_goal_id is not None and active.goal_id != expected_goal_id:
            return
        if expected_revision is not None and active.revision != expected_revision:
            return

        logger.info("[DeepAgent] cancelling %s round: %s", active.run_kind, reason)
        controller = self.loop_controller
        scheduler = getattr(controller, "task_scheduler", None) if controller else None
        if active.task_id and scheduler is not None:
            with suppress(Exception):
                await scheduler.cancel_task(active.task_id)
        with suppress(Exception):
            await self.abort(self._interaction_session)
        task = self._interaction_round_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _notify_work(self) -> None:
        self._interaction_wakeup.set()
        self._ensure_supervisor_running()

    def _ensure_supervisor_running(self) -> None:
        if self._interaction_supervisor_task is None or self._interaction_supervisor_task.done():
            self._interaction_supervisor_task = asyncio.create_task(self._supervisor_loop())

    async def _supervisor_loop(self) -> None:
        try:
            while self._interaction_started and self._interaction_phase is not InteractionPhase.TERMINATED:
                if not self._interaction_output.has_consumer():
                    self._interaction_phase = InteractionPhase.IDLE
                    self._interaction_wakeup.clear()
                    await self._interaction_wakeup.wait()
                    continue

                work = self._event_manager.next_work()
                if work is None:
                    await self._promote_loop_follow_ups()
                    work = self._event_manager.next_work()
                if work is None:
                    await self._close_idle_output_if_finished()
                    self._interaction_phase = InteractionPhase.IDLE
                    self._interaction_wakeup.clear()
                    if self._event_manager.has_pending_work():
                        continue
                    await self._interaction_wakeup.wait()
                    continue

                self._interaction_round_task = asyncio.create_task(self._execute_round(work))
                await asyncio.wait({self._interaction_round_task})
                if self._interaction_round_forwarded is not None:
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._interaction_round_forwarded.wait(), timeout=2.0)
                self._interaction_round_task = None
                self._interaction_round_forwarded = None
        except asyncio.CancelledError:
            logger.debug("[DeepAgent] supervisor cancelled")
        except Exception:
            logger.exception("[DeepAgent] supervisor failed")
            self._interaction_phase = InteractionPhase.IDLE

    async def _promote_loop_follow_ups(self) -> None:
        controller = self.loop_controller
        if controller is None or not controller.has_follow_up():
            return
        for query in controller.drain_follow_up():
            self._event_manager.push_user(
                RoundWorkItem.user(
                    request_id=None,
                    inputs={"query": query},
                    is_follow_up=True,
                    reset_loop=False,
                )
            )

    async def _close_idle_output_if_finished(self) -> None:
        """End an ordinary response stream after its final queued round."""
        async with self._interaction_control_lock:
            if self._should_keep_interaction_open_locked():
                return
            await self._interaction_output.finish_current()

    def _should_keep_interaction_open_locked(self) -> bool:
        """Return whether the current output stream still owns live work.

        The caller must hold ``_interaction_control_lock``.  A goal stream spans
        multiple attempts, so an ACTIVE goal keeps the interaction open.

        Side effect (intentional): when the GoalRecord is ACTIVE, this method
        also calls ``ensure_active_goal_work_locked()`` so the next attempt is
        queued before the host can observe an idle completion.  Callers must
        treat a True result as both "keep open" and "goal work ensured".
        """
        if self._event_manager.has_pending_work() or self._active_interaction_round is not None:
            return True
        record = self._load_goal_record_locked()
        if record is not None and record.status is GoalStatus.ACTIVE:
            if self.goal_manager is not None:
                self.goal_manager.ensure_active_goal_work_locked()
            return True
        return False

    async def _forward_session_stream(self) -> None:
        session = self._interaction_session
        if session is None:
            return
        try:
            async for chunk in session.stream_iterator():
                if chunk is _ROUND_BOUNDARY:
                    if self._interaction_round_forwarded is not None:
                        self._interaction_round_forwarded.set()
                    continue
                await self._interaction_output.emit(chunk)
        except Exception:
            logger.exception("[DeepAgent] output forwarder failed")
            self._emit_interaction_event(
                InteractionEvent.execution_error(
                    code="forwarder_crashed", message="output forwarder crashed"
                )
            )

    async def _emit_round_boundary(self, session: "Session") -> bool:
        """Emit an internal round-boundary marker into the session stream.

        Uses ``session._inner`` because the public Session API has no way to
        inject a non-payload control frame into ``stream_iterator()`` ordering.
        ``_ROUND_BOUNDARY`` is filtered by ``_forward_session_stream`` and must
        not go through ``write_stream`` / ``write_custom_stream``.
        """
        try:
            manager = getattr(session, "_inner").stream_writer_manager()
            await manager.stream_emitter().emit(_ROUND_BOUNDARY)
            return True
        except Exception:
            logger.debug("[DeepAgent] unable to emit ordered round boundary", exc_info=True)
            return False

    async def _execute_round(self, work: RoundWorkItem) -> None:
        session = self._interaction_session
        task_id = uuid.uuid4().hex
        forwarded = asyncio.Event()
        self._interaction_round_forwarded = forwarded
        self._active_interaction_round = ActiveInteractionRound(work=work, task_id=task_id)
        self._event_manager.mark_started(work)
        self._interaction_phase = InteractionPhase.RUNNING
        try:
            if session is None or not self._interaction_output.has_consumer():
                return
            if work.kind == "goal":
                if self.goal_manager is None:
                    return
                started = await self.goal_manager.begin_attempt(
                    goal_id=str(work.context["goal_id"]),
                    revision=int(work.context["revision"]),
                )
                if started is None:
                    return

            outcome: RoundOutcome = await self.run_one_round(
                work, task_id, session
            )
            if outcome.next_work is not None:
                self._event_manager.push_user(outcome.next_work)
                self._notify_work()
            if outcome.error_code is not None:
                self._emit_interaction_event(
                    InteractionEvent.execution_error(
                        code=outcome.error_code,
                        message=outcome.error_message or "",
                    )
                )
        except asyncio.CancelledError:
            logger.info("[DeepAgent] round cancelled")
        except Exception:
            logger.exception("[DeepAgent] round execution failed")
            self._emit_interaction_event(
                InteractionEvent.execution_error(
                    code="round_execution_error",
                    message="round execution failed",
                )
            )
        finally:
            self._event_manager.mark_finished(work)
            if session is not None:
                emitted = await self._emit_round_boundary(session)
                if not emitted:
                    forwarded.set()
            self._active_interaction_round = None
            if self._interaction_phase is InteractionPhase.RUNNING:
                self._interaction_phase = InteractionPhase.IDLE

    def _load_goal_record_locked(self) -> Optional[GoalRecord]:
        if self.goal_manager is None:
            return None
        return self.goal_manager.get_store().load()



__all__ = [
    "DeepAgent",
    "DeepAgentConfig",
]
