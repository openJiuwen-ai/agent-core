# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``report_plan`` tool: the model re-emits the FULL sub-task list every turn.

Unlike ``harness.tools.TodoTool`` (create/list/modify one item at a time, meant
for long-lived multi-day task tracking), this subagent re-plans from scratch
after every physical action because the world can change in ways the model
did not predict (grasp slipped, object moved, occlusion resolved). A single
tool that always takes the complete list keeps "the plan" and "what actually
happened" from silently diverging.

Sub-tasks carry no coordinates: the model only decides sequencing and what
each step is (``description``); where to point on the photo and whether to
grip is decided downstream by the executor's own VLM constraint generation
(see ``vendors/so101/rekep/constraint_generation.py``), which sees the same
photo plus each sub-task's description.
"""

from __future__ import annotations

from typing import Any, List

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

_VALID_STATUSES = ("pending", "in_progress", "done", "failed")

REPORT_PLAN_TOOL_CARD = ToolCard(
    id="tool.robotic_arm.report_plan",
    name="report_plan",
    description=(
        "Report/refresh the FULL sub-task plan for the current manipulation goal. Call this "
        "exactly once per turn, re-emitting ALL sub-tasks (not just the one that changed) -- "
        "re-plan from the latest photo every time rather than patching the previous list, since "
        "a grasp can slip or an object can move between steps. Exactly one sub-task should "
        "normally be `in_progress` at a time; it is executed automatically right after this call."
    ),
    input_params={
        "type": "object",
        "properties": {
            "sub_tasks": {
                "type": "array",
                "description": "The complete ordered list of sub-tasks for the current goal.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": (
                                "Stable short id for this sub-task, e.g. 's1'. Keep the same id across turns."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "Human-readable description, e.g. 'pick up the cup'.",
                        },
                        "status": {
                            "type": "string",
                            "enum": list(_VALID_STATUSES),
                            "description": "One of pending, in_progress, done, failed.",
                        },
                    },
                    "required": ["id", "description", "status"],
                },
            },
        },
        "required": ["sub_tasks"],
    },
)


def summarize_plan(sub_tasks: List[dict]) -> str:
    lines = [f"Plan ({len(sub_tasks)} sub-task(s)):"]
    for item in sub_tasks:
        lines.append(f"  [{item.get('id')}] {item.get('status')}: {item.get('description')}")
    return "\n".join(lines)


async def report_plan_action(sub_tasks: Any, ctx: AgentCallbackContext) -> str:
    if not isinstance(sub_tasks, list) or not sub_tasks:
        return "Error: InvalidPlan: `sub_tasks` must be a non-empty array."

    normalized: List[dict] = []
    for i, item in enumerate(sub_tasks):
        if not isinstance(item, dict):
            return f"Error: InvalidPlan: sub_tasks[{i}] must be an object."
        status = item.get("status")
        if status not in _VALID_STATUSES:
            return f"Error: InvalidPlan: sub_tasks[{i}].status must be one of {_VALID_STATUSES}, got {status!r}."
        if not item.get("id") or not item.get("description"):
            return f"Error: InvalidPlan: sub_tasks[{i}] requires non-empty `id` and `description`."
        normalized.append(item)

    summary = summarize_plan(normalized)

    # Best-effort only: the framework's Tool.invoke() never receives ctx (see
    # ReportPlanTool.invoke below), so this never actually fires at runtime.
    # StepExecutorRail reads ctx.inputs.tool_args/tool_result instead, which
    # the framework populates directly regardless of what the tool does.
    if ctx is not None:
        ctx.extra["last_plan_summary"] = summary
        ctx.extra["last_plan_sub_tasks"] = normalized

    return f"Success: plan updated.\n{summary}"


class ReportPlanTool(Tool):
    def __init__(self, card: ToolCard = REPORT_PLAN_TOOL_CARD) -> None:
        super().__init__(card=card)

    async def invoke(self, inputs: Any, **kwargs: Any) -> str:
        ctx = kwargs.get("ctx")
        sub_tasks = inputs.get("sub_tasks") if isinstance(inputs, dict) else getattr(inputs, "sub_tasks", None)
        return await report_plan_action(sub_tasks, ctx)

    async def stream(self, inputs: Any, **kwargs: Any):
        yield await self.invoke(inputs, **kwargs)


def build_plan_tools() -> List[Tool]:
    return [ReportPlanTool(REPORT_PLAN_TOOL_CARD)]


__all__ = ["REPORT_PLAN_TOOL_CARD", "ReportPlanTool", "build_plan_tools", "report_plan_action", "summarize_plan"]
