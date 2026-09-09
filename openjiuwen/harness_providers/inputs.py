# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers turning protocol inputs into the plain text most SDKs accept."""

from __future__ import annotations

import json
from typing import Any, Mapping

from openjiuwen.harness_protocol import HarnessInput, json_value_to_builtin


def harness_input_text(content: HarnessInput) -> str:
    """Render a ``HarnessInput`` as one prompt string.

    Strings pass through; a list of ``{"type": "text", "text": ...}`` blocks
    is joined with blank lines; any other JSON value is serialized compactly
    so structured inputs never silently degrade to ``str(dict)``.
    """

    value = json_value_to_builtin(content.content)
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(_is_text_block(block) for block in value):
        return "\n\n".join(str(block["text"]) for block in value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _is_text_block(block: Any) -> bool:
    return isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str)


__all__ = ["harness_input_text"]
