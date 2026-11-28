# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual metadata for the shared todo_write tool."""

from typing import Any

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider
from openjiuwen.harness.schema.task import TodoStatus

_DESCRIPTIONS = {
    "cn": {
        "tool": (
            "规划多步任务并展示进度。简单单步任务直接做。开始前按可验证的里程碑列待办，遵循用户指定的计划结构。"
            "开工标为 in_progress，首项可在首次提交时标记。同时最多一项进行中，里程碑内可并行。"
            "验证后及时标为 completed。切换到下一项时，在同一次整表提交中更新两项状态。"
        ),
        "todos": "完整有序列表，替换当前待办。带上要保留的条目及其原 ID。省略已有 ID 即删除，[] 仅用于清空。",
        "revision": (
            "首次建表省略。更新必填，取最新 todo_snapshot 或写入结果中的版本。"
            "遇到 conflict=true，将变更合入返回列表，用返回的版本重试。"
        ),
        "id": "会话内唯一的简短语义 ID。",
        "content": "可验证的阶段成果，用简短行动语句描述。",
        "status": "pending 待开始，in_progress 进行中，completed 已完成，cancelled 已取消并保留记录。",
        "description": "验收标准、约束或并行子项。省略时保留原值。",
        "selected_model_id": "当前会话提供的可用模型 ID。省略时保留原值。",
    },
    "en": {
        "tool": (
            "Plan multi-step work and show progress. Skip simple single-step tasks. Before starting, "
            "list verifiable milestones and follow the user's plan structure. "
            "Mark items in_progress when they start, including on the first write. "
            "Keep at most one in_progress item. Its subtasks may run in parallel. "
            "Mark items completed promptly after verification. When moving to the next item, "
            "update both statuses in one full-list write."
        ),
        "todos": (
            "Full ordered list replacing the current todos. Include all retained items with their original IDs. "
            "Omitting an existing ID deletes it. Use [] only to clear the list."
        ),
        "revision": (
            "Omit on first write. Updates require the revision from the latest todo_snapshot or write result. "
            "On conflict=true, merge changes into the returned list and retry with its revision."
        ),
        "id": "Short, meaningful, session-unique ID.",
        "content": "Brief action phrase describing a verifiable milestone outcome.",
        "status": "pending (not started), in_progress (active), completed (done), cancelled (dropped, record kept).",
        "description": "Acceptance criteria, constraints or parallel subtasks. Omitting preserves the existing value.",
        "selected_model_id": "An available model ID from this session. Omitting preserves the existing value.",
    },
}


class TodoWriteMetadataProvider(ToolMetadataProvider):
    def get_name(self) -> str:
        return "todo_write"

    def get_description(self, language: str = "cn") -> str:
        return _DESCRIPTIONS.get(language, _DESCRIPTIONS["cn"])["tool"]

    def get_input_params(self, language: str = "cn") -> dict[str, Any]:
        descriptions = _DESCRIPTIONS.get(language, _DESCRIPTIONS["cn"])
        item = {
            "type": "object",
            "properties": {
                name: {"type": "string", "description": descriptions[name]}
                for name in ("id", "content", "status", "description", "selected_model_id")
            },
            "required": ["id", "content", "status"],
            "additionalProperties": False,
        }
        item["properties"]["status"]["enum"] = [status.value for status in TodoStatus]
        return {
            "type": "object",
            "properties": {
                "revision": {"type": "string", "description": descriptions["revision"]},
                "todos": {"type": "array", "description": descriptions["todos"], "items": item},
            },
            "required": ["todos"],
            "additionalProperties": False,
        }
