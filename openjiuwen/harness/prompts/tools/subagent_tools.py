# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for runtime subagent tools."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

SUBAGENT_SPAWN_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "仅在用户或 AGENTS.md/技能明确要求委派时，为独立、可并行的任务启动常驻子代理。"
        "提供 display_name、role 和完整上下文。返回 subagent_id 和 task_id，同一 turn 用 subagent_wait 取结果。"
        "已有存活实例可继续派任务。浏览器操作走同步 task_tool。"
        "\n\n可用代理类型（仅供选择，不能单凭此列表派生）：\n{available_agents}"
    ),
    "en": (
        "Start an authorized persistent independent task only when the user or AGENTS.md/skill explicitly "
        "requests delegation. Give display_name, role and full context. Returns subagent_id/task_id. "
        "Wait in the same turn and follow up on live instances. Browser work uses synchronous task_tool. "
        "Available agent types (selection, not authorization):\n{available_agents}"
    ),
}

SUBAGENT_WAIT_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "等待指定子代理进入终态，返回状态、结果、完整回答文件路径和超时信息。用 output_files[id] 读取完整回答，避免要求子代理重复发送。遵循子代理生命周期规则。"
    ),
    "en": (
        "Wait for all IDs: statuses/results/output_files/timed_out. Read output_files[id] for the "
        "full answer; do not ask the child to repeat. Follow lifecycle rules."
    ),
}

SUBAGENT_LIST_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "列出存活实例 live_subagents（别名 subagents）、已关闭实例 closed_subagents "
        "以及各自数量。以本次返回为准，向存活实例发任务，恢复已关闭实例。"
    ),
    "en": (
        "List live_subagents (alias subagents), closed_subagents and live_count/closed_count. Report "
        "actual records; send_input to live, resume closed."
    ),
}

SUBAGENT_SEND_INPUT_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "向存活子代理追加任务并返回 task_id。等待超时且需要调整方向时用 interrupt=true 中断当前轮次。实例已关闭或被淘汰时先恢复。"
    ),
    "en": (
        "Send follow-up work to a live subagent and return task_id. After wait times out, use "
        "interrupt=true if the active turn needs redirection. Resume closed or evicted instances "
        "first."
    ),
}

SUBAGENT_CLOSE_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "关闭无需再用的子代理，返回关闭前状态并释放名额。已完成实例仍占用名额，有后续任务或刚按用户要求恢复的实例应保留。运行中先等待或中断，历史可恢复。"
    ),
    "en": (
        "Close unneeded subagents, release their slots and return the previous status. Completed "
        "instances still occupy slots. Keep instances with pending follow-ups or just restored at"
        " the user’s request. Wait or interrupt before closing a running instance. History stays "
        "resumable."
    ),
}

SUBAGENT_RESUME_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "从会话记录恢复已关闭或被淘汰的实例并占用名额。恢复后待命，restored=false 表示已在内存中。有新任务时用 subagent_send_input 后等待结果。"
    ),
    "en": (
        "Restore a closed or evicted checkpoint and reclaim a slot. The instance stays idle. "
        "restored=false means it is already live. For new work, use subagent_send_input then "
        "wait."
    ),
}

SUBAGENT_RESUME_IDLE_MESSAGE: dict[str, str] = {
    "cn": (
        "实例已就绪，本次 resume 未投递新任务；turn_outcome 描述恢复前的上一轮结果，"
        "不代表本次 resume 执行了新请求。"
        "仅要求恢复时保持待命；有后续消息时直接 subagent_send_input，再 subagent_wait。"
        "不要仅因 idle/completed 关闭实例或再次 resume。"
    ),
    "en": (
        "Instance is ready; this resume call did not enqueue new work. "
        "turn_outcome describes the previous turn before restoration; "
        "it does not mean this resume call executed a new request. "
        "If only restoration was requested, leave it idle. "
        "For follow-up work, use subagent_send_input, then subagent_wait. "
        "Do not close or resume it again just because it is idle/completed."
    ),
}


def get_subagent_spawn_input_params(language: str = "cn") -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "description": "已注册的子代理类型名。" if language == "cn" else "Registered subagent type name.",
            },
            "task_description": {
                "type": "string",
                "description": "子代理首轮的完整任务说明。" if language == "cn" else "First-turn task prompt for the subagent.",
            },
            "display_name": {
                "type": "string",
                "description": "界面显示的简短名称。" if language == "cn" else "Short UI name.",
            },
            "role": {
                "type": "string",
                "description": "用一句话说明子任务中的角色。" if language == "cn" else "One-line role for this subtask.",
            },
            "browser_capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "仅 browser_agent 需要，纯 Core 任务用空列表。"
                    if language == "cn" else "Required for browser_agent only; empty list for core-only tasks."
                ),
            },
        },
        "required": ["subagent_type", "task_description", "display_name", "role"],
    }


def get_subagent_wait_input_params(language: str = "cn") -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "subagent_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要等待的子代理 ID。" if language == "cn" else "Subagent ids to wait for.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": (
                    "单位毫秒，默认 1800000，短任务可用 120000，上限 3600000。子代理每轮仍最多 30 分钟。"
                    if language == "cn" else
                    "Milliseconds: default 1800000; quick 120000; max 3600000. Child turn cap remains"
                    " 30 minutes."
                ),
            },
        },
        "required": ["subagent_ids"],
    }


def get_subagent_list_input_params(language: str = "cn") -> Dict[str, Any]:
    _ = language
    return {
        "type": "object",
        "properties": {},
        "required": [],
    }


def get_subagent_send_input_input_params(language: str = "cn") -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "subagent_id": {
                "type": "string",
                "description": "目标子代理 ID。" if language == "cn" else "Target subagent id.",
            },
            "query": {
                "type": "string",
                "description": "发给子代理的后续任务。" if language == "cn" else "Follow-up task prompt for the subagent.",
            },
            "interrupt": {
                "type": "boolean",
                "description": (
                    "为 true 时先中断当前轮次，再提交新任务。"
                    if language == "cn" else "Cancel the active turn before enqueueing the new input."
                ),
            },
        },
        "required": ["subagent_id", "query"],
    }


def get_subagent_close_input_params(language: str = "cn") -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "subagent_id": {
                "type": "string",
                "description": (
                    "要关闭的子代理 ID（由 subagent_spawn 返回）。"
                    if language == "cn" else "Subagent id to close (from subagent_spawn)."
                ),
            },
        },
        "required": ["subagent_id"],
    }


def get_subagent_resume_input_params(language: str = "cn") -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "subagent_id": {
                "type": "string",
                "description": (
                    "要从会话记录中恢复的子代理 ID。"
                    if language == "cn" else "Subagent id to restore from checkpointer."
                ),
            },
        },
        "required": ["subagent_id"],
    }


class SubagentSpawnMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "subagent_spawn"

    def get_description(self, language: str = "cn") -> str:
        return SUBAGENT_SPAWN_DESCRIPTION.get(language, SUBAGENT_SPAWN_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_subagent_spawn_input_params(language)


class SubagentWaitMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "subagent_wait"

    def get_description(self, language: str = "cn") -> str:
        return SUBAGENT_WAIT_DESCRIPTION.get(language, SUBAGENT_WAIT_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_subagent_wait_input_params(language)


class SubagentListMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "subagent_list"

    def get_description(self, language: str = "cn") -> str:
        return SUBAGENT_LIST_DESCRIPTION.get(language, SUBAGENT_LIST_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_subagent_list_input_params(language)


class SubagentSendInputMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "subagent_send_input"

    def get_description(self, language: str = "cn") -> str:
        return SUBAGENT_SEND_INPUT_DESCRIPTION.get(
            language,
            SUBAGENT_SEND_INPUT_DESCRIPTION["cn"],
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_subagent_send_input_input_params(language)


class SubagentCloseMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "subagent_close"

    def get_description(self, language: str = "cn") -> str:
        return SUBAGENT_CLOSE_DESCRIPTION.get(language, SUBAGENT_CLOSE_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_subagent_close_input_params(language)


class SubagentResumeMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "subagent_resume"

    def get_description(self, language: str = "cn") -> str:
        return SUBAGENT_RESUME_DESCRIPTION.get(language, SUBAGENT_RESUME_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_subagent_resume_input_params(language)


__all__ = [
    "SubagentCloseMetadataProvider",
    "SubagentListMetadataProvider",
    "SubagentResumeMetadataProvider",
    "SubagentSendInputMetadataProvider",
    "SubagentSpawnMetadataProvider",
    "SubagentWaitMetadataProvider",
    "get_subagent_close_input_params",
    "get_subagent_list_input_params",
    "get_subagent_resume_input_params",
    "get_subagent_send_input_input_params",
    "get_subagent_spawn_input_params",
    "get_subagent_wait_input_params",
]
