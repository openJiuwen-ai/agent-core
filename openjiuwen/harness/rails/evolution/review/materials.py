# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Review-scoped trajectory material builders for Skill evolution review."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_evolving.trajectory.types import LLMCallDetail, ToolCallDetail

_REVIEW_TEXT_PREVIEW_CHARS = 240
_REVIEW_DETAIL_TEXT_CHARS = 2000
_REVIEW_LLM_MESSAGE_LIMIT = 8


def build_review_scoped_materials(trajectory) -> dict[str, Any]:
    """Build bounded review materials from a trajectory."""
    if trajectory is None:
        return {}

    index_items: list[dict[str, Any]] = []
    detail_items: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(trajectory.steps):
        ref = f"step-{index + 1}"
        index_item, detail_item = _review_trajectory_step(ref, index, step)
        index_items.append(index_item)
        detail_items[ref] = detail_item

    if not index_items:
        return {}
    return {
        "trajectory": {
            "execution_id": str(getattr(trajectory, "execution_id", "") or ""),
            "session_id": str(getattr(trajectory, "session_id", "") or ""),
            "step_count": len(trajectory.steps),
        },
        "trajectory_steps": index_items,
        "trajectory_step_details": detail_items,
    }


def build_swarm_review_scoped_materials(trajectory) -> dict[str, Any]:
    """Build bounded review materials for swarm/team skill evolution review."""
    materials = build_review_scoped_materials(trajectory)
    if not materials:
        return {}
    materials["swarm_review_focus"] = [
        "role responsibility drift",
        "leader/member handoff failure",
        "routing and delegation ambiguity",
        "shared context/state assumptions",
        "member trajectory mismatch",
        "task completion signal quality",
        "role-local change vs whole-swarm change",
        "collaboration protocol gaps",
        "cross-role tool usage and output dependency",
    ]
    return materials


def _review_trajectory_step(ref: str, index: int, step) -> tuple[dict[str, Any], dict[str, Any]]:
    kind = str(getattr(step, "kind", "") or "")
    detail = getattr(step, "detail", None)
    index_item: dict[str, Any] = {
        "ref": ref,
        "index": index,
        "kind": kind,
        "has_error": bool(getattr(step, "error", None)),
    }
    detail_item: dict[str, Any] = {"ref": ref, "index": index, "kind": kind}
    if isinstance(detail, ToolCallDetail):
        tool_name = str(getattr(detail, "tool_name", "") or "")
        call_result = getattr(detail, "call_result", None)
        result_text = "" if call_result is None else str(call_result)
        index_item["tool_name"] = tool_name
        index_item["summary"] = (f"tool={tool_name} result_preview={result_text[:_REVIEW_TEXT_PREVIEW_CHARS]}").strip()
        detail_item["detail"] = {
            "tool_name": tool_name,
            "call_args": _json_safe_bounded(getattr(detail, "call_args", None)),
            "call_result": result_text[:_REVIEW_DETAIL_TEXT_CHARS],
            "call_result_truncated": len(result_text) > _REVIEW_DETAIL_TEXT_CHARS,
            "call_result_original_chars": len(result_text),
            "tool_call_id": getattr(detail, "tool_call_id", None),
        }
        return index_item, detail_item
    if isinstance(detail, LLMCallDetail):
        messages = list(getattr(detail, "messages", []) or [])
        preview = _bounded_text(
            _last_message_content(messages),
            limit=_REVIEW_TEXT_PREVIEW_CHARS,
        )
        index_item["summary"] = (
            f"llm model={str(getattr(detail, 'model', '') or '')} "
            f"messages={len(messages)} "
            f"response_present={getattr(detail, 'response', None) is not None} "
            f"preview={preview}"
        ).strip()
        messages = list(getattr(detail, "messages", []) or [])[-_REVIEW_LLM_MESSAGE_LIMIT:]
        detail_item["detail"] = {
            "model": str(getattr(detail, "model", "") or ""),
            "messages": [_json_safe_bounded(message) for message in messages],
            "response": _json_safe_bounded(getattr(detail, "response", None)),
            "usage": _json_safe_bounded(getattr(detail, "usage", None)),
            "meta": _json_safe_bounded(getattr(detail, "meta", None)),
        }
        return index_item, detail_item
    index_item["summary"] = _bounded_text(detail, limit=_REVIEW_TEXT_PREVIEW_CHARS)
    detail_item["detail"] = _json_safe_bounded(detail)
    return index_item, detail_item


def _last_message_content(messages: list[Any]) -> Any:
    for message in reversed(messages):
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        if content:
            return content
    return ""


def _bounded_text(value: Any, *, limit: int = _REVIEW_DETAIL_TEXT_CHARS) -> str:
    return "" if value is None else str(value)[:limit]


def _json_safe_bounded(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, list):
        return [_json_safe_bounded(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_bounded(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_bounded(item) for key, item in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return _json_safe_bounded(dumped)
    return _bounded_text(value, limit=_REVIEW_TEXT_PREVIEW_CHARS)


__all__ = ["build_review_scoped_materials", "build_swarm_review_scoped_materials"]
