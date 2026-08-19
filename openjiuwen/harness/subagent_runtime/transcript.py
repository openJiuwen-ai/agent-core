# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Project subagent stream chunks into full-fidelity transcript messages."""

from __future__ import annotations

import json
import time
from typing import Any

from openjiuwen.harness.subagent_runtime.activity import _parse_chunk, _pick_str, _tool_info
from openjiuwen.harness.subagent_runtime.models import SubagentMessage
from openjiuwen.harness.subagent_runtime.stream_output import TurnOutputAggregator


class TranscriptProjector:
    """Map subagent stream chunks to durable transcript messages."""

    def __init__(
        self,
        *,
        subagent_id: str,
        parent_session_id: str,
    ) -> None:
        self._subagent_id = subagent_id
        self._parent_session_id = parent_session_id
        self._seq = 0
        self._reasoning_parts: list[str] = []
        self._current_task_id = ""
        self._phase_id = 0
        self._phase_open = False
        self._saw_reasoning = False

    def reset_for_turn(self, task_id: str) -> None:
        self._current_task_id = task_id
        self._reasoning_parts = []
        self._phase_open = False
        self._saw_reasoning = False
        # _phase_id is deliberately not reset: ids stay unique across turns.

    @property
    def phase_id(self) -> int:
        return self._phase_id

    def begin_turn(self, task_id: str, user_query: str) -> SubagentMessage:
        if task_id != self._current_task_id:
            self.reset_for_turn(task_id)
        return self._make(
            task_id=task_id,
            role="user",
            event_type="",
            content=str(user_query or ""),
        )

    def project(self, chunk: Any, *, task_id: str) -> SubagentMessage | None:
        if task_id and task_id != self._current_task_id:
            self.reset_for_turn(task_id)

        chunk_type, payload = _parse_chunk(chunk)
        if chunk_type == "tool_call":
            return self._project_tool_call(payload, task_id)
        if chunk_type == "tool_result":
            return self._project_tool_result(payload, task_id)
        if chunk_type == "llm_reasoning":
            self._accumulate_reasoning(payload)
            return None
        if chunk_type == "error":
            return self._project_error(payload, task_id)
        return None

    def end_turn(
        self,
        task_id: str,
        aggregator: TurnOutputAggregator,
    ) -> SubagentMessage:
        reasoning = self._take_phase_reasoning()
        if reasoning is None and not self._saw_reasoning:
            reasoning = (aggregator.reasoning_text() or "").strip() or None
        if aggregator.is_error():
            return self._make(
                task_id=task_id,
                role="assistant",
                event_type="chat.error",
                content=aggregator.output() or "subagent error",
                reasoning_content=reasoning,
            )
        return self._make(
            task_id=task_id,
            role="assistant",
            event_type="chat.final",
            content=aggregator.output(),
            reasoning_content=reasoning,
        )

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _make(
        self,
        *,
        task_id: str,
        role: str,
        event_type: str,
        content: str,
        reasoning_content: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        success: bool | None = None,
        extra: dict[str, Any] | None = None,
    ) -> SubagentMessage:
        return SubagentMessage(
            subagent_id=self._subagent_id,
            parent_session_id=self._parent_session_id,
            task_id=task_id,
            seq=self._next_seq(),
            role=role,
            event_type=event_type,
            content=content,
            reasoning_content=reasoning_content,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            success=success,
            extra=extra,
            at_ms=time.time() * 1000,
            phase_id=self._phase_id,
        )

    def _accumulate_reasoning(self, payload: dict[str, Any]) -> None:
        content = payload.get("content")
        if not isinstance(content, str) or not content:
            return
        if not self._phase_open:
            self._phase_id += 1
            self._phase_open = True
        self._saw_reasoning = True
        self._reasoning_parts.append(content)

    def _take_phase_reasoning(self) -> str | None:
        """Detach the reasoning accumulated since the last phase boundary."""
        text = "".join(self._reasoning_parts).strip()
        self._reasoning_parts = []
        self._phase_open = False
        return text or None

    def _project_tool_call(self, payload: dict[str, Any], task_id: str) -> SubagentMessage:
        info = _tool_info(payload)
        tool_name = _pick_str(info, "tool_name", "name") or "tool"
        tool_call_id = _pick_str(info, "tool_call_id", "toolCallId")
        arguments = info.get("arguments") or info.get("args") or info.get("input")
        if isinstance(arguments, dict):
            arg_text = ", ".join(f"{key}={value}" for key, value in arguments.items())
        elif arguments is not None:
            arg_text = str(arguments).strip()
        else:
            arg_text = ""
        content = f"{tool_name}({arg_text})" if arg_text else tool_name
        return self._make(
            task_id=task_id,
            role="assistant",
            event_type="chat.tool_call",
            content=content,
            reasoning_content=self._take_phase_reasoning(),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            extra={"tool_call": info},
        )

    def _project_tool_result(self, payload: dict[str, Any], task_id: str) -> SubagentMessage:
        info = _tool_info(payload)
        tool_name = _pick_str(info, "tool_name", "name") or "tool"
        tool_call_id = _pick_str(info, "tool_call_id", "toolCallId")
        success = info.get("success")
        if success is None and "is_error" in info:
            success = not bool(info.get("is_error"))
        if success is None and info.get("status") is not None:
            success = str(info.get("status")).lower() not in {"error", "failed", "failure"}
        result_text = _pick_str(info, "summary") or _pick_str(info, "result")
        if result_text is None:
            result_text = json.dumps(info, ensure_ascii=False) if info else tool_name
        return self._make(
            task_id=task_id,
            role="assistant",
            event_type="chat.tool_result",
            content=result_text or tool_name,
            reasoning_content=self._take_phase_reasoning(),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            success=success if isinstance(success, bool) else None,
            extra={"tool_result": info},
        )

    def _project_error(self, payload: dict[str, Any], task_id: str) -> SubagentMessage:
        summary = _pick_str(payload, "error", "message") or "subagent error"
        return self._make(
            task_id=task_id,
            role="assistant",
            event_type="chat.error",
            content=summary,
            reasoning_content=self._take_phase_reasoning(),
        )


__all__ = ["TranscriptProjector"]
