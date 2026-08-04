"""Shared helpers for compact LLM request payloads."""

from __future__ import annotations

import json
from typing import Any


def compact_json(payload: Any) -> str:
    return json.dumps(
        prune_empty(payload),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def prune_empty(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned_mapping = {}
        for key, item in value.items():
            cleaned = prune_empty(item)
            if cleaned not in (None, "", [], {}):
                cleaned_mapping[key] = cleaned
        return cleaned_mapping
    if isinstance(value, list):
        cleaned_sequence = []
        for item in value:
            cleaned = prune_empty(item)
            if cleaned not in (None, "", [], {}):
                cleaned_sequence.append(cleaned)
        return cleaned_sequence
    return value
