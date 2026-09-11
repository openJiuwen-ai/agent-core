# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Last-resort deterministic token estimate based on Unicode string length."""

from __future__ import annotations

import json
from typing import List

from openjiuwen.core.context_engine.content_sanitize import sanitize_content_for_text
from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage
from openjiuwen.core.foundation.tool import ToolInfo


class StringLengthCounter(TokenCounter):
    """Count canonical serialized text by Unicode code-point length."""

    measurement_source = "string_length_fallback"
    measurement_estimated = True
    measurement_tokenizer = "unicode_codepoints"

    def __init__(self, *, model: str = "", fallback_reason: str | None = None) -> None:
        self._model = model
        self.measurement_fallback_reason = fallback_reason

    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        return len(str(text)) // 3

    def count_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> int:
        if not messages:
            return 0
        total = 0
        for message in messages:
            content = self._content_text(message.content)
            total += self.count(f"<|start|>{message.role}\n{content}<|end|>")
            if isinstance(message, AssistantMessage) and message.tool_calls:
                total += self.count(json.dumps([call.model_dump() for call in message.tool_calls], ensure_ascii=False))
        return total + 3

    def count_tools(self, tools: List[ToolInfo], *, model: str = "", **kwargs) -> int:
        if not tools:
            return 0
        total = 0
        for index, tool in enumerate(tools):
            function_obj = {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters,
            }
            json_str = json.dumps(function_obj, ensure_ascii=False, separators=(",", ":"))
            total += self.count(f"<|start|>functions.{tool.name}:{index}\n{json_str}<|end|>")
        return total + 3

    @staticmethod
    def _content_text(content: object) -> str:
        if isinstance(content, str):
            return sanitize_content_for_text(content)
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("type") or ""))
            return "\n".join(parts)
        return str(content or "")


__all__ = ["StringLengthCounter"]
