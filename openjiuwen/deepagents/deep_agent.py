# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent implementation."""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional, cast

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
    InvokeInputs,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents.schema.config import DeepAgentConfig
from openjiuwen.deepagents.schema.loop_event import (
    DeepLoopEvent,
)
from openjiuwen.deepagents.schema.state import (
    clear_state,
    save_state,
)

_DEFAULT_SYSTEM_PROMPT = (
    "你是一个通用 AI 助手。请根据用户的需求，合理使用可用工具完成任务。\n"
    "在执行过程中保持目标聚焦，遇到问题时尝试不同策略。"
)

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
        self._pending_external_events: List[DeepLoopEvent] = []
        self._initialized = False
        super().__init__(card)

    def configure(self, config: DeepAgentConfig) -> "DeepAgent":
        """Apply configuration and rebuild the internal ReActAgent."""
        self._deep_config = config
        self._react_agent = self._create_react_agent()
        self._initialized = False
        return self

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
    def deep_config(self) -> DeepAgentConfig:
        """deep_config carried by this agent."""
        return self._deep_config

    def drain_pending_external_events(self) -> List[DeepLoopEvent]:
        """Return and clear external loop events queued via follow_up/steer/abort."""
        queued = list(self._pending_external_events)
        self._pending_external_events.clear()
        return queued

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
        react_config.max_iterations = cfg.max_iterations

        prompt = cfg.system_prompt or _DEFAULT_SYSTEM_PROMPT
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

        # Share ability manager so tools registered on DeepAgent are visible inside.
        agent.ability_manager = self.ability_manager

        # Inject pre-built Model to bypass lazy init.
        if cfg.model is not None:
            agent.set_llm(cfg.model)

        return agent

    async def _ensure_initialized(self) -> None:
        """Perform lazy async initialization."""
        if self._initialized:
            return

        for rail_inst in self._pending_rails:
            await self._register_rail_selective(rail_inst)
        self._pending_rails.clear()
        self._initialized = True

    def _normalize_inputs(self, inputs: Any) -> InvokeInputs:
        """Parse user inputs into typed InvokeInputs."""
        if isinstance(inputs, dict):
            query = inputs.get("query", "")
            conversation_id = inputs.get("conversation_id")
        elif isinstance(inputs, str):
            query = inputs
            conversation_id = None
        else:
            raise build_error(
                StatusCode.DEEPAGENT_INPUT_PARAM_ERROR,
                error_msg="Input must be dict with 'query' or str.",
            )

        return InvokeInputs(query=query, conversation_id=conversation_id)

    @staticmethod
    def _to_effective_inputs(invoke_inputs: InvokeInputs) -> Dict[str, Any]:
        """Convert typed invoke inputs to ReAct input dict."""
        effective_inputs: Dict[str, Any] = {"query": invoke_inputs.query}
        if invoke_inputs.conversation_id is not None:
            effective_inputs["conversation_id"] = invoke_inputs.conversation_id
        return effective_inputs

    def add_rail(self, rail: AgentRail) -> "DeepAgent":
        """Synchronously queue a rail for registration."""
        self._pending_rails.append(rail)
        return self

    async def register_rail(self, rail: AgentRail) -> "DeepAgent":
        """Register a rail with selective routing."""
        rail.init(self)
        await self._register_rail_selective(rail)
        return self

    async def unregister_rail(self, rail: AgentRail) -> "DeepAgent":
        """Unregister a rail from pending/outer/inner."""
        # Remove queued instances first so lazy-init does not re-register stale rails.
        self._pending_rails = [queued for queued in self._pending_rails if queued is not rail]

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
        """Route rail callbacks to the correct agent."""
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

    async def _run_task_loop_invoke(
        self, ctx: AgentCallbackContext, session: Optional[Session]
    ) -> Dict[str, Any]:
        """Invoke inner ReActAgent in outer loop skeleton."""
        _ = ctx
        _ = session
        raise build_error(
            StatusCode.DEEPAGENT_TASK_LOOP_NOT_IMPLEMENTED,
            error_msg=(
                "Task-loop invoke path is reserved for upcoming "
                "EventHandler/TaskExecutor-based integration."
            ),
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

    async def _run_task_loop_stream(
        self,
        ctx: AgentCallbackContext,
        session: Optional[Session],
        stream_modes: Optional[List[StreamMode]],
    ) -> AsyncIterator[Any]:
        """Stream in outer loop skeleton."""
        _ = ctx
        _ = session
        _ = stream_modes
        if False:
            yield None
        raise build_error(
            StatusCode.DEEPAGENT_TASK_LOOP_NOT_IMPLEMENTED,
            error_msg=(
                "Task-loop stream path is reserved for upcoming "
                "EventHandler/TaskExecutor-based integration."
            ),
        )

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

        result: Dict[str, Any]
        async with ctx.lifecycle(
            AgentCallbackEvent.BEFORE_INVOKE,
            AgentCallbackEvent.AFTER_INVOKE,
        ):
            if self._deep_config is not None and self._deep_config.enable_task_loop:
                result = await self._run_task_loop_invoke(ctx, session)
            else:
                result = await self._run_single_round_invoke(ctx, session)
            invoke_inputs.result = result

        save_state(ctx)
        clear_state(ctx)
        return result

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

        async with ctx.lifecycle(
            AgentCallbackEvent.BEFORE_INVOKE,
            AgentCallbackEvent.AFTER_INVOKE,
        ):
            if self._deep_config is not None and self._deep_config.enable_task_loop:
                async for chunk in self._run_task_loop_stream(ctx, session, stream_modes):
                    yield chunk
            else:
                async for chunk in self._run_single_round_stream(ctx, session, stream_modes):
                    yield chunk

        save_state(ctx)
        clear_state(ctx)

    async def follow_up(
        self,
        msg: str,
        task_id: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> None:
        """Placeholder for future external follow-up event enqueue."""
        _ = msg
        _ = task_id
        _ = session

    async def steer(self, msg: str, session: Optional[Session] = None) -> None:
        """Placeholder for future external steer event enqueue."""
        _ = msg
        _ = session

    async def abort(self, session: Optional[Session] = None) -> None:
        """Placeholder for future external abort request."""
        _ = session


__all__ = [
    "DeepAgent",
    "DeepAgentConfig",
]
