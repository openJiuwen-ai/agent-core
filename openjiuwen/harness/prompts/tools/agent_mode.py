# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Plan mode tool metadata providers.

Provides bilingual descriptions and parameter schemas for
``enter_plan_mode`` and ``exit_plan_mode`` tools.
"""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider


# ---------------------------------------------------------------------------
# switch_mode
# ---------------------------------------------------------------------------

SWITCH_MODE_DESCRIPTION_CN = (
    "在 normal 与 plan 模式间切换当前会话模式。"
    "\n\n"
    "何时使用："
    "\n"
    "- 用户明确要求只做规划、不做实现时，（e.g.切到 plan 模式）。"
    "\n"
    "- 你判断当前模式不适合该任务。"
    "\n"
    "- 任务的复杂度或需求发生显著变化。"
    "\n\n"
    "模式说明："
    "\n"
    "- plan：规划优先。除 plan 文件外仅允许只读操作。"
    "\n"
    "- normal：完整的开发权限，可修改文件并执行命令。"
    "\n\n"
    "注意："
    "\n"
    "- 在意图不明确时先用 ask_user 澄清，再切换模式。"
)

SWITCH_MODE_DESCRIPTION_EN = (
    "Switch the current session between normal and plan modes."
    "\n\n"
    "When to use:"
    "\n"
    "- Switch to plan when the user explicitly wants planning only and no implementation."
    "\n"
    "- You determine the current mode is inappropriate for the task"
    "\n"
    "- A task's complexity or requirements have changed significantly."
    "\n\n"
    "Mode characteristics:"
    "\n"
    "- plan: Structured planning before execution, read-only with plan file writing only."
    "\n"
    "- normal: Full development actions are allowed (editing files, running commands, etc.)."
    "\n\n"
    "Note:"
    "\n"
    "- If intent is ambiguous, call ask_user first before switching mode."
)

SWITCH_MODE_INPUT_PARAMS_CN: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["normal", "plan"],
            "description": "目标模式：normal 或 plan",
        }
    },
    "required": ["mode"],
}

SWITCH_MODE_INPUT_PARAMS_EN: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["normal", "plan"],
            "description": "Target mode: normal or plan",
        }
    },
    "required": ["mode"],
}


class SwitchModeMetadataProvider(ToolMetadataProvider):
    """Metadata provider for the ``switch_mode`` tool."""

    def get_name(self) -> str:
        return "switch_mode"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return SWITCH_MODE_DESCRIPTION_EN
        return SWITCH_MODE_DESCRIPTION_CN

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        if language == "en":
            return SWITCH_MODE_INPUT_PARAMS_EN
        return SWITCH_MODE_INPUT_PARAMS_CN


# ---------------------------------------------------------------------------
# enter_plan_mode
# ---------------------------------------------------------------------------

ENTER_PLAN_MODE_DESCRIPTION_CN = (
    "初始化 plan 文件并返回文件路径。在 plan 模式下，这必须是你的第一个操作。"
    "该工具会创建一个新的 plan 文件（幂等：若已存在则直接返回路径）。"
)

ENTER_PLAN_MODE_DESCRIPTION_EN = (
    "Initialize the plan file and return its path. In plan mode this must be "
    "your very first action. Creates a new plan file (idempotent: returns the "
    "existing path if already created)."
)

ENTER_PLAN_MODE_INPUT_PARAMS_CN: Dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}

ENTER_PLAN_MODE_INPUT_PARAMS_EN: Dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


class EnterPlanModeMetadataProvider(ToolMetadataProvider):
    """Metadata provider for the ``enter_plan_mode`` tool."""

    def get_name(self) -> str:
        return "enter_plan_mode"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return ENTER_PLAN_MODE_DESCRIPTION_EN
        return ENTER_PLAN_MODE_DESCRIPTION_CN

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        if language == "en":
            return ENTER_PLAN_MODE_INPUT_PARAMS_EN
        return ENTER_PLAN_MODE_INPUT_PARAMS_CN

    def validate(self) -> None:
        """No-parameter tools skip the standard bilingual schema check."""


# ---------------------------------------------------------------------------
# exit_plan_mode
# ---------------------------------------------------------------------------

EXIT_PLAN_MODE_DESCRIPTION_CN = (
    "读取 plan 文件全文并直接返回给用户，结束规划阶段。"
    "当你对最终 plan 文件满意时，必须调用此工具结束规划阶段。"
    "tool_result 中包含完整计划内容。"
)

EXIT_PLAN_MODE_DESCRIPTION_EN = (
    "Read the full plan file and return the plan directly, ending the planning phase. "
    "Call this when you are satisfied with the final plan. "
    "The tool result contains the complete plan content."
)

EXIT_PLAN_MODE_INPUT_PARAMS_CN: Dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}

EXIT_PLAN_MODE_INPUT_PARAMS_EN: Dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


class ExitPlanModeMetadataProvider(ToolMetadataProvider):
    """Metadata provider for the ``exit_plan_mode`` tool."""

    def get_name(self) -> str:
        return "exit_plan_mode"

    def get_description(self, language: str = "cn") -> str:
        if language == "en":
            return EXIT_PLAN_MODE_DESCRIPTION_EN
        return EXIT_PLAN_MODE_DESCRIPTION_CN

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        if language == "en":
            return EXIT_PLAN_MODE_INPUT_PARAMS_EN
        return EXIT_PLAN_MODE_INPUT_PARAMS_CN

    def validate(self) -> None:
        """No-parameter tools skip the standard bilingual schema check."""


__all__ = [
    "SwitchModeMetadataProvider",
    "EnterPlanModeMetadataProvider",
    "ExitPlanModeMetadataProvider",
]
