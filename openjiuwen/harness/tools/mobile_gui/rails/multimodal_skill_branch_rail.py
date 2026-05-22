# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from typing import Any, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ToolCallInputs,
)
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.skill_branch.format import (
    format_branch_failure_tool_message,
    format_planner_tool_message,
)
from openjiuwen.harness.tools.mobile_gui.skill_branch.manifest import (
    build_skill_image_manifest,
)
from openjiuwen.harness.tools.mobile_gui.skill_branch.previous_steps import (
    format_previous_steps_for_branch,
)
from openjiuwen.harness.tools.mobile_gui.skill_branch.runner import run_skill_branch
from openjiuwen.harness.tools.mobile_gui.state import mobile_gui_shared


def resolve_branch_model(agent: Any) -> Optional[Model]:
    """Obtain the ReAct/DeepAgent LLM used for branch consultation."""
    react = getattr(agent, "react_agent", None)
    target = react if react is not None else agent
    get_llm = getattr(target, "_get_llm", None)
    if callable(get_llm):
        try:
            return get_llm()
        except Exception as exc:
            logger.warning("[MultimodalSkillBranchRail] could not resolve model: %s", exc)
    return None


def _parse_tool_args(tool_args: Any) -> dict[str, Any]:
    if isinstance(tool_args, dict):
        return tool_args
    if isinstance(tool_args, str):
        try:
            parsed = json.loads(tool_args)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def _extract_skill_payload(
    tool_result: Any,
    tool_args: Any,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    skill_name = str(_parse_tool_args(tool_args).get("skill_name", "") or "").strip() or None
    data = getattr(tool_result, "data", None)
    if not isinstance(data, dict):
        return skill_name, None, None
    skill_text = data.get("skill_content")
    skill_dir = data.get("skill_directory")
    if skill_text is not None and not isinstance(skill_text, str):
        skill_text = str(skill_text)
    if skill_dir is not None:
        skill_dir = str(skill_dir)
    return skill_name, skill_text, skill_dir


class MultimodalSkillBranchRail(AgentRail):
    """Run MMSkills-style branch after skill_tool and replace ToolMessage with planner memo."""

    priority: int = 35

    def __init__(self, settings: MobileGuiRuntimeSettings) -> None:
        super().__init__()
        self._settings = settings

    def _consult_counts(self, ctx: AgentCallbackContext) -> dict[str, int]:
        counts = ctx.extra.get("skill_branch_consult_counts")
        if not isinstance(counts, dict):
            counts = {}
            ctx.extra["skill_branch_consult_counts"] = counts
        return counts

    def _is_exhausted(self, ctx: AgentCallbackContext, skill_name: str) -> bool:
        limit = self._settings.skill_branch_max_consults_per_skill
        return self._consult_counts(ctx).get(skill_name, 0) >= limit

    def _record_consult(self, ctx: AgentCallbackContext, skill_name: str) -> None:
        counts = self._consult_counts(ctx)
        counts[skill_name] = counts.get(skill_name, 0) + 1

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if self._settings.skill_consult_mode != "branch":
            return
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        if ctx.inputs.tool_name != "skill_tool":
            return

        tool_result = ctx.inputs.tool_result
        success = getattr(tool_result, "success", True)
        if success is False:
            return

        skill_name, skill_text, skill_dir = _extract_skill_payload(
            tool_result,
            ctx.inputs.tool_args,
        )
        if not skill_name or not skill_text or not skill_dir:
            return

        manifest = build_skill_image_manifest(skill_text, skill_dir)
        if not manifest:
            return

        if self._is_exhausted(ctx, skill_name):
            msg = (
                f"Skill consult: {skill_name}\n"
                f"Consult limit reached ({self._settings.skill_branch_max_consults_per_skill} per skill). "
                "Act from the current screenshot and prior planner memos."
            )
            self._rewrite_tool_message(ctx, msg)
            return

        model = resolve_branch_model(ctx.agent)
        if model is None:
            logger.warning("[MultimodalSkillBranchRail] no model; skipping branch for %s", skill_name)
            return

        instruction = (
            ctx.extra.get("pinned_user_goal")
            or mobile_gui_shared.get("pinned_user_goal")
            or ""
        )
        screenshot_b64 = str(ctx.extra.get("vlm_grounding_base64") or "")

        context_messages: list[Any] = []
        if ctx.context is not None:
            context_messages = list(ctx.context.get_messages() or [])

        skip_tool_call_id: Optional[str] = None
        tool_call = ctx.inputs.tool_call
        if tool_call is not None:
            skip_tool_call_id = getattr(tool_call, "id", None) or None

        previous_steps = format_previous_steps_for_branch(
            context_messages,
            skip_tool_call_id=skip_tool_call_id,
            last_n_turns=self._settings.skill_branch_previous_steps_turns,
        )

        branch = await run_skill_branch(
            model,
            instruction=str(instruction),
            skill_name=skill_name,
            skill_text=skill_text,
            skill_directory=skill_dir,
            live_screenshot_b64=screenshot_b64,
            previous_steps=previous_steps,
            max_images=self._settings.skill_branch_max_images,
        )

        self._record_consult(ctx, skill_name)

        if branch.success and branch.planner:
            stage1_note = ""
            if branch.stage1_decision:
                stage1_note = str(branch.stage1_decision.get("why_not_text_only", "") or "")
            body = format_planner_tool_message(
                skill_name,
                branch.planner,
                stage1_note=stage1_note,
            )
            self._rewrite_tool_message(ctx, body)
            logger.info(
                "[MultimodalSkillBranchRail] branch ok skill=%s images=%s",
                skill_name,
                branch.selected_image_ids,
            )
            return

        error = branch.error or "Skill branch failed."
        body = format_branch_failure_tool_message(
            skill_name,
            error,
            skill_excerpt=skill_text,
        )
        self._rewrite_tool_message(ctx, body)
        logger.warning(
            "[MultimodalSkillBranchRail] branch failed skill=%s: %s",
            skill_name,
            error,
        )

    @staticmethod
    def _rewrite_tool_message(ctx: AgentCallbackContext, content: str) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_msg = ctx.inputs.tool_msg
        if isinstance(tool_msg, ToolMessage):
            tool_msg.content = content
            ctx.inputs.tool_msg = tool_msg
        elif tool_msg is None and ctx.inputs.tool_call is not None:
            ctx.inputs.tool_msg = ToolMessage(
                content=content,
                tool_call_id=getattr(ctx.inputs.tool_call, "id", None) or "",
                name="skill_tool",
            )

        tool_result = ctx.inputs.tool_result
        if isinstance(tool_result, ToolOutput):
            tool_result.data = {"planner_memo": content}


__all__ = ["MultimodalSkillBranchRail", "resolve_branch_model"]
