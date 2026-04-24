# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "向用户提问以收集信息、澄清歧义或做出决策。支持1-4个问题，每个问题2-4个选项。"
        "\n\n"
        "何时主动使用：需求模糊、多种方案可选、涉及用户偏好时，应主动询问而非假设。"
        "\n\n"
        "【禁止】选项中添加'其他'、'自定义'等兜底选项，系统已自动提供。"
        "【推荐】将推荐选项放第一位，label末尾加'（推荐）'。"
        "preview字段仅用于单选问题的视觉比较场景。"
    ),
    "en": (
        "Ask user questions to gather info, clarify ambiguity, or make decisions. "
        "Supports 1-4 questions, each with 2-4 options."
        "\n\n"
        "When to use proactively: Ask when requirements are vague, multiple approaches exist, "
        "or user preferences matter. Don't assume."
        "\n\n"
        "FORBIDDEN: Adding 'Other', 'Custom' etc. as options — system provides this automatically. "
        "RECOMMENDED: Place recommended option first, append '(Recommended)' to its label. "
        "Preview field is only for single-select questions with visual comparison needs."
    ),
}

ASK_USER_PARAMS: Dict[str, Dict[str, str]] = {
    "questions": {
        "cn": "向用户提出的问题列表（1-4个）",
        "en": "Questions to ask the user (1-4 questions)",
    },
    "header": {
        "cn": "问题的简短标题或标签",
        "en": "A short label or tag for the question (max 12 chars)",
    },
    "question": {
        "cn": "完整的问题文本",
        "en": "The complete question to ask",
    },
    "options": {
        "cn": "可选答案列表（2-4个）",
        "en": "Available choices for this question (2-4 options)",
    },
    "options_label": {
        "cn": "选项显示文本（1-5个词）",
        "en": "The display text for this option (1-5 words).",
    },
    "options_description": {
        "cn": "选项详细说明",
        "en": "Explanation of what this option means or what will happen if chosen.",
    },
    "options_preview": {
        "cn": "可选的预览内容，用于UI模型、代码片段或视觉比较。仅在单选问题中支持。",
        "en": "Optional preview content rendered when this option is focused. Use for mockups, "
              "code snippets, or visual comparisons. Only supported for single-select questions.",
    },
    "multi_select": {
        "cn": "是否允许多选",
        "en": "Set to true to allow the user to select multiple options instead of just one.",
    },
}


def get_ask_user_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for ask_user tool input_params."""
    p = ASK_USER_PARAMS
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": p["questions"].get(language, p["questions"]["cn"]),
                "items": {
                    "type": "object",
                    "properties": {
                        "header": {
                            "type": "string",
                            "description": p["header"].get(language, p["header"]["cn"]),
                        },
                        "question": {
                            "type": "string",
                            "description": p["question"].get(language, p["question"]["cn"]),
                        },
                        "options": {
                            "type": "array",
                            "description": p["options"].get(language, p["options"]["cn"]),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": p["options_label"].get(language, p["options_label"]["cn"]),
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": p["options_description"].get(
                                            language, p["options_description"]["cn"],
                                        ),
                                    },
                                    "preview": {
                                        "type": "string",
                                        "description": p["options_preview"].get(language, p["options_preview"]["cn"]),
                                    },
                                },
                                "required": ["label", "description"],
                            },
                        },
                        "multi_select": {
                            "type": "boolean",
                            "default": False,
                            "description": p["multi_select"].get(language, p["multi_select"]["cn"]),
                        },
                    },
                    "required": ["header", "question", "options"],
                },
                "minItems": 1,
                "maxItems": 4,
            },
        },
        "required": ["questions"],
    }


class AskUserMetadataProvider(ToolMetadataProvider):
    """AskUser 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "ask_user"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_ask_user_input_params(language)
