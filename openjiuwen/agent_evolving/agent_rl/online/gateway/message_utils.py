# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Message helpers used by gateway runtime."""

from __future__ import annotations

from typing import Any


def flatten_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return " ".join(parts).strip()
    if content is None:
        return ""
    return str(content)


def extract_last_user_instruction(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = flatten_message_content(msg.get("content"))
            if text:
                return text
    return ""
