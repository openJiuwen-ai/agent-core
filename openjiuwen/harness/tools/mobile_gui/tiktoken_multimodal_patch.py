# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime monkey-patch for multimodal-aware Tiktoken counting (mobile GUI / vision skills).

Without this patch, ``TiktokenCounter.count_messages`` interpolates ``list`` ``content``
via ``repr()``, ballooning estimates when ``image_url`` blocks embed large ``data:`` URLs
and DialogueCompressor trims away skill or screenshot turns unexpectedly.
"""

from __future__ import annotations

import json
import os
from typing import List

from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage

DEFAULT_IMAGE_PLACEHOLDER_TOKENS = 1445
_PATCHED = False


def _image_placeholder_tokens() -> int:
    raw = os.getenv("TIKTOKEN_IMAGE_PLACEHOLDER_TOKENS")
    if raw is None or not raw.strip():
        return DEFAULT_IMAGE_PLACEHOLDER_TOKENS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_IMAGE_PLACEHOLDER_TOKENS
    return value if value > 0 else DEFAULT_IMAGE_PLACEHOLDER_TOKENS


def _compact_json_tokens(counter: TiktokenCounter, payload: dict, *, model: str = "", **kwargs) -> int:
    return counter.count(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        model=model,
        **kwargs,
    )


def _count_messages_multimodal(
    self: TiktokenCounter,
    messages: List[BaseMessage],
    *,
    model: str = "",
    **kwargs,
) -> int:
    placeholder = _image_placeholder_tokens()
    if not messages:
        return 0
    total = 0
    for msg in messages:
        if isinstance(msg.content, list):
            text_parts: list[str] = []
            for part in msg.content:
                if isinstance(part, dict):
                    ptype = part.get("type")
                    if ptype in ("image_url", "image") or "image_url" in part or "image" in part:
                        total += placeholder
                    elif ptype == "text" or "text" in part:
                        text_parts.append(str(part.get("text") or ""))
                    else:
                        total += _compact_json_tokens(self, part, model=model, **kwargs)
                elif isinstance(part, str):
                    text_parts.append(part)
            combined_text = "\n".join(text_parts)
            piece = f"<|start|>{msg.role}\n{combined_text}<|end|>"
        else:
            piece = f"<|start|>{msg.role}\n{msg.content}<|end|>"

        total += self.count(piece, model=model, **kwargs)

        if isinstance(msg, AssistantMessage):
            dict_msg = msg.model_dump()
            tool_calls = dict_msg.get("tool_calls")
            if tool_calls:
                total += self.count(
                    json.dumps(dict_msg["tool_calls"], ensure_ascii=False),
                    model=model,
                    **kwargs,
                )
    return total + 3


def apply_tiktoken_counter_multimodal_patch() -> None:
    """Replace TiktokenCounter.count_messages with multimodal-aware counting."""
    global _PATCHED  # noqa: PLW0603 — single guarded module flag
    if _PATCHED:
        return
    TiktokenCounter.count_messages = _count_messages_multimodal  # type: ignore[method-assign]
    _PATCHED = True


__all__ = [
    "apply_tiktoken_counter_multimodal_patch",
]
