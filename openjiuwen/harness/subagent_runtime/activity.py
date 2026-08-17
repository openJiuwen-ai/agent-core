# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Project subagent stream chunks into activity milestones."""

from __future__ import annotations

import time
from typing import Any

from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.models import SubagentActivity

_PERSISTABLE_KINDS = frozenset({"tool_call", "tool_result", "error"})


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."


def _pick_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _parse_chunk(chunk: Any) -> tuple[str | None, dict[str, Any]]:
    chunk_type = getattr(chunk, "type", None)
    payload = getattr(chunk, "payload", None)
    if isinstance(chunk, dict):
        chunk_type = chunk.get("type", chunk_type)
        payload = chunk.get("payload", payload)
    if not isinstance(payload, dict):
        return chunk_type, {}
    return chunk_type, payload


def _tool_info(payload: dict[str, Any]) -> dict[str, Any]:
    tool_info = payload.get("tool_call") or payload.get("tool_result") or payload
    return tool_info if isinstance(tool_info, dict) else {}


class ActivityProjector:
    """Map subagent stream chunks to bounded activity milestones."""

    def __init__(
        self,
        *,
        subagent_id: str,
        config: SubagentRuntimeConfig,
    ) -> None:
        self._subagent_id = subagent_id
        self._config = config
        self._seq = 0
        self._turn_count = 0
        self._truncated = False
        self._thinking_buffer = ""
        self._last_thinking_emit_ms = 0.0
        self._current_task_id = ""

    def reset_for_turn(self, task_id: str) -> None:
        self._current_task_id = task_id
        self._turn_count = 0
        self._truncated = False
        self._thinking_buffer = ""
        self._last_thinking_emit_ms = 0.0

    def project(self, chunk: Any, *, task_id: str) -> SubagentActivity | None:
        if task_id and task_id != self._current_task_id:
            self.reset_for_turn(task_id)

        chunk_type, payload = _parse_chunk(chunk)
        if self._truncated and chunk_type == "llm_reasoning":
            return None

        max_per_turn = self._config.activity_queue_size
        if not self._truncated and self._turn_count >= max_per_turn:
            self._truncated = True
            return self._make(
                kind="truncated",
                task_id=task_id,
                summary="activity stream truncated for this turn",
                dropped=self._turn_count - max_per_turn + 1,
            )

        if chunk_type == "tool_call":
            return self._emit(self._project_tool_call(payload, task_id))
        if chunk_type == "tool_result":
            return self._emit(self._project_tool_result(payload, task_id))
        if chunk_type == "llm_reasoning":
            return self._project_thinking(payload, task_id)
        if chunk_type == "error":
            return self._emit(self._project_error(payload, task_id))
        return None

    def _emit(self, activity: SubagentActivity | None) -> SubagentActivity | None:
        if activity is None:
            return None
        self._turn_count += 1
        return activity

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _make(
        self,
        *,
        kind: str,
        task_id: str,
        summary: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        ok: bool | None = None,
        dropped: int | None = None,
    ) -> SubagentActivity:
        return SubagentActivity(
            subagent_id=self._subagent_id,
            task_id=task_id,
            seq=self._next_seq(),
            kind=kind,
            summary=_truncate(summary, self._config.activity_text_max_len),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            ok=ok,
            at_ms=time.time() * 1000,
            dropped=dropped,
        )

    def _project_tool_call(self, payload: dict[str, Any], task_id: str) -> SubagentActivity:
        info = _tool_info(payload)
        tool_name = _pick_str(info, "tool_name", "name") or "tool"
        tool_call_id = _pick_str(info, "tool_call_id", "toolCallId")
        arguments = info.get("arguments") or info.get("args") or info.get("input")
        if isinstance(arguments, dict):
            arg_text = ", ".join(f"{key}={value}" for key, value in arguments.items())
        else:
            arg_text = str(arguments or "").strip()
        summary = f"{tool_name}({arg_text})" if arg_text else tool_name
        return self._make(
            kind="tool_call",
            task_id=task_id,
            summary=summary,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )

    def _project_tool_result(self, payload: dict[str, Any], task_id: str) -> SubagentActivity:
        info = _tool_info(payload)
        tool_name = _pick_str(info, "tool_name", "name") or "tool"
        tool_call_id = _pick_str(info, "tool_call_id", "toolCallId")
        ok = info.get("success")
        if ok is None and "is_error" in info:
            ok = not bool(info.get("is_error"))
        if ok is None and info.get("status") is not None:
            ok = str(info.get("status")).lower() not in {"error", "failed", "failure"}
        summary = _pick_str(info, "summary") or _pick_str(info, "result") or tool_name
        return self._make(
            kind="tool_result",
            task_id=task_id,
            summary=summary or tool_name,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            ok=ok if isinstance(ok, bool) else None,
        )

    def _project_error(self, payload: dict[str, Any], task_id: str) -> SubagentActivity:
        summary = _pick_str(payload, "error", "message") or "subagent error"
        return self._make(kind="error", task_id=task_id, summary=summary)

    def _project_thinking(self, payload: dict[str, Any], task_id: str) -> SubagentActivity | None:
        content = payload.get("content")
        if not isinstance(content, str) or not content:
            return None
        self._thinking_buffer += content
        now_ms = time.time() * 1000
        buffer_full = len(self._thinking_buffer) >= self._config.activity_text_max_len
        throttle_elapsed = (
            now_ms - self._last_thinking_emit_ms
        ) >= self._config.activity_throttle_ms
        if not buffer_full and not throttle_elapsed:
            return None

        summary = self._thinking_buffer
        self._thinking_buffer = ""
        self._last_thinking_emit_ms = now_ms
        return self._emit(
            self._make(kind="thinking", task_id=task_id, summary=summary),
        )


__all__ = ["ActivityProjector", "_PERSISTABLE_KINDS"]
