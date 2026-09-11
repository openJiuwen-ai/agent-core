# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Subagent runtime domain types: status, ops, metadata, and DTOs."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Union


class SubagentStatusKind(str, Enum):
    """Lifecycle status kinds for a subagent instance."""

    PENDING_INIT = "pending_init"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ERRORED = "errored"
    CLOSED = "closed"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class SubagentStatus:
    """Immutable subagent status snapshot."""

    kind: SubagentStatusKind
    message: str | None = None
    error_code: str | None = None
    close_reason: str | None = None

    @classmethod
    def pending_init(cls) -> SubagentStatus:
        return cls(SubagentStatusKind.PENDING_INIT)

    @classmethod
    def running(cls) -> SubagentStatus:
        return cls(SubagentStatusKind.RUNNING)

    @classmethod
    def completed(cls, message: str | None = None) -> SubagentStatus:
        return cls(SubagentStatusKind.COMPLETED, message=message)

    @classmethod
    def interrupted(cls) -> SubagentStatus:
        return cls(SubagentStatusKind.INTERRUPTED)

    @classmethod
    def errored(cls, message: str, code: str | None = None) -> SubagentStatus:
        return cls(SubagentStatusKind.ERRORED, message=message, error_code=code)

    @classmethod
    def closed(cls, reason: str) -> SubagentStatus:
        return cls(SubagentStatusKind.CLOSED, close_reason=reason)

    @classmethod
    def not_found(cls) -> SubagentStatus:
        return cls(SubagentStatusKind.NOT_FOUND)

    def is_final(self) -> bool:
        return self.kind not in {
            SubagentStatusKind.PENDING_INIT,
            SubagentStatusKind.RUNNING,
            SubagentStatusKind.INTERRUPTED,
        }


@dataclass(frozen=True)
class UserInputOp:
    """Queued user input for the serial worker."""

    query: str
    task_id: str


@dataclass(frozen=True)
class ShutdownOp:
    """Queued shutdown request for the serial worker."""

    reason: str = "manual"


SubagentOp = Union[UserInputOp, ShutdownOp]


@dataclass
class SubagentMetadata:
    """Registry metadata for one live subagent instance."""

    subagent_id: str
    subagent_type: str
    display_name: str
    role: str
    parent_session_id: str
    created_at: float
    last_used_at: float
    current_task_id: str | None = None
    task_description: str = ""
    created_at_ms: float = 0.0
    updated_at_ms: float = 0.0
    closed_at_ms: float | None = None


@dataclass(frozen=True)
class SubagentMetadataBuildParams:
    """Inputs for constructing live subagent registry metadata."""

    subagent_id: str
    subagent_type: str
    task_id: str
    display_name: str
    role: str
    task_description: str

    def to_metadata(self, *, parent_session_id: str) -> SubagentMetadata:
        now_mono = time.monotonic()
        now_ms = time.time() * 1000
        return SubagentMetadata(
            subagent_id=self.subagent_id,
            subagent_type=self.subagent_type,
            display_name=self.display_name,
            role=self.role,
            parent_session_id=parent_session_id,
            created_at=now_mono,
            last_used_at=now_mono,
            current_task_id=self.task_id,
            task_description=self.task_description,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )


def resolve_presentation(
    *,
    subagent_type: str,
    display_name: str | None,
    role: str | None,
    agent_card: Any | None = None,
) -> tuple[str, str]:
    """Normalize display fields before writing SubagentMetadata."""
    card_name = ""
    card_role = ""
    if agent_card is not None:
        card_name = str(getattr(agent_card, "name", "") or "").strip()
        card_desc = str(getattr(agent_card, "description", "") or "").strip()
        if len(card_desc) > 200:
            card_desc = card_desc[:200]
        card_role = card_desc
    return (
        (display_name or "").strip() or card_name or subagent_type,
        (role or "").strip() or card_role,
    )


@dataclass(frozen=True)
class SpawnResult:
    """Control-layer spawn response shape."""

    subagent_id: str
    task_id: str
    status: SubagentStatus


@dataclass(frozen=True)
class WaitResult:
    """Control-layer wait response shape."""

    statuses: dict[str, SubagentStatus]
    results: dict[str, str]
    output_files: dict[str, str]
    timed_out: bool


@dataclass(frozen=True)
class ResumeResult:
    """Control-layer resume response shape."""

    status: SubagentStatus
    restored: bool
    message: str | None = None


@dataclass(frozen=True)
class SubagentRecord:
    """Persistable record for one subagent under a parent session."""

    subagent_id: str
    subagent_type: str
    display_name: str
    role: str
    task_description: str
    created_at_ms: float
    updated_at_ms: float
    closed_at_ms: float | None = None
    closed_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subagent_id": self.subagent_id,
            "subagent_type": self.subagent_type,
            "display_name": self.display_name,
            "role": self.role,
            "task_description": self.task_description,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "closed_at_ms": self.closed_at_ms,
            "closed_reason": self.closed_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SubagentRecord:
        return cls(
            subagent_id=str(raw.get("subagent_id") or ""),
            subagent_type=str(raw.get("subagent_type") or ""),
            display_name=str(raw.get("display_name") or ""),
            role=str(raw.get("role") or ""),
            task_description=str(raw.get("task_description") or ""),
            created_at_ms=float(raw.get("created_at_ms") or 0.0),
            updated_at_ms=float(raw.get("updated_at_ms") or 0.0),
            closed_at_ms=_optional_float(raw.get("closed_at_ms")),
            closed_reason=_optional_str(raw.get("closed_reason")),
        )

    @property
    def is_closed(self) -> bool:
        return self.closed_at_ms is not None


@dataclass(frozen=True)
class SubagentTurn:
    """One subagent turn: parent prompt and final answer."""

    subagent_id: str
    task_id: str
    seq: int
    prompt: str
    answer: str | None
    closed_reason: str | None
    created_at_ms: float
    output_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "subagent_id": self.subagent_id,
            "task_id": self.task_id,
            "seq": self.seq,
            "prompt": self.prompt,
            "answer": self.answer,
            "closed_reason": self.closed_reason,
            "created_at_ms": self.created_at_ms,
        }
        if self.output_file is not None:
            payload["output_file"] = self.output_file
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SubagentTurn:
        return cls(
            subagent_id=str(raw.get("subagent_id") or ""),
            task_id=str(raw.get("task_id") or ""),
            seq=int(raw.get("seq") or 0),
            prompt=str(raw.get("prompt") or ""),
            answer=_optional_str(raw.get("answer")),
            closed_reason=_optional_str(raw.get("closed_reason")),
            created_at_ms=float(raw.get("created_at_ms") or 0.0),
            output_file=_optional_str(raw.get("output_file")),
        )


@dataclass(frozen=True)
class SubagentActivity:
    """One subagent activity milestone for parent-session activity stream."""

    subagent_id: str
    task_id: str
    seq: int
    kind: str
    summary: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    ok: bool | None = None
    at_ms: float = 0.0
    dropped: int | None = None
    phase_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subagent_id": self.subagent_id,
            "task_id": self.task_id,
            "seq": self.seq,
            "kind": self.kind,
            "summary": self.summary,
            "at_ms": self.at_ms,
            "phase_id": self.phase_id,
        }
        if self.tool_name is not None:
            payload["tool_name"] = self.tool_name
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        if self.ok is not None:
            payload["ok"] = self.ok
        if self.dropped is not None:
            payload["dropped"] = self.dropped
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SubagentActivity:
        ok_raw = raw.get("ok")
        ok = ok_raw if isinstance(ok_raw, bool) else None
        dropped_raw = raw.get("dropped")
        dropped = int(dropped_raw) if dropped_raw is not None else None
        return cls(
            subagent_id=str(raw.get("subagent_id") or ""),
            task_id=str(raw.get("task_id") or ""),
            seq=int(raw.get("seq") or 0),
            kind=str(raw.get("kind") or ""),
            summary=str(raw.get("summary") or ""),
            tool_name=_optional_str(raw.get("tool_name")),
            tool_call_id=_optional_str(raw.get("tool_call_id")),
            ok=ok,
            at_ms=float(raw.get("at_ms") or 0.0),
            dropped=dropped,
            phase_id=int(raw.get("phase_id") or 0),
        )

    def is_persistable(self) -> bool:
        return self.kind in {"tool_call", "tool_result", "error"}


@dataclass(frozen=True)
class SubagentMessage:
    """Full-fidelity subagent transcript event for durable history."""

    subagent_id: str
    parent_session_id: str
    task_id: str
    seq: int
    role: str
    event_type: str
    content: str
    reasoning_content: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    success: bool | None = None
    extra: dict[str, Any] | None = None
    at_ms: float = 0.0
    phase_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subagent_id": self.subagent_id,
            "parent_session_id": self.parent_session_id,
            "task_id": self.task_id,
            "seq": self.seq,
            "role": self.role,
            "event_type": self.event_type,
            "content": self.content,
            "at_ms": self.at_ms,
            "phase_id": self.phase_id,
        }
        if self.reasoning_content:
            payload["reasoning_content"] = self.reasoning_content
        if self.tool_name is not None:
            payload["tool_name"] = self.tool_name
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        if self.success is not None:
            payload["success"] = self.success
        if isinstance(self.extra, dict) and self.extra:
            payload["extra"] = self.extra
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SubagentMessage:
        success_raw = raw.get("success")
        success = success_raw if isinstance(success_raw, bool) else None
        extra = raw.get("extra")
        return cls(
            subagent_id=str(raw.get("subagent_id") or ""),
            parent_session_id=str(raw.get("parent_session_id") or ""),
            task_id=str(raw.get("task_id") or ""),
            seq=int(raw.get("seq") or 0),
            role=str(raw.get("role") or "assistant"),
            event_type=str(raw.get("event_type") or ""),
            content=str(raw.get("content") or ""),
            reasoning_content=_optional_str(raw.get("reasoning_content")),
            tool_name=_optional_str(raw.get("tool_name")),
            tool_call_id=_optional_str(raw.get("tool_call_id")),
            success=success,
            extra=extra if isinstance(extra, dict) else None,
            at_ms=float(raw.get("at_ms") or 0.0),
            phase_id=int(raw.get("phase_id") or 0),
        )


@dataclass(frozen=True)
class SubagentSnapshot:
    """Read-only parent-session view of subagents and qa history."""

    subagents: list[dict[str, Any]]
    turns: list[SubagentTurn]
    activities: list[SubagentActivity]
    cursor: str | None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
