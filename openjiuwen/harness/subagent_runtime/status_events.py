# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Subagent instance status payloads and parent-session stream emission."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.harness.subagent_runtime.models import SubagentStatus, SubagentStatusKind

SUBAGENT_UPDATED_EVENT_TYPE = "subagent_updated"

_PARENT_ENDED_CLOSE_REASONS = frozenset(
    {
        "parent_ended",
        "stream_cancelled",
        "aborted",
        "rail_uninit",
        "spawn_failed",
        "test_cleanup",
    }
)


def resolve_turn_outcome(status: SubagentStatus) -> str | None:
    """Map internal status to persisted turn outcome (SubagentTurn.closed_reason)."""
    kind = status.kind
    if kind is SubagentStatusKind.COMPLETED:
        return "completed"
    if kind is SubagentStatusKind.INTERRUPTED:
        return "cancelled"
    if kind is SubagentStatusKind.ERRORED:
        return "failed"
    return None


def map_status_to_view(status: SubagentStatus) -> dict[str, Any]:
    """Map internal status to external view fields."""
    kind = status.kind
    if kind in {SubagentStatusKind.PENDING_INIT, SubagentStatusKind.RUNNING}:
        return {
            "status": "running",
            "turn_outcome": None,
            "closed_reason": None,
            "error": None,
        }

    if kind is SubagentStatusKind.COMPLETED:
        return {
            "status": "idle",
            "turn_outcome": "completed",
            "closed_reason": None,
            "error": None,
        }

    if kind is SubagentStatusKind.INTERRUPTED:
        return {
            "status": "idle",
            "turn_outcome": "cancelled",
            "closed_reason": None,
            "error": None,
        }

    if kind is SubagentStatusKind.ERRORED:
        code = status.error_code or "ERROR"
        message = status.message or code
        return {
            "status": "idle",
            "turn_outcome": "failed",
            "closed_reason": None,
            "error": {"code": code, "message": message},
        }

    if kind is SubagentStatusKind.CLOSED:
        return {
            "status": "closed",
            "turn_outcome": None,
            "closed_reason": _normalize_close_reason(status.close_reason or "manual"),
            "error": None,
        }

    if kind is SubagentStatusKind.NOT_FOUND:
        return {
            "status": "closed",
            "turn_outcome": None,
            "closed_reason": "manual",
            "error": None,
        }

    return {
        "status": "running",
        "turn_outcome": None,
        "closed_reason": None,
        "error": None,
    }


def is_turn_finished(status: SubagentStatus) -> bool:
    """Return True when a turn reached a terminal state (archive + emit)."""
    return status.kind in {
        SubagentStatusKind.COMPLETED,
        SubagentStatusKind.INTERRUPTED,
        SubagentStatusKind.ERRORED,
        SubagentStatusKind.CLOSED,
    }


def is_instance_closed(status: SubagentStatus) -> bool:
    """Return True when the subagent instance is shut down (not merely idle)."""
    return status.kind in {
        SubagentStatusKind.CLOSED,
        SubagentStatusKind.NOT_FOUND,
    }


def is_externally_closed(status: SubagentStatus) -> bool:
    """Return True when the subagent instance is shut down (not merely idle)."""
    return is_instance_closed(status)


def _lifecycle_fields(view: dict[str, Any]) -> dict[str, Any]:
    external_status = view["status"]
    if external_status == "closed":
        return {
            "lifecycle": "closed",
            "can_send_input": False,
            "needs_resume": True,
        }
    if external_status == "idle":
        return {
            "lifecycle": "live",
            "can_send_input": True,
            "needs_resume": False,
        }
    return {
        "lifecycle": "live",
        "can_send_input": False,
        "needs_resume": False,
    }


def _normalize_close_reason(reason: str) -> str:
    if reason == "manual":
        return "manual"
    if reason == "evicted":
        return "evicted"
    if reason in _PARENT_ENDED_CLOSE_REASONS:
        return "parent_ended"
    return "parent_ended"


def build_subagent_updated_payload(
    *,
    subagent_id: str,
    subagent_type: str,
    display_name: str,
    role: str,
    parent_session_id: str,
    task_description: str,
    created_at_ms: float,
    updated_at_ms: float,
    closed_at_ms: float | None,
    status: SubagentStatus,
    revision: int,
) -> dict[str, Any]:
    """Build the external subagent status payload."""
    view = map_status_to_view(status)
    lifecycle = _lifecycle_fields(view)
    return {
        "subagent_id": subagent_id,
        "sub_session_id": subagent_id,
        "parent_session_id": parent_session_id,
        "subagent_type": subagent_type,
        "display_name": display_name,
        "role": role,
        "task_description": task_description,
        "status": view["status"],
        "turn_outcome": view["turn_outcome"],
        "closed_at": closed_at_ms if view["status"] == "closed" else None,
        "closed_reason": view["closed_reason"],
        "error": view["error"],
        "created_at": created_at_ms,
        "updated_at": updated_at_ms,
        "revision": revision,
        **lifecycle,
    }


async def emit_subagent_updated(
    session: Session,
    *,
    projection: dict[str, Any],
) -> None:
    """Write one subagent status update to the parent session stream."""
    try:
        await session.write_stream(
            OutputSchema(
                type=SUBAGENT_UPDATED_EVENT_TYPE,
                index=0,
                payload={"subagent_updated": projection},
            )
        )
    except Exception as exc:
        logger.warning("[subagent_events] emit failed: %s", exc)


__all__ = [
    "SUBAGENT_UPDATED_EVENT_TYPE",
    "build_subagent_updated_payload",
    "emit_subagent_updated",
    "is_externally_closed",
    "is_instance_closed",
    "is_turn_finished",
    "map_status_to_view",
    "resolve_turn_outcome",
]
