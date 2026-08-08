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


def map_status_to_view(status: SubagentStatus) -> dict[str, Any]:
    """Map internal status to external view fields (status / closed_reason / error)."""
    kind = status.kind
    if kind in {SubagentStatusKind.PENDING_INIT, SubagentStatusKind.RUNNING}:
        return {"status": "running", "closed_reason": None, "error": None}

    if kind is SubagentStatusKind.COMPLETED:
        return {"status": "closed", "closed_reason": "completed", "error": None}

    if kind is SubagentStatusKind.INTERRUPTED:
        return {"status": "closed", "closed_reason": "cancelled", "error": None}

    if kind is SubagentStatusKind.ERRORED:
        code = status.error_code or "ERROR"
        message = status.message or code
        return {
            "status": "closed",
            "closed_reason": "failed",
            "error": {"code": code, "message": message},
        }

    if kind is SubagentStatusKind.CLOSED:
        return {
            "status": "closed",
            "closed_reason": _normalize_close_reason(status.close_reason or "manual"),
            "error": None,
        }

    if kind is SubagentStatusKind.NOT_FOUND:
        return {"status": "closed", "closed_reason": "manual", "error": None}

    return {"status": "running", "closed_reason": None, "error": None}


def is_externally_closed(status: SubagentStatus) -> bool:
    """Return True when the external view status is closed."""
    return map_status_to_view(status)["status"] == "closed"


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
    return {
        "subagent_id": subagent_id,
        "sub_session_id": subagent_id,
        "parent_session_id": parent_session_id,
        "subagent_type": subagent_type,
        "display_name": display_name,
        "role": role,
        "task_description": task_description,
        "status": view["status"],
        "closed_at": closed_at_ms,
        "closed_reason": view["closed_reason"],
        "error": view["error"],
        "created_at": created_at_ms,
        "updated_at": updated_at_ms,
        "revision": revision,
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
    "map_status_to_view",
]
