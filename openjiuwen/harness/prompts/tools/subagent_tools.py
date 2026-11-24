# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for runtime subagent tools."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

SUBAGENT_SPAWN_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "为目标具体、范围清晰、可独立完成的子任务创建常驻子代理并下发首轮任务，立即返回 subagent_id 与 task_id。"
        "仅当用户或 AGENTS.md/skill 明确要求委派/并行子代理时使用；"
        "用户要求深度调研或细读代码库，本身不构成 spawn 条件。"
        "spawn 不返回最终 output，必须在同一 turn 内调用 subagent_wait。"
        "调用时必须提供 display_name（界面展示名）和 role（本轮子任务角色）。"
        "优先委派可与本地工作并行的子任务；不要把必须先等结果才能继续的工作 spawn 出去后空等。"
        "\n\n可用子代理类型（仅用于选择 subagent_type，不能单凭此列表 spawn）：\n{available_agents}"
    ),
    "en": (
        "Start an authorized persistent independent task; returns subagent_id/task_id, not output. "
        "Give display_name/role and full objective/context; wait in the same turn. Follow up on the "
        "live instance rather than respawning. Browser work uses synchronous task_tool. Available "
        "agent types (selection, not authorization):\n{available_agents}"
    ),
}

SUBAGENT_WAIT_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "阻塞等待一个或多个 subagent_id 达到终态，返回 statuses、results、output_files 与 timed_out。"
        "output_files[subagent_id] 是该子代理本轮回答的完整文件路径；"
        "正文较长或后续还要引用细节时，用 read_file 读该文件，勿让子代理重复输出。"
        "timeout_ms 默认 1800000（30 分钟）；简单查询可传 120000（2 分钟），"
        "超长调研/编码可到上限 3600000。"
    ),
    "en": (
        "Wait for all IDs: statuses/results/output_files/timed_out. Read output_files[id] for the "
        "full answer; do not ask the child to repeat. Follow lifecycle rules."
    ),
}

SUBAGENT_LIST_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "列出当前父会话下的子代理与容量占用。"
        "subagents / live_subagents 为存活实例（可 send_input）；"
        "closed_subagents 为已关闭、可 subagent_resume 的实例；"
        "summary 给出 live_count / closed_count。"
        "向用户汇报时须以本次返回为准，勿凭对话历史臆造列表。"
    ),
    "en": (
        "List live_subagents (alias subagents), closed_subagents and live_count/closed_count. Report "
        "actual records; send_input to live, resume closed."
    ),
}

SUBAGENT_SEND_INPUT_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "向已存在的 subagent_id 投递后续消息，立即返回新 task_id。"
        "wait 超时且方向错误时可传 interrupt=true 中止当前轮并改向。"
        "实例已 close 或被淘汰时须先 subagent_resume。"
    ),
    "en": (
        "Continue a live instance; returns task_id. interrupt=true redirects running work. Resume "
        "closed/evicted instances first."
    ),
}

SUBAGENT_CLOSE_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "在子代理不再需要时关闭实例并释放常驻名额；返回关闭前的状态。"
        "已完成任务的子代理仍会占用名额，不要长期保留不再需要的实例。"
        "用户刚要求恢复或还有待发送的后续消息时，应保留实例；不要仅因 idle/completed 就关闭。"
        "会话上下文保留在 checkpointer，之后可用 subagent_resume 恢复。"
        "RUNNING 实例会被拒绝，请先 subagent_wait 或 subagent_send_input(interrupt=true)。"
    ),
    "en": (
        "Release unneeded slots; returns previous status. RUNNING requires wait/interrupt first. Keep"
        " pending follow-ups/restored idle instances. History remains resumable."
    ),
}

SUBAGENT_RESUME_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "从 checkpointer 恢复 status=closed（manual/evicted）的 subagent，重新占用名额，不自动投递任务。"
        "status=idle 的存活实例无需 resume，直接 subagent_send_input。"
        "返回 restored=false 表示实例本就在内存中，不是恢复失败，无需 close 后重试。"
        "仅要求恢复时保持实例待命；有后续消息时直接 subagent_send_input，再 subagent_wait。"
        "恢复后的 idle/completed 不代表新请求已执行，不要因此立即 close 或再次 resume。"
    ),
    "en": (
        "Restore a closed checkpoint without starting work. restored=false means already live. "
        "Restore-only leaves idle; new work requires send_input then wait."
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
    _ = language
    return {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "description": "Registered subagent type name.",
            },
            "task_description": {
                "type": "string",
                "description": "First-turn task prompt for the subagent.",
            },
            "display_name": {
                "type": "string",
                "description": "Short UI name.",
            },
            "role": {
                "type": "string",
                "description": "One-line role for this subtask.",
            },
            "browser_capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required for browser_agent only; empty list for core-only tasks.",
            },
        },
        "required": ["subagent_type", "task_description", "display_name", "role"],
    }


def get_subagent_wait_input_params(language: str = "cn") -> Dict[str, Any]:
    _ = language
    return {
        "type": "object",
        "properties": {
            "subagent_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Subagent ids to wait for.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": (
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
    _ = language
    return {
        "type": "object",
        "properties": {
            "subagent_id": {
                "type": "string",
                "description": "Target subagent id.",
            },
            "query": {
                "type": "string",
                "description": "Follow-up task prompt for the subagent.",
            },
            "interrupt": {
                "type": "boolean",
                "description": "Cancel the active turn before enqueueing the new input.",
            },
        },
        "required": ["subagent_id", "query"],
    }


def get_subagent_close_input_params(language: str = "cn") -> Dict[str, Any]:
    _ = language
    return {
        "type": "object",
        "properties": {
            "subagent_id": {
                "type": "string",
                "description": "Subagent id to close (from subagent_spawn).",
            },
        },
        "required": ["subagent_id"],
    }


def get_subagent_resume_input_params(language: str = "cn") -> Dict[str, Any]:
    _ = language
    return {
        "type": "object",
        "properties": {
            "subagent_id": {
                "type": "string",
                "description": "Subagent id to restore from checkpointer.",
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
