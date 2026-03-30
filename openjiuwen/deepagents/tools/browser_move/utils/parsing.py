# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared parsing helpers for runtime/controller responses."""

from __future__ import annotations

import json
from typing import Any

# Top-level schema keys that OpenAI-compatible APIs (including Dashscope) reject.
_UNSUPPORTED_SCHEMA_KEYS = {"$schema", "$id", "$defs", "definitions", "$comment", "$anchor", "$vocabulary"}


def sanitize_json_schema(schema: Any) -> Any:
    """Recursively strip schema keywords unsupported by OpenAI-compatible APIs.

    Converts ``anyOf: [{type: X}, {type: "null"}]`` nullable patterns to the
    plain type so Dashscope doesn't reject the schema with error 181001.
    """
    if not isinstance(schema, dict):
        return schema

    # Collapse anyOf/oneOf nullable shorthands: [{type: X}, {type: "null"}] → type: X
    for kw in ("anyOf", "oneOf"):
        variants = schema.get(kw)
        if isinstance(variants, list) and len(variants) == 2:
            non_null = [v for v in variants if v != {"type": "null"} and v.get("type") != "null"]
            null_count = len(variants) - len(non_null)
            if null_count == 1 and len(non_null) == 1:
                merged = {k: v for k, v in schema.items() if k != kw}
                merged.update(non_null[0])
                schema = merged

    cleaned = {k: v for k, v in schema.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}

    # Normalize null type to "object" — MCP servers sometimes emit {"type": null}
    # for tools with no parameters, but OpenAI-compatible APIs require type to be a string.
    if cleaned.get("type") is None and "type" in cleaned:
        cleaned["type"] = "object"

    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {k: sanitize_json_schema(v) for k, v in cleaned["properties"].items()}

    for kw in ("items", "additionalProperties", "not"):
        if kw in cleaned:
            cleaned[kw] = sanitize_json_schema(cleaned[kw])

    for kw in ("anyOf", "oneOf", "allOf"):
        if kw in cleaned and isinstance(cleaned[kw], list):
            cleaned[kw] = [sanitize_json_schema(v) for v in cleaned[kw]]

    return cleaned


def extract_json_object(text: Any) -> dict[str, Any]:
    """Best-effort JSON extraction from model or tool text."""
    if isinstance(text, dict):
        return text
    if text is None:
        return {}

    raw = str(text).strip()
    if not raw:
        return {}

    marker_result = "### Result"
    marker_ran = "### Ran Playwright code"
    if marker_result in raw and marker_ran in raw:
        start = raw.find(marker_result) + len(marker_result)
        end = raw.find(marker_ran, start)
        if end > start:
            raw = raw[start:end].strip()

    for _ in range(2):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            break
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            raw = parsed.strip()
            continue
        break

    if "```json" in raw:
        start = raw.find("```json") + len("```json")
        end = raw.find("```", start)
        if end > start:
            block = raw[start:end].strip()
            try:
                parsed = json.loads(block)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed

    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        snippet = raw[first:last + 1]
        try:
            parsed = json.loads(snippet)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    return {}
