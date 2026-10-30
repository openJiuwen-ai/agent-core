# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared builder for the structured ``AGENT_ACTIVITY`` progress event.

The worker rail (``team_worker_backend``), the session avatar rail
(``avatar_session_backend``) and the external harness runtime
(``external.member_runtime``) all emit the same ``WorkflowProgressEvent`` with
the same content cap; this module keeps the cap and the event shape in
exactly one place so a fourth emitter cannot drift.
"""

from __future__ import annotations

from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind, WorkflowProgressEvent

#: The one engine-side cap for tool-activity ``content``. Arguments and
#: results pass through in full — the member call list shows the same complete
#: payload a real teammate's tool_call frame carries — with this bound only as
#: a guard against pathological payloads (a multi-MB command output) exploding
#: the event, the checkpointed activity rows and the broadcast. A cut preview
#: keeps the marker so it reads as "I stopped early", not as a complete text.
ACTIVITY_CONTENT_LIMIT = 65536
ACTIVITY_TRUNCATED_MARKER = "…(truncated)"


def truncate_activity_content(content: str | None) -> str | None:
    """Cap an activity preview at the engine limit; mark what was cut."""
    if content is None:
        return None
    if len(content) <= ACTIVITY_CONTENT_LIMIT:
        return content
    return content[:ACTIVITY_CONTENT_LIMIT] + ACTIVITY_TRUNCATED_MARKER


def build_activity_event(
    *,
    activity_type: str,
    tool_call_id: str | None,
    tool_name: str | None,
    content: str | None,
    agent_id: str | None = None,
    phase: str | None = None,
    label: str | None = None,
    correlation_id: str | None = None,
) -> WorkflowProgressEvent:
    """Build one structured ``AGENT_ACTIVITY`` event from a tool pair half.

    ``content`` is truncated here — the one engine-side cap every consumer can
    rely on; ``message`` stays for legacy consumers.
    """
    return WorkflowProgressEvent(
        kind=ProgressKind.AGENT_ACTIVITY,
        phase=phase,
        label=label,
        agent_id=agent_id,
        correlation_id=correlation_id,
        activity_type=activity_type,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        content=truncate_activity_content(content),
        message=f"tool: {tool_name}",
    )


__all__ = [
    "ACTIVITY_CONTENT_LIMIT",
    "ACTIVITY_TRUNCATED_MARKER",
    "build_activity_event",
    "truncate_activity_content",
]
