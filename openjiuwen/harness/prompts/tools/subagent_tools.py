# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for runtime subagent tools."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

SUBAGENT_SPAWN_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "为具体、有界、可独立执行的子任务创建常驻子代理并投递首轮任务，立即返回 subagent_id 与 task_id。"
        "仅当用户或 AGENTS.md/skill 明确要求委派/并行子代理时使用；"
        "深度调研或细读代码库本身不构成 spawn 授权。"
        "spawn 不含最终 output，必须在同一 turn 内调用 subagent_wait。"
        "优先委派可与本地工作并行的侧车子任务，勿把关键路径阻塞项 spawn 后空等。"
        "\n\n可用子代理类型（仅用于选择 subagent_type，不单独构成 spawn 授权）：\n{available_agents}"
    ),
    "en": (
        "Create a persistent subagent for a concrete, bounded, self-contained subtask; "
        "returns subagent_id and task_id immediately. "
        "Use only when the user or AGENTS.md/skill explicitly asks for delegation or parallel sub-agents; "
        "depth, research, or codebase analysis alone is not permission. "
        "spawn does not return output—you must subagent_wait in the same turn. "
        "Prefer parallel sidecar tasks; do not spawn blocking critical-path work and idle-wait."
        "\n\nAvailable agent types (choose subagent_type only; does not authorize spawning alone):\n"
        "{available_agents}"
    ),
}

SUBAGENT_WAIT_DESCRIPTION: Dict[str, str] = {
    "cn": (
        "阻塞等待一个或多个 subagent_id 达到终态，返回 statuses、results 与 timed_out。"
        "研究或编码等长任务请传分钟级 timeout_ms。"
    ),
    "en": (
        "Block until all listed subagent_ids reach a final status; returns statuses, "
        "results, and timed_out. Prefer minute-scale timeout_ms for research or coding tasks."
    ),
}

SUBAGENT_LIST_DESCRIPTION: Dict[str, str] = {
    "cn": "列出当前父会话下存活的子代理及容量占用情况。",
    "en": "List live subagents and current capacity usage for the parent session.",
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
            "browser_capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required for browser_agent only; empty list for core-only tasks.",
            },
        },
        "required": ["subagent_type", "task_description"],
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
                "description": "Wait deadline in milliseconds.",
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


__all__ = [
    "SubagentListMetadataProvider",
    "SubagentSpawnMetadataProvider",
    "SubagentWaitMetadataProvider",
    "get_subagent_list_input_params",
    "get_subagent_spawn_input_params",
    "get_subagent_wait_input_params",
]
