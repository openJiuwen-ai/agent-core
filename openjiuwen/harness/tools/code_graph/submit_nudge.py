# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Locate-exam gate: widen a one-file packet with already-read files or one hop.

Product ``graph`` never calls this (no ``submit_code_context``). Include
production files the model already read, or hop importers/callers once. Do not
pull in unread ``seen_files`` or graph neighbors.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex
from openjiuwen.core.retrieval.code_graph.query.test_paths import issue_about_tests, is_test_path
from openjiuwen.harness.schema.code_graph import (
    PROMPT_MODE_LOCATE,
    CodeGraphRunState,
)

_NUDGE_EXTRA_READ = "extra_read"
_NUDGE_RELATION_HOP = "relation_hop"
_MAX_ACTIONS = 5


def locate_submit_nudge(
    state: CodeGraphRunState,
    index: CodeGraphIndex | None = None,
) -> dict[str, Any] | None:
    """Return a PARTIAL payload with ``next_actions``, or ``None`` to allow submit."""
    _ = index
    mode = (state.prompt_mode or "").strip().lower()
    if mode != PROMPT_MODE_LOCATE:
        return None
    selected_files = _selected_files(state)
    if len(selected_files) >= 2:
        return None
    allow_tests = issue_about_tests(str(state.request.query or ""))
    extras_read = _extra_read_files(state, selected_files, allow_tests=allow_tests)
    if extras_read and _NUDGE_EXTRA_READ not in state.submit_nudges:
        state.submit_nudges.add(_NUDGE_EXTRA_READ)
        actions = _include_read_actions(state, extras_read)
        return _payload(
            "submit_code_context deferred: include already-read production files "
            "with the primary location, then submit again.",
            actions,
        )
    if (
        not _hopped_relations(state)
        and _NUDGE_EXTRA_READ not in state.submit_nudges
        and _NUDGE_RELATION_HOP not in state.submit_nudges
    ):
        symbol_id = _primary_symbol_id(state)
        if symbol_id:
            state.submit_nudges.add(_NUDGE_RELATION_HOP)
            return _payload(
                "submit_code_context deferred: hop find_importers / find_callers "
                "once before a one-file submit.",
                [
                    {
                        "tool": "find_importers",
                        "symbol_id": symbol_id,
                        "reason": "production modules that import the primary location",
                    },
                    {
                        "tool": "find_callers",
                        "symbol_id": symbol_id,
                        "reason": "callers that may also need to change",
                    },
                ],
            )
    return None


def _payload(message: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "PARTIAL",
        "message": message,
        "submitted": False,
        "next_actions": actions[:_MAX_ACTIONS],
    }


def _selected_files(state: CodeGraphRunState) -> set[str]:
    return {_norm_file(item.file) for item in state.selected if item.file}


def _primary_symbol_id(state: CodeGraphRunState) -> str:
    for item in state.selected:
        symbol_id = str(item.symbol_id or "").strip()
        if symbol_id:
            return symbol_id
    return ""


def _hopped_relations(state: CodeGraphRunState) -> bool:
    return any(
        str(item.relation or "") in {"imported_by", "called_by", "imports", "calls"}
        for item in state.relations
    )


def _extra_read_files(
    state: CodeGraphRunState,
    selected_files: set[str],
    *,
    allow_tests: bool,
) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for payload in state.read_evidence.values():
        if not isinstance(payload, dict) or not payload.get("symbol_id"):
            continue
        file_path = _norm_file(payload.get("file"))
        if not file_path or file_path in selected_files or file_path in seen:
            continue
        if not allow_tests and is_test_path(file_path):
            continue
        seen.add(file_path)
        files.append(file_path)
    return files


def _include_read_actions(
    state: CodeGraphRunState,
    files: list[str],
) -> list[dict[str, Any]]:
    locations = [_location_for_file(state, path) for path in files]
    locations = [item for item in locations if item is not None]
    actions: list[dict[str, Any]] = []
    if locations:
        actions.append(
            {
                "tool": "submit_code_context",
                "locations": locations,
                "reason": "include already-read production files with the primary location",
            }
        )
    for path in files:
        symbol_id = _symbol_for_file(state, path)
        if not symbol_id:
            continue
        actions.append(
            {
                "tool": "read_symbol",
                "symbol_id": symbol_id,
                "file": path,
                "reason": f"already read {path}; include it or drop it before submit",
            }
        )
        if len(actions) >= _MAX_ACTIONS:
            break
    return actions


def _location_for_file(state: CodeGraphRunState, file_path: str) -> dict[str, str] | None:
    symbol_id = _symbol_for_file(state, file_path)
    if not symbol_id:
        return None
    return {"symbol_id": symbol_id, "file": file_path}


def _symbol_for_file(state: CodeGraphRunState, file_path: str) -> str:
    needle = _norm_file(file_path)
    for payload in reversed(list(state.read_evidence.values())):
        if not isinstance(payload, dict):
            continue
        if _norm_file(payload.get("file")) != needle:
            continue
        symbol_id = str(payload.get("symbol_id") or "").strip()
        if symbol_id:
            return symbol_id
    for symbol_id, payload in state.candidates.items():
        if not isinstance(payload, dict):
            continue
        if _norm_file(payload.get("file")) == needle and symbol_id:
            return str(symbol_id)
    return ""


def _norm_file(value: Any) -> str:
    return str(value or "").replace("\\", "/").lstrip("./")
