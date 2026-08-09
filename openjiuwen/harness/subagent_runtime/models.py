# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Subagent runtime domain types: status, ops, metadata, and DTOs."""

from __future__ import annotations

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
    timed_out: bool


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "subagent_id": self.subagent_id,
            "task_id": self.task_id,
            "seq": self.seq,
            "prompt": self.prompt,
            "answer": self.answer,
            "closed_reason": self.closed_reason,
            "created_at_ms": self.created_at_ms,
        }

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
        )


@dataclass(frozen=True)
class SubagentSnapshot:
    """Read-only parent-session view of subagents and qa history."""

    subagents: list[dict[str, Any]]
    turns: list[SubagentTurn]
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
