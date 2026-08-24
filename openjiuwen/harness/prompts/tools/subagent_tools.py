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
        "Create a persistent subagent for a concrete, bounded, self-contained subtask; "
        "returns subagent_id and task_id immediately. "
        "Use only when the user or AGENTS.md/skill explicitly asks for delegation or parallel sub-agents; "
        "depth, research, or codebase analysis alone is not permission. "
        "spawn does not return output—you must subagent_wait in the same turn. "
        "Always provide display_name (UI label) and role (this subtask's role). "
        "Prefer parallel sidecar tasks; do not spawn blocking critical-path work and idle-wait."
        "\n\nAvailable agent types (choose subagent_type only; does not authorize spawning alone):\n"
        "{available_agents}"
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
        "Block until all listed subagent_ids reach a final status; returns statuses, "
        "results, output_files, and timed_out. "
        "output_files[subagent_id] is the absolute path to that subagent's full turn answer; "
        "for long output or later reference, read_file that path instead of asking the subagent "
        "to repeat it. Default timeout_ms is 1800000 (30 min); use 120000 (2 min) for "
        "simple queries, and up to 3600000 for very long research or coding."
    ),
}

SUBAGENT_LIST_DESCRIPTION: Dict[str, str] = {
    "cn": "列出当前父会话下存活的子代理及容量占用情况。",
    "en": "List live subagents and current capacity usage for the parent session.",
}

SUBAGENT_SEND_INPUT_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "向已存在的 subagent_id 投递后续消息，立即返回新 task_id。"
        "wait 超时且方向错误时可传 interrupt=true 中止当前轮并改向。"
        "实例已 close 或被淘汰时须先 subagent_resume。"
    ),
    "en": (
        "Send follow-up input to an existing subagent_id; returns a new task_id immediately. "
        "Use interrupt=true after a timed-out wait to cancel the current turn and redirect. "
        "If the instance was closed or evicted, call subagent_resume first."
    ),
}

SUBAGENT_CLOSE_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "在子代理不再需要时关闭实例并释放常驻名额；返回关闭前的状态。"
        "已完成任务的子代理仍会占用名额，不要长期保留不再需要的实例。"
        "会话上下文保留在 checkpointer，之后可用 subagent_resume 恢复。"
        "RUNNING 实例会被拒绝，请先 subagent_wait 或 subagent_send_input(interrupt=true)。"
    ),
    "en": (
        "Close a subagent when it is no longer needed and release its persistent slot; "
        "returns the target's previous status before shutdown was requested. "
        "Completed subagents remain open and count toward the capacity limit until closed—"
        "don't keep instances around longer than necessary. "
        "Conversation history stays in checkpointer and can be restored with subagent_resume. "
        "RUNNING instances are rejected—subagent_wait or subagent_send_input(interrupt=true) first."
    ),
}

SUBAGENT_RESUME_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "从 checkpointer 恢复 status=closed（manual/evicted）的 subagent，重新占用名额，不自动投递任务。"
        "status=idle 的存活实例无需 resume，直接 subagent_send_input。"
        "返回 restored=false 表示实例本就在内存中。恢复后须再 subagent_send_input 并 subagent_wait。"
    ),
    "en": (
        "Restore a status=closed (manual/evicted) subagent from checkpointer and reclaim a slot; "
        "does not enqueue work. Live status=idle instances need subagent_send_input, not resume. "
        "restored=false means the instance was already in memory. "
        "Follow with subagent_send_input and subagent_wait after a true restore."
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
                "description": "Short human-readable name shown in the UI (e.g. 'API Researcher').",
            },
            "role": {
                "type": "string",
                "description": "One-line role for this subtask (e.g. 'Review auth module changes').",
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
                    "Wait deadline in milliseconds. Default 1800000 (30 min); "
                    "use 120000 for quick tasks, up to 3600000 for very long research/coding."
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
