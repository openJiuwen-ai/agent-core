# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Safe logging helpers for browser-move MCP clients.

These helpers keep low-level transport logs useful without dumping generated
JavaScript, page snapshots, form values, credentials, or other user data.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

_TEXT_VALUE_KEYS = {
    "value",
    "text",
    "query",
    "name",
    "email",
    "phone",
    "mobile",
    "password",
    "address",
    "task_description",
    "code",
    "script",
    "js",
    "expression",
    "function",
}
_BATCH_VALUE_KEYS = {
    "value",
    "text",
    "choose_text",
    "option_text",
    "option_label",
    "option_value",
    "label_value",
    "option_name",
    "values",
}
_BATCH_TARGET_KEYS = {
    "selector",
    "label",
    "placeholder",
    "role",
    "name",
    "aria_label",
    "testid",
    "text",
    "option_role",
    "option_selector",
    "year_selector",
    "month_selector",
    "day_selector",
}


def _safe_str(value: Any, limit: int = 160) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _redacted_text(value: Any) -> dict[str, Any]:
    text = "" if value is None else str(value)
    text_hash = ""
    if text:
        text_hash = hashlib.sha256(
            text.encode("utf-8", errors="ignore")
        ).hexdigest()[:12]
    
    return {
        "kind": type(value).__name__,
        "length": len(text),
        "sha256_12": text_hash,
        "redacted": True,
    }


def _sanitize_url(value: Any) -> str:
    raw = _safe_str(value, 600)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return _safe_str(raw.split("?")[0], 180)
    if not parts.scheme and not parts.netloc:
        return _safe_str(raw.split("?")[0], 180)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "<redacted>" if parts.query else "", ""))


def _summarize_batch_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    steps = arguments.get("steps")
    if not isinstance(steps, list):
        return {
            "kind": "browser_batch_interact",
            "step_count": 0,
            "keys": sorted(str(key) for key in arguments.keys()),
        }

    op_counts = Counter(str(step.get("op") or "<missing>") for step in steps if isinstance(step, Mapping))
    step_summaries = []
    for idx, step in enumerate(steps[:10]):
        if not isinstance(step, Mapping):
            step_summaries.append({"index": idx, "kind": type(step).__name__})
            continue
        keys = {str(key) for key in step.keys()}
        step_summaries.append(
            {
                "index": idx,
                "op": _safe_str(step.get("op") or "", 80),
                "target_keys": sorted(keys & _BATCH_TARGET_KEYS),
                "value_keys_redacted": sorted(keys & _BATCH_VALUE_KEYS),
            }
        )

    return {
        "kind": "browser_batch_interact",
        "step_count": len(steps),
        "op_counts": dict(sorted(op_counts.items())),
        "steps_preview": step_summaries,
        "truncated": len(steps) > len(step_summaries),
        "continue_on_error": bool(arguments.get("continue_on_error", False)),
        "timeout_ms": arguments.get("timeout_ms"),
        "global_timeout_ms": arguments.get("global_timeout_ms"),
    }


def summarize_tool_arguments_for_log(tool_name: str, arguments: Any) -> dict[str, Any]:
    """Return a compact, redacted argument summary for low-level MCP client logs."""
    lowered_name = (tool_name or "").lower()

    if isinstance(arguments, list):
        return {"kind": "list", "length": len(arguments)}

    if not isinstance(arguments, Mapping):
        return {
            "kind": type(arguments).__name__,
            "repr_length": len(str(arguments)),
        }

    keys = sorted(str(key) for key in arguments.keys())

    if lowered_name == "browser_batch_interact":
        return _summarize_batch_arguments(arguments)

    if "run_code" in lowered_name or "evaluate" in lowered_name:
        code = (
            arguments.get("code")
            or arguments.get("script")
            or arguments.get("expression")
            or arguments.get("function")
            or ""
        )

        code_text = str(code)
        code_hash = ""
        if code_text:
            code_hash = hashlib.sha256(
                code_text.encode("utf-8", "ignore")
            ).hexdigest()[:12]

        return {
            "kind": "code_execution",
            "keys": keys,
            "code_length": len(code_text),
            "code_sha256_12": code_hash,
            "code_redacted": True,
        }

    if "navigate" in lowered_name:
        raw_url = (
            arguments.get("url")
            or arguments.get("href")
            or arguments.get("target")
        )
        return {
            "kind": "navigation",
            "keys": keys,
            "url": _sanitize_url(raw_url),
        }

    summary: dict[str, Any] = {
        "kind": "dict",
        "keys": keys,
    }
    safe_values: dict[str, Any] = {}
    redacted_values: dict[str, Any] = {}

    sensitive_key_tokens = {
        "password",
        "token",
        "secret",
        "api_key",
        "apikey",
        "credential",
        "authorization",
    }
    safe_metadata_keys = {
        "selector",
        "role",
        "label",
        "placeholder",
        "checked",
        "timeout",
        "timeout_ms",
        "max_items",
    }

    for key, value in arguments.items():
        key_str = str(key)
        key_lower = key_str.lower()

        is_sensitive_key = any(
            token in key_lower
            for token in sensitive_key_tokens
        )

        if key_lower in _TEXT_VALUE_KEYS or is_sensitive_key:
            redacted_values[key_str] = _redacted_text(value)
        elif key_lower in safe_metadata_keys:
            if isinstance(value, (bool, int, float)):
                safe_values[key_str] = value
            else:
                safe_values[key_str] = _safe_str(value, 120)
        elif key_lower in {"url", "href", "target"}:
            safe_values[key_str] = _sanitize_url(value)

    if safe_values:
        summary["safe_values"] = safe_values

    if redacted_values:
        summary["redacted_values"] = redacted_values

    return summary