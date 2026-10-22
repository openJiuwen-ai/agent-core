# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent lifecycle integration for optional reference consultation."""

from __future__ import annotations

from itertools import islice
from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
    ToolCallInputs,
    UserMessageInputs,
)
from openjiuwen.harness.moa.config import MoAConfig
from openjiuwen.harness.moa.consultation import prepare_reference
from openjiuwen.harness.moa.proposal import ProposalModel
from openjiuwen.harness.moa.skill_mount import ConsultationSkillMount
from openjiuwen.harness.moa.state import MoAState, TurnKey, active_moa
from openjiuwen.harness.moa.verifier.jev_verifier.verifier import decide_reference
from openjiuwen.harness.moa.verifier.llm_verifier.scorer import score_progress
from openjiuwen.harness.moa.verifier.redaction import redact
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.moa import CONSULT_TOOL, ConsultReferenceModels

if TYPE_CHECKING:
    from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail


class MoARail(DeepAgentRail):
    """Keep routing state within one outer DeepAgent invocation."""

    priority = 10

    def __init__(self, config: MoAConfig) -> None:
        super().__init__()
        self._config = config
        self._tool: ConsultReferenceModels | None = None
        self._states: dict[str, MoAState] = {}
        self._skill_mount = (
            ConsultationSkillMount(config.consultation_skill_dir) if config.mode == "aggregator_on_demand" else None
        )

    @property
    def owned_skill_rail(self) -> SkillUseRail | None:
        """Companion rail to retire alongside this MoA rail, if any."""
        return self._skill_mount.owned_rail if self._skill_mount is not None else None

    def prepare_skill_loading(self, agent: Any) -> None:
        """Reuse the native skill loader once startup rails are complete."""
        if self._skill_mount is not None:
            self._skill_mount.prepare(agent)

    async def validate_skill_loading(self, session: Any = None, *, refresh: bool = False) -> None:
        if self._skill_mount is not None:
            await self._skill_mount.validate(session, refresh=refresh)

    async def _validate_skill_callback(self, ctx: AgentCallbackContext) -> None:
        try:
            await self.validate_skill_loading(ctx.session)
        except BaseError as exc:
            # Ordinary callback exceptions are logged and swallowed. Use the
            # framework's existing abort mechanism to stop an invalid request.
            raise AbortError(reason=str(exc), cause=exc) from exc

    def init(self, agent: Any) -> None:
        if self._config.mode == "aggregator_on_demand":
            existing = agent.ability_manager.get(CONSULT_TOOL)
            if existing is not None and (self._tool is None or existing is not self._tool.card):
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg=f"{CONSULT_TOOL} is already registered",
                )
            self._skill_mount.mount(agent)
            if existing is None:
                self._tool = ConsultReferenceModels(self._config, language=agent.system_prompt_builder.language)
                if not agent.ability_manager.add_ability(self._tool.card, self._tool).added:
                    raise build_error(
                        StatusCode.DEEPAGENT_RUNTIME_ERROR,
                        error_msg=f"could not register {CONSULT_TOOL}",
                    )

    def uninit(self, agent: Any) -> None:
        if self._tool is not None and agent.ability_manager.get(CONSULT_TOOL) is self._tool.card:
            agent.ability_manager.remove_ability(CONSULT_TOOL)
        self._tool = None
        if self._skill_mount is not None:
            self._skill_mount.unmount(agent)
        for state in self._states.values():
            state.closed = True
            state.expire_pending("rail_uninit")
        self._states.clear()

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        # Failed before-invoke callbacks do not run after_invoke cleanup.
        # Validate before installing invocation-local state.
        await self._validate_skill_callback(ctx)
        state = MoAState(problem=str(getattr(ctx.inputs, "query", "") or ""))
        ctx.extra["_moa_token"] = active_moa.set(state)
        session_id = self._session_id(ctx)
        if session_id is not None:
            self._states[session_id] = state

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        token = ctx.extra.pop("_moa_token", None)
        state = active_moa.get()
        if state is not None:
            state.closed = True
            state.expire_pending("outer_end")
            state.active_key = None
            state.baseline.clear()
            state.steps.clear()
            state.context = None
            state.scorer = None
        session_id = self._session_id(ctx)
        if session_id is not None:
            self._states.pop(session_id, None)
        if token is not None:
            active_moa.reset(token)

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        session_id = self._session_id(ctx)
        state = self._states.get(session_id) if session_id is not None else None
        if state is not None and active_moa.get() is not state:
            ctx.extra["_moa_task_token"] = active_moa.set(state)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        state = active_moa.get()
        if state is not None:
            state.expire_pending("task_iteration_end")
            state.active_key = None
        token = ctx.extra.pop("_moa_task_token", None)
        if token is not None:
            active_moa.reset(token)

    @staticmethod
    def _session_id(ctx: AgentCallbackContext) -> str | None:
        get_id = getattr(ctx.session, "get_session_id", None)
        return get_id() if callable(get_id) else None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._validate_skill_callback(ctx)
        state = active_moa.get()
        if state is None or state.closed or not isinstance(ctx.inputs, ModelCallInputs):
            return
        inputs = ctx.inputs
        inner_token = ctx.extra.setdefault("_moa_inner_token", object())
        key: TurnKey = (inner_token, inputs.react_iteration)
        state.active_key = key
        if state.pending is not None and state.pending.target != key:
            state.expire_pending("target_mismatch")
        state.context = ctx.context
        # The callback context and ModelCallInputs are reused across @rail
        # retries. A response from an earlier attempt is not this attempt's
        # success signal.
        inputs.response = None
        actual_model = ctx.agent.get_llm()
        if not isinstance(actual_model, ProposalModel):
            ctx.agent.set_llm(ProposalModel(actual_model))
        if not state.problem:
            state.problem = next(
                (str(message.content) for message in inputs.messages if getattr(message, "role", None) == "user"),
                "",
            )
        if self._config.mode == "verifier_routed" and state.reference_calls == 0:
            await prepare_reference(self._config, state, list(inputs.messages), key)
        elif self._config.mode == "jev_routed" and not state.jev_initial_checked:
            # A negative or failed decision is still the initial check. Mark
            # before awaiting so retries and new inner task contexts do not
            # turn it into an implicit per-model-call evaluation.
            state.jev_initial_checked = True
            await self._route_jev(ctx, state, list(inputs.messages), key)

    async def _route_jev(
        self, ctx: AgentCallbackContext, state: MoAState, messages: list[BaseMessage], target: TurnKey
    ) -> None:
        config = self._config.jev
        if config is not None and await decide_reference(config, state, messages, ctx.agent, ctx.session) is True:
            await prepare_reference(self._config, state, messages, target)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if self._config.mode != "aggregator_on_demand":
            return
        state = active_moa.get()
        if state is None or state.closed or not isinstance(ctx.inputs, ModelCallInputs):
            return
        if ctx.exception is not None or ctx.inputs.response is None:
            return
        # These are the framework's final-window messages, before the
        # private ProposalModel copy; references never see earlier proposals.
        state.baseline = list(ctx.inputs.messages)

    async def on_user_message(self, ctx: AgentCallbackContext) -> None:
        state = active_moa.get()
        if state is None or state.closed:
            return
        if isinstance(ctx.inputs, UserMessageInputs) and ctx.inputs.parts:
            state.expire_pending("new_input")

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if self._config.mode != "aggregator_on_demand":
            return
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs) or inputs.tool_name != CONSULT_TOOL:
            return
        state = active_moa.get()
        if state is None or state.closed:
            return
        get_messages = getattr(ctx.context, "get_messages", None)
        messages = get_messages() if callable(get_messages) else []
        for message in reversed(messages):
            if getattr(message, "role", None) == "assistant":
                calls = list(getattr(message, "tool_calls", None) or [])
                state.consultation_rejection = (
                    "consult_reference_models must be called alone."
                    if len(calls) != 1 or calls[0].name != CONSULT_TOOL
                    else None
                )
                break

    async def after_react_iteration(self, ctx: AgentCallbackContext) -> None:
        """Expire this turn's batch, then route proposals to the following turn."""
        state = active_moa.get()
        if state is None or state.closed:
            return
        state.completed_turns += 1
        current_key = state.active_key
        if state.pending is not None and state.pending.target == current_key:
            state.expire_pending("turn_completed")
        if self._config.mode == "jev_routed":
            config = self._config.jev
            if config is not None and current_key is not None and state.completed_turns % config.interval == 0:
                get_messages = getattr(ctx.context, "get_messages", None)
                messages = list(get_messages()) if callable(get_messages) else []
                await self._route_jev(ctx, state, messages, (current_key[0], current_key[1] + 1))
            return
        if self._config.mode != "verifier_routed":
            return
        get_messages = getattr(ctx.context, "get_messages", None)
        messages = list(get_messages()) if callable(get_messages) else []
        assistant_index = next(
            (i for i in range(len(messages) - 1, -1, -1) if getattr(messages[i], "role", None) == "assistant"),
            None,
        )
        if assistant_index is None:
            return
        assistant = messages[assistant_index]
        actions = ", ".join(call.name for call in (getattr(assistant, "tool_calls", None) or []))
        observations = "\n".join(
            str(message.content)[:1_000]
            for message in islice(messages, assistant_index + 1, None)
            if getattr(message, "role", None) == "tool"
        )
        step = redact(f"Action: {actions}; response: {assistant.content}\nObservations: {observations}"[:8_000])
        state.steps.append(step)
        routing = self._config.verifier
        if len(state.steps) % routing.interval or (
            routing.max_verifier_calls is not None
            and state.verifier_calls + routing.n_evaluations > routing.max_verifier_calls
        ):
            return
        state.verifier_calls += routing.n_evaluations
        score = await score_progress(self._config, state)
        if score is None:
            return
        stalled = state.previous_score is not None and score <= state.previous_score
        state.previous_score = score
        if stalled and current_key is not None:
            await prepare_reference(self._config, state, messages, (current_key[0], current_key[1] + 1))
