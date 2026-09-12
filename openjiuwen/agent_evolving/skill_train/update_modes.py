# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Canonical update-mode tokens and payload helpers for skill revision."""

from __future__ import annotations

from enum import Enum
from typing import Any


class UpdateMode(str, Enum):
    PATCH = "patch"
    REWRITE = "rewrite_from_suggestions"
    FULL_REWRITE = "full_rewrite_minibatch"


_ALIAS_TABLE: dict[str, UpdateMode] = {
    "patch": UpdateMode.PATCH,
    "edits": UpdateMode.PATCH,
    "rewrite": UpdateMode.REWRITE,
    "rewrite_from_suggestions": UpdateMode.REWRITE,
    "suggestions": UpdateMode.REWRITE,
    "rewrite_suggestions": UpdateMode.REWRITE,
    "full_rewrite": UpdateMode.FULL_REWRITE,
    "full_rewrite_minibatch": UpdateMode.FULL_REWRITE,
    "minibatch_full_rewrite": UpdateMode.FULL_REWRITE,
    "skill_rewrite_minibatch": UpdateMode.FULL_REWRITE,
}

_PAYLOAD_KEYS = {
    UpdateMode.PATCH: "edits",
    UpdateMode.REWRITE: "revise_suggestions",
    UpdateMode.FULL_REWRITE: "skill_candidates",
}

_LABELS = {
    UpdateMode.PATCH: ("edit", "edits"),
    UpdateMode.REWRITE: ("suggestion", "suggestions"),
    UpdateMode.FULL_REWRITE: ("skill candidate", "skill candidates"),
}


def normalize_update_mode(mode: str | None) -> str:
    """Map aliases and defaults to a canonical update-mode token."""
    token = UpdateMode.PATCH.value if mode is None else str(mode).strip().lower() or UpdateMode.PATCH.value
    return _ALIAS_TABLE.get(token, UpdateMode.PATCH).value


def _resolve(mode: str | None) -> UpdateMode:
    return UpdateMode(normalize_update_mode(mode))


def is_rewrite_mode(mode: str | None) -> bool:
    return _resolve(mode) is UpdateMode.REWRITE


def is_full_rewrite_minibatch_mode(mode: str | None) -> bool:
    return _resolve(mode) is UpdateMode.FULL_REWRITE


def payload_key(mode: str | None) -> str:
    return _PAYLOAD_KEYS[_resolve(mode)]


def payload_label(mode: str | None, *, singular: bool = False, title: bool = False) -> str:
    one, many = _LABELS[_resolve(mode)]
    text = one if singular else many
    return text.title() if title else text


def get_payload_items(container: dict | None, mode: str | None) -> list[dict]:
    if not isinstance(container, dict):
        return []
    value = container.get(payload_key(mode), [])
    return list(value) if isinstance(value, list) else []


def set_payload_items(container: dict, items: list[dict], mode: str | None) -> dict:
    key = payload_key(mode)
    container[key] = [*items]
    return container


def truncate_payload(container: dict, max_items: int, mode: str | None) -> dict:
    if max_items < 0:
        return container
    current = get_payload_items(container, mode)
    if len(current) <= max_items:
        return container
    clipped = current[0:max_items]
    return set_payload_items(container, clipped, mode)


def _append_kv(parts: list[str], key: str, value: Any) -> None:
    if value in (None, ""):
        return
    parts.append(f"{key}={value!r}" if isinstance(value, str) else f"{key}={value}")


def describe_item(item: dict, mode: str | None, *, max_chars: int | None = None) -> str:
    """Return a verbose text description of one update payload item."""
    if not isinstance(item, dict):
        return ""
    kind = _resolve(mode)
    chunks: list[str] = []
    if kind is UpdateMode.FULL_REWRITE:
        chunks.append(f"title={item.get('title', '')!r}")
        chunks.append(f"change_summary={item.get('change_summary', [])!r}")
        source = item.get("source_type")
        if source:
            chunks.append(f"source={source}")
        support = item.get("support_count")
        if support is not None:
            chunks.append(f"support={support}")
        skill_preview = str(item.get("new_skill", "")).strip()
        if skill_preview:
            chunks.append(f"new_skill_preview={skill_preview!r}")
    elif kind is UpdateMode.REWRITE:
        chunks.append(f"type={item.get('type', '?')}")
        chunks.append(f"title={item.get('title', '')!r}")
        chunks.append(f"instruction={item.get('instruction', '')!r}")
        hint = item.get("priority_hint")
        if hint:
            chunks.append(f"priority={hint}")
        support = item.get("support_count")
        if support is not None:
            chunks.append(f"support={support}")
    else:
        chunks.append(f"op={item.get('op', '?')}")
        _append_kv(chunks, "target", item.get("target", ""))
        _append_kv(chunks, "content", item.get("content", ""))
        support = item.get("support_count")
        if support is not None:
            chunks.append(f"support={support}")
    return "  ".join(chunks)


def short_item_summary(item: dict, mode: str | None, *, max_chars: int | None = None) -> dict[str, Any]:
    """Return a compact dict summary of one update payload item."""
    kind = _resolve(mode)
    if kind is UpdateMode.FULL_REWRITE:
        change_raw = item.get("change_summary")
        summary = [str(entry) for entry in change_raw] if isinstance(change_raw, list) else []
        return {
            "title": str(item.get("title", "")),
            "change_summary": summary,
            "source_type": item.get("source_type", ""),
        }
    if kind is UpdateMode.REWRITE:
        return {
            "type": item.get("type", "?"),
            "title": str(item.get("title", "")),
            "instruction": str(item.get("instruction", "")),
        }
    return {
        "op": item.get("op", "?"),
        "content": str(item.get("content", "")),
        "target": item.get("target", ""),
    }


# Public constants preserved for importers.
PATCH_MODE = UpdateMode.PATCH.value
REWRITE_MODE = UpdateMode.REWRITE.value
FULL_REWRITE_MINIBATCH_MODE = UpdateMode.FULL_REWRITE.value
