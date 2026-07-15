# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Run the caller's SubTaskExecutor after every report_plan call.

The model never calls a movement tool directly: it reports a full plan, and
whichever sub-task comes back marked ``in_progress`` is handed to
``SubTaskExecutor.execute()`` automatically. The result is fed back to the
model as a steering message before its next turn.

``step_executor`` is expected to already be resolved on ``settings`` (see
``rails_factory._resolve_step_executor``) by the time this rail is constructed,
so this rail only ever touches the inner ReActAgent's own callback context --
it never needs the outer DeepAgent's ``before_invoke``/``after_invoke``.
"""

from __future__ import annotations

import json

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail, ToolCallInputs
from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings, SubTaskExecutor
from openjiuwen.harness.tools.robotic_arm.plan_tools import REPORT_PLAN_TOOL_CARD, summarize_plan


class StepExecutorRail(AgentRail):
    """Run ``step_executor`` on the in_progress sub-task after every report_plan call."""

    priority: int = 90

    def __init__(self, settings: RoboticArmRuntimeSettings) -> None:
        super().__init__()
        self._step_executor: SubTaskExecutor | None = settings.step_executor
        self._on_step_result = settings.on_step_result
        if self._step_executor is None:
            raise ValueError(
                "RoboticArmRuntimeSettings.step_executor or step_executor_model is required: either "
                "supply a SubTaskExecutor handle directly (capture(), execute(frame, sub_task) -> str), "
                "or set step_executor_model to a name registered via SubTaskExecutorRegistry.register(...)."
            )
        if settings.health_check:
            self._verify_health()

    def _verify_health(self) -> None:
        try:
            self._step_executor.capture()
        except Exception:
            logger.exception("[StepExecutorRail] step_executor health check failed (continuing)")

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        if ctx.inputs.tool_name != REPORT_PLAN_TOOL_CARD.name:
            return

        # report_plan's own ctx.extra write never runs -- Tool.invoke() is called
        # without ctx (see ability_manager._execute_single_tool_call), so
        # kwargs.get("ctx") inside plan_tools.py is always None. ctx.inputs is
        # populated by the framework directly, independent of the tool's own
        # kwargs handling, so read the validated call args from there instead.
        tool_result = ctx.inputs.tool_result
        if not isinstance(tool_result, str) or not tool_result.startswith("Success:"):
            return

        # ctx.inputs.tool_args mirrors ToolCall.arguments, which stays the raw
        # JSON string the model emitted unless AbilityManager had to repair
        # malformed JSON (see _parse_tool_arguments_with_repair) -- it is only
        # ever a dict already in that repaired case.
        tool_args = ctx.inputs.tool_args
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except (json.JSONDecodeError, TypeError):
                return
        if not isinstance(tool_args, dict):
            return
        sub_tasks = tool_args.get("sub_tasks")
        if not sub_tasks:
            return

        ctx.extra["last_plan_sub_tasks"] = sub_tasks
        ctx.extra["last_plan_summary"] = summarize_plan(sub_tasks)

        current = next((t for t in sub_tasks if t.get("status") == "in_progress"), None)
        if current is None:
            logger.info("[StepExecutorRail] no in_progress sub-task in the reported plan; skipping execution")
            return

        frame = ctx.extra.get("vlm_raw_frame")

        try:
            result_text = self._step_executor.execute(frame, current)
        except Exception as e:
            result_text = f"Error: StepExecutionFailed: {type(e).__name__}: {e}"
            logger.exception("[StepExecutorRail] step_executor.execute failed")

        ctx.push_steering(f"[Execution Result] {result_text}")

        await self._notify_step_result(sub_tasks, current, result_text)

    async def _notify_step_result(self, sub_tasks: list, current: dict, result_text: str) -> None:
        if self._on_step_result is None:
            return
        debug = getattr(self._step_executor, "last_debug", None)
        try:
            await self._on_step_result(
                {"sub_tasks": sub_tasks, "current": current, "result_text": result_text, "debug": debug}
            )
        except Exception:
            logger.exception("[StepExecutorRail] on_step_result callback failed")


__all__ = ["StepExecutorRail"]
