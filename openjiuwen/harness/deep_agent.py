# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent implementation."""
from __future__ import annotations

import sys
from typing import (
    Any, AsyncIterator, Dict, List, Optional,
    TYPE_CHECKING, Tuple, cast,
)
import asyncio
from pathlib import Path

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
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
    RunKind,
    RunContext,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails import DeepAgentRail
from openjiuwen.harness.rails.task_completion_rail import (
    TaskCompletionRail,
)
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.schema.event import (
    TaskInteractionEvent,
    FollowUpEvent,
)
from openjiuwen.harness.task_loop.task_loop_event_handler import (
    TaskLoopEventHandler,
)
from openjiuwen.harness.task_loop.task_loop_event_executor import (
    DEEP_TASK_TYPE,
    build_deep_executor,
)
from openjiuwen.harness.task_loop.loop_coordinator import (
    LoopCoordinator,
)
from openjiuwen.harness.schema.state import (
    DeepAgentState,
    _SESSION_RUNTIME_ATTR,
    _SESSION_STATE_KEY,
)
from openjiuwen.harness.task_loop.loop_queues import (
    LoopQueues,
)
from openjiuwen.harness.task_loop.task_loop_controller import (
    TaskLoopController,
)
from openjiuwen.harness.rails.progressive_tool_rail import ProgressiveToolRail
from openjiuwen.harness.tools.session_tools import SessionToolkit

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.event_queue import (
        EventQueue,
    )

from openjiuwen.harness.prompts import (
    resolve_language,
    resolve_mode,
    PromptSection,
    SystemPromptBuilder,
)
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.identity import build_identity_section
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
        self._auto_invoke_scheduled: bool = False
        self._bound_session_id: Optional[str] = None
        self._session_toolkit: SessionToolkit | None = None
        super().__init__(card)

    def set_session_toolkit(self, toolkit: SessionToolkit | None) -> None:
        """Attach or clear the session toolkit (wired by SessionRail)."""
        self._session_toolkit = toolkit

    def configure(self, config: DeepAgentConfig) -> "DeepAgent":
        """Apply configuration and rebuild the internal ReActAgent."""

        if self._deep_config is None:
            self._initial_configure(config)
        else:
            self._hot_reconfigure(config)

        self._initialized = False
        return self

    def _initial_configure(self, config: DeepAgentConfig) -> None:
        """First-time setup: persist config, create the inner ReActAgent, and queue rails."""
        self._deep_config = config
        if config.card is not None:
            self.card = config.card

        self._react_agent = self._create_react_agent()
        self._queue_pending_rails(config)

    def _hot_reconfigure(self, config: DeepAgentConfig) -> None:
        """Hot-reconfigure an already-running agent without restarting it."""
        self._deep_config = config
        if config.card is not None:
            self.card = config.card

        self._hot_reload_rails(config)

        if config.model is not None:
            self._hot_reload_model(config)

        if config.tools is not None and self._react_agent is not None:
            self._hot_reload_tools(config)

        if config.system_prompt is not None and self._react_agent is not None:
            self._hot_reload_system_prompt(config)

        self._queue_pending_rails(config)

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
        logger.info("[DeepAgent] Model configuration hot reloaded")

    def _hot_reload_tools(self, config: DeepAgentConfig) -> None:
        """Sync tool cards in the shared AbilityManager during hot-reconfigure.

        Tools are matched by id: a card whose id already exists in the
        AbilityManager is left untouched.  Cards with a new id replace any
        existing entry with the same name, or are added fresh.  Tools present
        in the AbilityManager but absent from config.tools are removed.
        MCP server registrations and other ability types are not affected.
        """
        new_by_name = {card.name: card for card in (config.tools or [])}

        # Remove tools that are no longer in the new config.
        existing_tool_names = {
            a.name for a in self.ability_manager.list() if isinstance(a, ToolCard)
        }
        stale = [name for name in existing_tool_names if name not in new_by_name]
        if stale:
            self.ability_manager.remove(stale)

        # Add or replace tools whose id has changed (or are new).
        for name, card in new_by_name.items():
            existing = self.ability_manager.get(name)
            existing_tool = existing if isinstance(existing, ToolCard) else None
            if existing_tool is not None and existing_tool.id == card.id:
                continue  # Same id — no update needed.
            if existing_tool is not None:
                self.ability_manager.remove(name)
            self.ability_manager.add(card)

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
            ))
        else:
            prompt_builder.add_section(build_identity_section(language))
        prompt = prompt_builder.build()
        new_react_config = self._react_agent.config.model_copy()
        new_react_config.prompt_template = [{"role": "system", "content": prompt}]
        self._react_agent.configure(new_react_config)
        self.system_prompt_builder = prompt_builder
        self._react_agent.prompt_builder = prompt_builder
        self._react_agent.system_prompt_builder = prompt_builder
        logger.info("[DeepAgent] System prompt hot reloaded")

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

        language = resolve_language(cfg.language)
        mode = resolve_mode(cfg.prompt_mode)
        prompt_builder = SystemPromptBuilder(language=language, mode=mode)
        if cfg.system_prompt:
            # Wrap the provided prompt as the identity section so all
            # rails can consistently operate on prompt_builder.
            prompt_builder.add_section(PromptSection(
                name=SectionName.IDENTITY,
                content={"cn": cfg.system_prompt, "en": cfg.system_prompt},
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

    async def _ensure_initialized(self) -> None:
        """Perform lazy async initialization."""
        if self._initialized:
            return

        await self._register_pending_mcps()

        if self._needs_workspace_init():
            await self.init_workspace()

        # Unregister stale rails left over from a previous configure() cycle.
        for stale_rail in self._stale_rails:
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
    def react_config(self) -> ReActAgentConfig:
        """React agent config. For testing only."""
        return self._react_agent.config

    def create_subagent(self, subagent_type: str, subsession_id: str) -> "DeepAgent":
        """Create a subagent instance (shared by TaskTool and SessionSpawnExecutor).

        Args:
            subagent_type: Type of subagent to create (e.g., "general-purpose").
            subsession_id: The session id for the subagent.

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
                f"{self._deep_config.workspace}/{subsession_id}"
                if self._deep_config.workspace
                else f"./{subsession_id}"
            )
            workspace = Workspace(
                root_path=workspace_path,
                language=self._deep_config.language
            )
        else:
            workspace = Workspace(
                root_path=self._deep_config.workspace.root_path + f"/{subsession_id}",
                language=self._deep_config.language
            )

        create_kwargs = {
            "model": spec.model or self._deep_config.model,
            "card": spec.agent_card,
            "system_prompt": spec.system_prompt,
            "tools": spec.tools or [],
            "mcps": spec.mcps or [],
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
            "add_general_purpose_agent": False
        }

        if spec.factory_name:
            normalized_factory = (spec.factory_name or "").strip().lower()
            if normalized_factory in {"browser_agent", "browser_runtime"}:
                from openjiuwen.harness.subagents.browser_agent import (
                    create_browser_agent,
                )

                return create_browser_agent(
                    **create_kwargs,
                    **dict(spec.factory_kwargs or {}),
                )

            raise build_error(
                StatusCode.DEEPAGENT_CREATE_SUBAGENT_NOT_FOUND,
                error_msg=f"Unsupported subagent factory: {spec.factory_name}",
            )

        from openjiuwen.harness.factory import create_deep_agent

        return create_deep_agent(**create_kwargs)

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
                    run_context = RunContext(**context_data)
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

        return InvokeInputs(
            query=query,
            conversation_id=conversation_id,
            run_kind=run_kind,
            run_context=run_context
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

    async def register_rail(self, rail: AgentRail) -> "DeepAgent":
        """Register a rail with selective routing."""
        if isinstance(rail, TaskCompletionRail):
            self._task_completion_rail = rail
        if isinstance(rail, DeepAgentRail):
            rail.set_sys_operation(self.deep_config.sys_operation)
            rail.set_workspace(self.deep_config.workspace)
        rail.init(self)
        await self._register_rail_selective(rail)
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

                query_preview = str(current_query)[:120]
                self._log_loop(
                    f"round={outer_round} started",
                    f", query={query_preview}",
                )

                await controller.submit_round(
                    session, current_query,
                    is_follow_up=is_follow_up,
                    run_kind=modified.run_kind,
                    run_context=modified.run_context,
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
        async for result in self._run_task_loop(
            ctx, session
        ):
            last_result = result
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
                logger.error(f"Task loop stream error: {e}")
            finally:
                # Only close the stream emitter to send END_FRAME,
                # so stream_iterator() can terminate.
                # Full session cleanup (post_run) is left to the caller.
                await session.close_stream()

        task = asyncio.create_task(_stream_process())

        async for chunk in session.stream_iterator():
            yield chunk

        await task

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

    async def invoke(  # type: ignore[override]
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

    async def stream(  # type: ignore[override]
        self,
        inputs: Any,
        session: Optional[Session] = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        """Stream execute DeepAgent in single-round or task-loop mode."""
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
                        yield chunk
                else:
                    async for chunk in self._run_single_round_stream(
                        ctx, session, stream_modes
                    ):
                        yield chunk

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

    async def abort(
        self,
        session: Optional[Session] = None,
    ) -> None:
        """Request immediate abort of the task loop.

        Sets the abort flag on the coordinator and
        calls on_abort() on the event handler so the
        outer loop can exit promptly.

        Args:
            session: Current session (unused).
        """
        _ = session
        coordinator = self._loop_coordinator
        controller = self._loop_controller
        if coordinator is None or controller is None:
            return
        coordinator.request_abort()
        handler = cast(
            TaskLoopEventHandler,
            controller.event_handler,
        )
        await handler.on_abort()


__all__ = [
    "DeepAgent",
    "DeepAgentConfig",
]
