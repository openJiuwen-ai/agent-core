# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""JSON extraction helpers for ReflACT LLM outputs."""

from __future__ import annotations

from typing import Any, Dict, Optional

from openjiuwen.agent_evolving.utils import TuneUtils


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from an LLM response."""
    parsed = TuneUtils.parse_json_from_llm_response(text)
    if isinstance(parsed, dict):
        return parsed
    return None
