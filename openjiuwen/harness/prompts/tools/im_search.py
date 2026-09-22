# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for the im_search tool (OJ-06)."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

IM_SEARCH_DESCRIPTION: Dict[str, str] = {
    "cn": "按关键词检索学习范围内的 IM 原始消息，支持按会话、人员、时间过滤，返回原文与出处（只读）。",
    "en": (
        "Search original IM messages within the learning scope by keyword, "
        "filterable by conversation, sender, and time; read-only."
    ),
}


IM_SEARCH_PARAMS: Dict[str, Dict[str, str]] = {
    "keyword": {"cn": "检索关键词（中英文均可）", "en": "Search keyword (Chinese or English)"},
    "conversation": {
        "cn": "会话过滤：群/私聊的 external_id 或名称关键词（可选）",
        "en": "Conversation filter: external_id or name keyword (optional)",
    },
    "sender": {"cn": "人员过滤：账号或昵称（可选）", "en": "Sender filter: account or display name (optional)"},
    "since": {
        "cn": "起始时间：ISO 8601 或相对表述如 7d/24h/30m（可选）",
        "en": "Start time: ISO 8601 or relative like 7d/24h/30m (optional)",
    },
    "until": {
        "cn": "结束时间：ISO 8601 或相对表述如 7d/24h/30m（可选）",
        "en": "End time: ISO 8601 or relative like 7d/24h/30m (optional)",
    },
    "limit": {"cn": "单页条数，默认 20、最大 50（可选）", "en": "Page size, default 20, max 50 (optional)"},
    "offset": {"cn": "翻页偏移（可选）", "en": "Paging offset (optional)"},
}


def get_im_search_input_params(language: str = "cn") -> Dict[str, Any]:
    p = IM_SEARCH_PARAMS
    return {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": p["keyword"].get(language, p["keyword"]["cn"])},
            "conversation": {"type": "string", "description": p["conversation"].get(language, p["conversation"]["cn"])},
            "sender": {"type": "string", "description": p["sender"].get(language, p["sender"]["cn"])},
            "since": {"type": "string", "description": p["since"].get(language, p["since"]["cn"])},
            "until": {"type": "string", "description": p["until"].get(language, p["until"]["cn"])},
            "limit": {"type": "integer", "description": p["limit"].get(language, p["limit"]["cn"])},
            "offset": {"type": "integer", "description": p["offset"].get(language, p["offset"]["cn"])},
        },
        "required": ["keyword"],
    }


class ImSearchMetadataProvider(ToolMetadataProvider):
    """im_search ToolMetadataProvider。"""

    def get_name(self) -> str:
        return "im_search"

    def get_description(self, language: str = "cn") -> str:
        return IM_SEARCH_DESCRIPTION.get(language, IM_SEARCH_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_im_search_input_params(language)


__all__ = [
    "IM_SEARCH_DESCRIPTION",
    "IM_SEARCH_PARAMS",
    "ImSearchMetadataProvider",
    "get_im_search_input_params",
]
