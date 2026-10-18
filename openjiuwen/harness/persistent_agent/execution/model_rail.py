# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Budget admission, usage reports and model events around each native model call."""

from typing import TYPE_CHECKING
from uuid import uuid4

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.harness.rails.base import DeepAgentRail

from ..schema.events import EventType
from ..schema.port_results import BudgetAdmission, BudgetReport
from ..schema.protocol import ModelUsage
from . import capacity

if TYPE_CHECKING:
    from ..schema.build_context import ConstructionBinding
    from ..schema.spec import PersistentAgentSpec

# Result marker a force-finished model call hands back to the execution entry.
PAUSE_MARKER = "persistent_pause"
_CALL_KEY = "_persistent_model_call_id"
_PROGRESS_LIMIT = 500
# Result of a call refused after a stop, a cancel or a critical failure.
SKIPPED_TOOL_TEXT = "本次执行正在结束，该工具未调用。"
_DETAIL_LIMIT = 4096


def pause_result(reason: str, detail: str | None = None) -> dict:
    """Structured result for a model call skipped by Persistent policy."""
    return {"result_type": PAUSE_MARKER, "reason": reason, "detail": detail, "output": ""}


class PersistentModelCallRail(DeepAgentRail):
    """Inert outside the execution entries, which attach the emitter before running."""

    # Check stopping after higher-priority before-model rails; capacity and admission use the final request hook.
    priority = 10

    def __init__(self, binding: "ConstructionBinding", *, spec: "PersistentAgentSpec | None" = None):
        super().__init__()
        self._binding = binding
        self._spec = spec
        self._limits = capacity.capacity_limits(spec) if spec is not None else None

    def _emitter(self):
        return self._binding.control.emitter

    def _stop_requested(self, ctx) -> bool:
        """End the native loop once a critical failure or control is registered; the entry decides the outcome."""
        control = self._binding.control
        if control.fatal is None and control.abort_reason is None:
            return False
        ctx.request_force_finish(pause_result("error", "execution is stopping"))
        return True

    async def before_invoke(self, ctx) -> None:
        """Rails are registered by now and the native context is not yet created: refuse offloading.

        A rail's init may add context processors after construction, so this is checked here too.
        """
        if self._emitter() is None:
            return
        from .session_state import offloading_processors

        reference = self._binding.agent_ref
        agent = reference() if reference is not None else None
        offloading = offloading_processors(agent) if agent is not None else []
        if offloading:
            error = build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg=f"Persistent windows do not support offloading; remove {', '.join(offloading)}",
            )
            self._binding.control.mark_fatal(error)
            raise error
        if agent is not None:
            # This instance's native loop checks every final request: capacity, then budget (F_20 D-09).
            agent.react_agent.set_final_request_check(self.check_final_request)

    async def before_model_call(self, ctx) -> None:
        """Skip the call after a control or critical failure; budget is asked for on the final request."""
        if self._emitter() is not None:
            self._stop_requested(ctx)

    async def after_react_iteration(self, ctx) -> None:
        """Mark a complete tool iteration for checkpointing before the next model request."""
        if self._emitter() is None:
            return
        manager = getattr(self._binding.control, "window_switch_manager", None)
        if manager is not None:
            await manager.after_react_iteration(ctx)

    async def check_final_request(self, ctx) -> dict | None:
        """Final request, capacity check, budget admission, in this order; a result ends the round.

        Called by the native loop once the final messages and tools are built, before the model. A
        request that does not fit or cannot be counted is never admitted, and a denied one never runs.
        """
        if self._emitter() is None:
            return None
        manager = getattr(self._binding.control, "window_switch_manager", None)
        if manager is not None:
            result = await manager.before_model_request(ctx)
            if result is not None:
                return result
            # A provisional candidate has been staged. Let core rebuild the final request,
            # but do not count or admit the stale request or charge a second model call.
            if ctx.has_context_rebuild_request:
                return None
        if self._limits is not None:
            needed = await self._count(ctx)
            if needed is None:
                return pause_result("error", "context capacity unknown: the final request cannot be counted reliably")
            if needed > self._limits.available_input:
                available = self._limits.available_input
                return pause_result("context_unfit", f"the final request needs {needed} input tokens; {available} fit")
        return await self._admit(ctx)

    async def _count(self, ctx) -> int | None:
        if self._spec is None:
            return None
        try:
            # Core already selected this counter from the published tokenizer settings. Read it on
            # every final check so a context rebind cannot leave us using a stale counter.
            counter = ctx.context.token_counter()
        except Exception:  # noqa: BLE001 - without a counter the capacity is unknown; cancellation propagates
            return None
        if counter is None:
            return None
        request = self._spec.model.model_request_config if self._spec.model is not None else None
        model = str(getattr(request, "model_name", "") or "")
        return capacity.count_request(counter, list(ctx.inputs.messages or []), ctx.inputs.tools, model)

    async def _admit(self, ctx) -> dict | None:
        execution, budget = self._binding.execution, self._binding.ports.budget
        model_call_id = "mc_" + uuid4().hex
        try:
            admission = BudgetAdmission.model_validate(await budget.admit(execution, model_call_id))
            admission.assert_call(execution, model_call_id)
        except Exception as exc:
            return pause_result("error", f"budget admission failed: {exc}"[:_DETAIL_LIMIT])
        if admission.status == "denied":
            return pause_result("budget_denied", admission.reason)
        ctx.extra[_CALL_KEY] = model_call_id
        return None

    async def before_tool_call(self, ctx) -> None:
        """Skip every tool not yet started, read-only ones included, after a control or critical failure."""
        if self._emitter() is not None and self._stop_requested(ctx):
            # Keep the internal force-finish payload for entry control; the window gets a plain note.
            call = getattr(ctx.inputs, "tool_call", None)
            ctx.inputs.tool_msg = ToolMessage(content=SKIPPED_TOOL_TEXT, tool_call_id=getattr(call, "id", "") or "")

    async def after_model_call(self, ctx) -> None:
        """Report usage and record the call; text beside tool calls is an intermediate conclusion."""
        model_call_id = ctx.extra.pop(_CALL_KEY, None)
        if model_call_id is None or self._emitter() is None:
            return
        response = getattr(ctx.inputs, "response", None)
        metadata = getattr(response, "usage_metadata", None)
        if metadata is None:
            usage = ModelUsage(status="unknown", reason="model returned no usage")
        else:
            usage = ModelUsage(status="known", input_tokens=metadata.input_tokens, output_tokens=metadata.output_tokens)
        await self._finish_call(model_call_id, usage)
        content = getattr(response, "content", None)
        if getattr(response, "tool_calls", None) and isinstance(content, str) and content.strip():
            await self._emitter().emit(EventType.PROGRESS_UPDATED, {"text": content.strip()[:_PROGRESS_LIMIT]})

    async def on_model_exception(self, ctx) -> None:
        """A failed attempt still consumed an admission; report it with unknown usage."""
        model_call_id = ctx.extra.pop(_CALL_KEY, None)
        if model_call_id is None or self._emitter() is None:
            return
        await self._finish_call(model_call_id, ModelUsage(status="unknown", reason="model call failed"))

    async def _finish_call(self, model_call_id: str, usage: ModelUsage) -> None:
        execution = self._binding.execution
        try:
            report = BudgetReport.model_validate(
                await self._binding.ports.budget.report(execution, model_call_id, usage)
            )
            report.assert_call(execution, model_call_id)
        except Exception as exc:
            # The model already ran; the host reconciles usage, the Execution continues.
            logger.warning(
                "[PersistentAgent] budget report failed: execution=%s call=%s error=%s",
                execution.execution_id,
                model_call_id,
                exc,
            )
        await self._emitter().emit(
            EventType.MODEL_COMPLETED,
            {"model_call_id": model_call_id, "config_version": execution.config_version, "usage": usage},
        )
