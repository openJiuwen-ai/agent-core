# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluation-only controlled delivery of one candidate Skill."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

CONTROLLED_SKILL_TREATMENT_CASE_KEY = "_controlled_skill_treatment"


def _canonical_tool_name(value: str) -> str:
    return value.strip().lower().replace("-", "_").removesuffix("_tool")


def _tool_definition_name(tool: Any) -> str:
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        return str(tool.get("name", "") or "")
    return str(getattr(tool, "name", "") or "")


def _tool_result_succeeded(result: Any) -> bool:
    if result is None:
        return False
    if isinstance(result, dict):
        if result.get("success") is False or result.get("error"):
            return False
        status = str(result.get("status", "") or "").strip().lower()
        return status not in {"error", "failed", "failure", "skipped"}
    if isinstance(result, str):
        normalized = result.strip().lower()
        return not normalized.startswith(("error", "exception", "failed"))
    if getattr(result, "success", None) is False or getattr(result, "error", None):
        return False
    return True


class ControlledSkillTreatmentRail(DeepAgentRail):
    """Deliver one exact candidate Skill before the solver can investigate.

    This rail is mounted only in a diagnostic sidecar after the natural primary
    evaluation misses Skill activation. Its result can distinguish discovery
    from content, but it never contributes to candidate acceptance or scoring.
    """

    priority = -100
    _SECTION_NAME = "controlled_candidate_skill_treatment"

    def __init__(self, skill_name: str) -> None:
        super().__init__()
        normalized = str(skill_name or "").strip()
        if not normalized:
            raise ValueError("controlled Skill treatment requires a skill name")
        self.skill_name = normalized
        self.system_prompt_builder = None
        self.model_call_count = 0
        self.tool_call_count = 0
        self.first_requested_tool_name = ""
        self.rewritten_skill_names: list[str] = []
        self.blocked_tool_names: list[str] = []
        self.delivered = False

    def init(self, agent: Any) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:  # noqa: ARG002
        self._remove_section()
        self.system_prompt_builder = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:  # noqa: ARG002
        self.model_call_count = 0
        self.tool_call_count = 0
        self.first_requested_tool_name = ""
        self.rewritten_skill_names = []
        self.blocked_tool_names = []
        self.delivered = False

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if self.delivered:
            return
        self.model_call_count += 1
        self._install_section()
        tools = getattr(ctx.inputs, "tools", None)
        if isinstance(tools, list):
            ctx.inputs.tools = [tool for tool in tools if _canonical_tool_name(_tool_definition_name(tool)) == "skill"]

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if self.delivered:
            return
        self.tool_call_count += 1
        requested_tool = str(getattr(ctx.inputs, "tool_name", "") or "")
        if not self.first_requested_tool_name:
            self.first_requested_tool_name = requested_tool
        if _canonical_tool_name(requested_tool) != "skill":
            self.blocked_tool_names.append(requested_tool)
            self._reject_tool(
                ctx,
                f"Controlled candidate treatment requires skill_tool('{self.skill_name}') "
                "before any investigation tool.",
            )
            return

        args = self._normalize_args(getattr(ctx.inputs, "tool_args", None))
        requested_skill = str(args.get("skill_name", "") or "").strip()
        if requested_skill != self.skill_name:
            if requested_skill:
                self.rewritten_skill_names.append(requested_skill)
            ctx.inputs.tool_args = {"skill_name": self.skill_name}

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if self.delivered:
            return
        tool_name = str(getattr(ctx.inputs, "tool_name", "") or "")
        if _canonical_tool_name(tool_name) != "skill":
            return
        args = self._normalize_args(getattr(ctx.inputs, "tool_args", None))
        if str(args.get("skill_name", "") or "").strip() != self.skill_name:
            return
        if not _tool_result_succeeded(getattr(ctx.inputs, "tool_result", None)):
            return
        self.delivered = True
        self._remove_section()

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:  # noqa: ARG002
        self._remove_section()

    def evidence(self) -> dict[str, Any]:
        """Return machine-readable treatment delivery evidence."""
        return {
            "mode": "controlled_first_action",
            "expected_skill_name": self.skill_name,
            "model_call_count_before_delivery": self.model_call_count,
            "tool_call_count_before_delivery": self.tool_call_count,
            "first_requested_tool_name": self.first_requested_tool_name,
            "rewritten_skill_names": list(self.rewritten_skill_names),
            "blocked_tool_names": list(self.blocked_tool_names),
            "delivered": self.delivered,
        }

    def _install_section(self) -> None:
        if self.system_prompt_builder is None:
            return
        content = (
            "# Controlled Candidate Skill Treatment\n\n"
            "This turn is a controlled capability experiment, not natural routing. "
            "Before reading files, running commands, forming a task hypothesis, or "
            "editing anything, your first and only action on this turn must be "
            f'`skill_tool` with `{{"skill_name": "{self.skill_name}"}}`. '
            "Do not call another tool in the same response. After the Skill result is "
            "returned, use its causal discriminator and acceptance probe to solve the "
            "original task."
        )
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self._SECTION_NAME,
                content={"cn": content, "en": content},
                priority=1,
            )
        )

    def _remove_section(self) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self._SECTION_NAME)

    @staticmethod
    def _normalize_args(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _reject_tool(ctx: AgentCallbackContext, message: str) -> None:
        tool_call = getattr(ctx.inputs, "tool_call", None)
        tool_call_id = str(getattr(tool_call, "id", "") or "")
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = {"error": message}
        ctx.inputs.tool_msg = ToolMessage(content=message, tool_call_id=tool_call_id)


__all__ = [
    "CONTROLLED_SKILL_TREATMENT_CASE_KEY",
    "ControlledSkillTreatmentRail",
]
