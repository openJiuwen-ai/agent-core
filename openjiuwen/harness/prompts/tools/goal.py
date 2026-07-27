# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for the submit_goal_report tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

# ---------------------------------------------------------------------------
# submit_goal_report description
# ---------------------------------------------------------------------------
SUBMIT_GOAL_REPORT_DESCRIPTION_CN = (
    "仅当本次用户消息中包含 <goal_task> 标签（即处于 Goal 执行轮）时才调用本工具；"
    "普通对话轮（无 <goal_task>）绝不要调用本工具。"
    "本工具用于提交当前 goal 尝试的结构化结果。"
    "这是本次 goal 尝试的最终工具动作；工具调用成功后不要再调用其他工具，"
    "已有进展但目标仍可继续推进时使用 continue；"
    "只有具备可审计完成证据时才使用 complete；"
    "只有缺少用户输入、权限、依赖、外部服务或环境状态"
    "且无法继续任何有意义推进时才使用 blocked。"
)

SUBMIT_GOAL_REPORT_DESCRIPTION_EN = (
    "Only call this tool when the current user message contains a <goal_task> tag "
    "(i.e. this is a Goal execution round); never call it on a normal turn "
    "without <goal_task>. "
    "Submit a structured result for the current goal attempt. "
    "This is the final tool action for the current goal attempt; after the tool "
    "call succeeds, do not call any other tools. "
    "Use continue when progress has been made but the objective can still be advanced; "
    "use complete only with auditable completion evidence; "
    "use blocked only when user input, permissions, dependencies, external services, "
    "or environment state prevent any meaningful progress."
)

SUBMIT_GOAL_REPORT_DESCRIPTION: Dict[str, str] = {
    "cn": SUBMIT_GOAL_REPORT_DESCRIPTION_CN,
    "en": SUBMIT_GOAL_REPORT_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Parameter-level bilingual descriptions
# ---------------------------------------------------------------------------
_PARAMS: Dict[str, Dict[str, str]] = {
    "status": {
        "cn": (
            "当前 goal 尝试结果：continue、complete 或 blocked。"
            "目标尚未完成但仍可推进时，不要报告 blocked。"
        ),
        "en": (
            "Current goal attempt result: continue, complete, or blocked. "
            "Do not report blocked when the objective is incomplete but can still be advanced."
        ),
    },
    "evidence": {
        "cn": (
            "支持该状态的可审计证据，例如测试输出、命令结果、文件路径、"
            "工具结果、完成证明，或确切阻塞原因。"
        ),
        "en": (
            "Auditable evidence supporting this status, e.g. test output, "
            "command results, file paths, tool results, proof of completion, "
            "or exact blocking reason."
        ),
    },
    "remaining_work": {
        "cn": "目标未完成时说明剩余工作；完成时可以为空。",
        "en": "Remaining work when the objective is not yet met; may be empty when complete.",
    },
    "next_instruction": {
        "cn": "status=continue 时给出下一次尝试的具体、可执行指令；其他状态可以为空。",
        "en": (
            "Specific, actionable instruction for the next attempt when status=continue; "
            "may be empty for other statuses."
        ),
    },
}


def get_submit_goal_report_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for submit_goal_report input_params."""

    def _d(key: str) -> str:
        return _PARAMS[key].get(language, _PARAMS[key]["cn"])

    return {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["continue", "complete", "blocked"],
                "description": _d("status"),
            },
            "evidence": {
                "type": "string",
                "description": _d("evidence"),
            },
            "remaining_work": {
                "type": "string",
                "description": _d("remaining_work"),
            },
            "next_instruction": {
                "type": "string",
                "description": _d("next_instruction"),
            },
        },
        "required": ["status", "evidence"],
    }


class SubmitGoalReportMetadataProvider(ToolMetadataProvider):
    """Metadata provider for the submit_goal_report tool."""

    def get_name(self) -> str:
        return "submit_goal_report"

    def get_description(self, language: str = "cn") -> str:
        return SUBMIT_GOAL_REPORT_DESCRIPTION.get(
            language, SUBMIT_GOAL_REPORT_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_submit_goal_report_input_params(language)


# ---------------------------------------------------------------------------
# get_current_goal description
# ---------------------------------------------------------------------------
GET_CURRENT_GOAL_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "查询当前会话的持续目标（goal）。"
        "当你不确定当前 goal 任务是什么、或用户提到「目标 / 继续目标 / 那个任务」时调用。"
        "返回 objective、status、attempt_count 与上一次评估（last_assessment）。"
    ),
    "en": (
        "Query the current session's persistent goal. "
        "Call this when you are unsure what the goal is, or when the user refers to "
        "\"the goal / continue the goal / that task\". "
        "Returns objective, status, attempt_count, and the last assessment."
    ),
}


class GetCurrentGoalMetadataProvider(ToolMetadataProvider):
    """Metadata provider for the get_current_goal tool."""

    def get_name(self) -> str:
        return "get_current_goal"

    def get_description(self, language: str = "cn") -> str:
        return GET_CURRENT_GOAL_DESCRIPTION.get(
            language, GET_CURRENT_GOAL_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }


__all__ = [
    "GetCurrentGoalMetadataProvider",
    "SubmitGoalReportMetadataProvider",
]
