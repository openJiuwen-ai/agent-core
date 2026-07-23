# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared AgentResult.text policy for schema and free-text paths."""
from __future__ import annotations

import json
from typing import Any


def prefer_natural_or_structured_text(natural: str | None, structured: Any) -> str:
    """Prefer non-empty natural-language text; else JSON-preview structured data.

    Used by both single-shot workers (``agent()``) and session turns
    (``agent_session.send``) when a schema was requested: UI / journal
    ``raw_text`` should show the model's free-text reply when present, and
    only fall back to a JSON dump of the StructuredOutputTool capture.
    """
    if isinstance(natural, str) and natural.strip():
        return natural
    try:
        return json.dumps(structured, ensure_ascii=False, default=str)
    except Exception:
        return str(structured)
