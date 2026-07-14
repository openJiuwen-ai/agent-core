# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Review-scoped trajectory material builders for Skill evolution review."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    trajectory_execution_id,
    trajectory_session_id,
    trajectory_steps,
)

_REVIEW_TEXT_PREVIEW_CHARS = 240
_REVIEW_DETAIL_TEXT_CHARS = 1200
_REVIEW_ARG_TEXT_CHARS = 800
_REVIEW_LLM_MESSAGE_TEXT_CHARS = 500
_REVIEW_LLM_MESSAGE_LIMIT = 3
_REVIEW_MAX_STEPS = 80
_REVIEW_HEAD_STEPS = 10
_REVIEW_TAIL_STEPS = 30


def build_review_scoped_materials(trajectory: Trajectory | None) -> dict[str, Any]:
    """Build bounded review materials from a trajectory."""
    if trajectory is None:
        return {}

    index_items: list[dict[str, Any]] = []
    detail_items: dict[str, dict[str, Any]] = {}
    steps = trajectory_steps(trajectory)
    selected_steps = _select_review_steps(steps)
    for index, step in selected_steps:
        ref = f"step-{index + 1}"
        index_item, detail_item = _review_trajectory_step(ref, index, step)
        index_items.append(index_item)
        detail_items[ref] = detail_item

    if not index_items:
        return {}
    return {
        "trajectory": {
            "execution_id": trajectory_execution_id(trajectory),
            "session_id": str(trajectory_session_id(trajectory) or ""),
            "step_count": len(steps),
            "included_step_count": len(selected_steps),
            "omitted_step_count": max(0, len(steps) - len(selected_steps)),
            "material_format": "compact_trajectory_step_projection",
        },
        "trajectory_steps": index_items,
        "trajectory_step_details": detail_items,
    }


def build_swarm_review_scoped_materials(trajectory: Trajectory | None) -> dict[str, Any]:
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
        result_preview = _value_preview(call_result, limit=_REVIEW_TEXT_PREVIEW_CHARS)
        result_text = _value_preview(call_result, limit=_REVIEW_DETAIL_TEXT_CHARS)
        result_original_chars = len(call_result) if isinstance(call_result, str) else None
        index_item["tool_name"] = tool_name
        index_item["summary"] = (f"tool={tool_name} result_preview={result_preview}").strip()
        detail_item["detail"] = {
            "tool_name": tool_name,
            "call_args": _json_safe_bounded(getattr(detail, "call_args", None), limit=_REVIEW_ARG_TEXT_CHARS),
            "call_result": result_text,
            "call_result_truncated": bool(
                result_original_chars is not None and result_original_chars > _REVIEW_DETAIL_TEXT_CHARS
            ),
            "call_result_original_chars": result_original_chars,
            "tool_call_id": getattr(detail, "tool_call_id", None),
        }
        return index_item, detail_item
    if isinstance(detail, LLMCallDetail):
        messages = list(getattr(detail, "messages", []) or [])
        preview = _bounded_text(
            _message_content_text(_last_message_content(messages)),
            limit=_REVIEW_TEXT_PREVIEW_CHARS,
        )
        index_item["summary"] = (
            f"llm model={str(getattr(detail, 'model', '') or '')} "
            f"messages={len(messages)} "
            f"response_present={getattr(detail, 'response', None) is not None} "
            f"preview={preview}"
        ).strip()
        detail_item["detail"] = {
            "model": str(getattr(detail, "model", "") or ""),
            "message_count": len(messages),
            "message_previews": _message_previews(messages),
            "response_present": getattr(detail, "response", None) is not None,
            "response_preview": _value_preview(getattr(detail, "response", None), limit=_REVIEW_DETAIL_TEXT_CHARS),
            "usage": _json_safe_bounded(getattr(detail, "usage", None), limit=_REVIEW_ARG_TEXT_CHARS),
        }
        return index_item, detail_item
    index_item["summary"] = _bounded_text(detail, limit=_REVIEW_TEXT_PREVIEW_CHARS)
    detail_item["detail"] = _json_safe_bounded(detail, limit=_REVIEW_DETAIL_TEXT_CHARS)
    return index_item, detail_item


def _select_review_steps(steps: list[Any]) -> list[tuple[int, Any]]:
    indexed_steps = list(enumerate(steps))
    if len(indexed_steps) <= _REVIEW_MAX_STEPS:
        return indexed_steps

    selected: dict[int, Any] = {}
    for index, step in indexed_steps[:_REVIEW_HEAD_STEPS]:
        selected[index] = step
    for index, step in indexed_steps[-_REVIEW_TAIL_STEPS:]:
        selected[index] = step
    for index, step in indexed_steps:
        if _is_review_important_step(step):
            selected[index] = step
        if len(selected) >= _REVIEW_MAX_STEPS:
            break
    return sorted(selected.items())[:_REVIEW_MAX_STEPS]


def _is_review_important_step(step: Any) -> bool:
    if getattr(step, "error", None):
        return True
    return str(getattr(step, "kind", "") or "") == "tool"


def _last_message_content(messages: list[Any]) -> Any:
    for message in reversed(messages):
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        if content:
            return content
    return ""


def _message_previews(messages: list[Any]) -> list[dict[str, str]]:
    previews: list[dict[str, str]] = []
    for message in messages[-_REVIEW_LLM_MESSAGE_LIMIT:]:
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", "")
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        previews.append(
            {
                "role": str(role or ""),
                "content_preview": _bounded_text(
                    _message_content_text(content),
                    limit=_REVIEW_LLM_MESSAGE_TEXT_CHARS,
                ),
            }
        )
    return previews


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("value")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return "\n".join(parts)
    return "" if content is None else str(content)


def _bounded_text(value: Any, *, limit: int = _REVIEW_DETAIL_TEXT_CHARS) -> str:
    return "" if value is None else str(value)[:limit]


def _value_preview(value: Any, *, limit: int = _REVIEW_DETAIL_TEXT_CHARS) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _bounded_text(value, limit=limit)
    if isinstance(value, (int, float, bool)):
        return _bounded_text(value, limit=limit)
    if isinstance(value, (list, tuple)):
        preview_items = [_value_preview(item, limit=160) for item in list(value)[:3]]
        preview = {"type": "list", "len": len(value), "items": preview_items}
        return _bounded_text(preview, limit=limit)
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        for key, item in list(value.items())[:8]:
            if isinstance(item, (dict, list, tuple)):
                preview[str(key)] = _container_marker(item)
            else:
                preview[str(key)] = _value_preview(item, limit=160)
        if len(value) > 8:
            preview["..."] = f"{len(value) - 8} more keys"
        return _bounded_text(preview, limit=limit)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return _value_preview(dumped, limit=limit)
    return _bounded_text(value, limit=limit)


def _container_marker(value: Any) -> str:
    if isinstance(value, dict):
        return f"<dict keys={len(value)}>"
    if isinstance(value, (list, tuple)):
        return f"<list len={len(value)}>"
    return f"<{type(value).__name__}>"


def _json_safe_bounded(value: Any, *, limit: int = _REVIEW_DETAIL_TEXT_CHARS) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _bounded_text(value, limit=limit)
    if isinstance(value, list):
        return [_json_safe_bounded(item, limit=limit) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_bounded(item, limit=limit) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_bounded(item, limit=limit) for key, item in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return _json_safe_bounded(dumped, limit=limit)
    return _bounded_text(value, limit=_REVIEW_TEXT_PREVIEW_CHARS)


__all__ = ["build_review_scoped_materials", "build_swarm_review_scoped_materials"]
