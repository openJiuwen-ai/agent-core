# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Counter backed by a model-provided HuggingFace tokenizer artifact."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from openjiuwen.core.context_engine.content_sanitize import sanitize_content_for_text, sanitize_value_for_text
from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage
from openjiuwen.core.foundation.tool import ToolInfo


class NativeTokenizerCounter(TokenCounter):
    """Count tokens with a local ``tokenizer.json`` artifact."""

    measurement_source = "native_tokenizer"
    measurement_estimated = True

    def __init__(
        self,
        tokenizer_path: str | Path,
        *,
        model: str = "",
        tokenizer_model: str | None = None,
        measurement_source: str = "native_tokenizer",
        fallback_reason: str | None = None,
        fallback_tokenizer_model: str | None = None,
    ) -> None:
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - dependency is required by pyproject
            raise RuntimeError("HuggingFace tokenizers is required for native tokenizer counting") from exc

        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._model = model
        self.measurement_source = measurement_source
        self.measurement_estimated = measurement_source != "native_tokenizer"
        self.measurement_tokenizer = tokenizer_model or Path(tokenizer_path).name
        self.measurement_fallback_reason = fallback_reason
        self.measurement_fallback_tokenizer_model = fallback_tokenizer_model

    def count(self, text: str, *, model: str = "", **kwargs) -> int:
        return len(self._tokenizer.encode(str(text)).ids)

    def count_messages(self, messages: List[BaseMessage], *, model: str = "", **kwargs) -> int:
        if not messages:
            return 0
        total = 0
        for message in messages:
            content = self.content_text(message.content)
            piece = f"<|start|>{message.role}\n{content}<|end|>"
            total += self.count(piece, model=model, **kwargs)
            if isinstance(message, AssistantMessage) and message.tool_calls:
                total += self.count(
                    json.dumps([call.model_dump() for call in message.tool_calls], ensure_ascii=False),
                    model=model,
                    **kwargs,
                )
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
            piece = f"<|start|>functions.{tool.name}:{index}\n"
            piece += json.dumps(function_obj, ensure_ascii=False, separators=(",", ":"))
            piece += "<|end|>"
            total += self.count(piece, model=model, **kwargs)
        return total + 3

    @staticmethod
    def content_text(content: object) -> str:
        """Convert message content into text for token counting."""
        if isinstance(content, str):
            return sanitize_content_for_text(content)
        if not isinstance(content, list):
            return str(content or "")

        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and (part.get("type") == "text" or "text" in part):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, dict):
                sanitized = sanitize_value_for_text(part)
                parts.append(json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(parts)


__all__ = ["NativeTokenizerCounter"]
