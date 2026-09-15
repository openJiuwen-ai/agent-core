# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Helpers for ReflACT skill training."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from openjiuwen.agent_evolving.utils import TuneUtils


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from an LLM response."""
    parsed = TuneUtils.parse_json_from_llm_response(text)
    if isinstance(parsed, dict):
        return parsed
    return None


def safe_fs_id(value: str) -> str:
    """Make item ids safe as Windows directory names (e.g. strip ':')."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return safe[:120] or "item"


def prediction_item_dir(prediction_dir: str, task_id: str) -> str:
    """Resolve ``predictions/<id>/``, trying raw then sanitized id (Windows)."""
    raw = os.path.join(prediction_dir, str(task_id))
    if os.path.isdir(raw):
        return raw
    safe = os.path.join(prediction_dir, safe_fs_id(task_id))
    if os.path.isdir(safe):
        return safe
    return raw
