# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Monitor output models decoupled from internal SQLModel and event schemas.

These Pydantic models form the public contract of the monitor module.
Upper-layer services consume these types without depending on database
models or internal event message classes.
"""

from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, Field


class TeamInfo(BaseModel):
    """Team basic information."""

    team_id: str
    name: str
    leader_id: str
    desc: str | None = None
    created: int = Field(description="Creation timestamp in milliseconds")

    @classmethod
    def from_internal(cls, team) -> TeamInfo:
        """Build from internal ``Team`` SQLModel instance.

        Args:
            team: A ``Team`` database row object.
        """
        return cls(
            team_id=team.team_name,
            name=team.display_name,
            leader_id=team.leader_member_name,
            desc=team.desc,
            created=team.created,
        )


class MemberInfo(BaseModel):
    """Team member information."""

    member_id: str
    team_id: str
    name: str
    desc: str | None = None
    status: str = Field(description="MemberStatus value")
    execution_status: str | None = Field(default=None, description="ExecutionStatus value")
    mode: str = Field(description="MemberMode value")

    @classmethod
    def from_internal(cls, member) -> MemberInfo:
        """Build from internal ``TeamMember`` SQLModel instance.

        Args:
            member: A ``TeamMember`` database row object.
        """
        return cls(
            member_id=member.member_name,
            team_id=member.team_name,
            name=member.display_name,
            desc=member.desc,
            status=member.status,
            execution_status=member.execution_status,
            mode=member.mode,
        )


class TaskInfo(BaseModel):
    """Task information."""

    task_id: str
    team_id: str
    title: str
    content: str
    status: str = Field(description="TaskStatus value")
    assignee: str | None = None
    completed_at: int | None = None

    @classmethod
    def from_internal(cls, task) -> TaskInfo:
        """Build from internal ``TeamTaskBase`` SQLModel instance.

        Args:
            task: A ``TeamTaskBase`` database row object.
        """
        return cls(
            task_id=task.task_id,
            team_id=task.team_name,
            title=task.title,
            content=task.content,
            status=task.status,
            assignee=task.assignee,
            completed_at=task.completed_at,
        )


class MessageInfo(BaseModel):
    """Mailbox message information."""

    message_id: str
    team_id: str
    from_member: str
    to_member: str | None = None
    content: str
    timestamp: int
    is_broadcast: bool
    is_read: bool = False

    @classmethod
    def from_internal(cls, msg) -> MessageInfo:
        """Build from internal ``TeamMessageBase`` SQLModel instance.

        Args:
            msg: A ``TeamMessageBase`` database row object.
        """
        return cls(
            message_id=msg.message_id,
            team_id=msg.team_name,
            from_member=msg.from_member_name,
            to_member=msg.to_member_name,
            content=msg.content,
            timestamp=msg.timestamp,
            is_broadcast=msg.broadcast,
            is_read=msg.is_read,
        )


class MonitorEventType(str, Enum):
    """Observable event types exposed by the monitor.

    Only team, member, task, and message events are included.
    Internal events (plan approval, tool approval, worktree,
    workspace lock, etc.) are excluded.
    """

    # Team lifecycle
    TEAM_CREATED = "team_created"
    TEAM_CLEANED = "team_cleaned"
    TEAM_STANDBY = "team_standby"

    # Member lifecycle
    MEMBER_SPAWNED = "member_spawned"
    MEMBER_RESTARTED = "member_restarted"
    MEMBER_STATUS_CHANGED = "member_status_changed"
    MEMBER_EXECUTION_CHANGED = "member_execution_changed"
    MEMBER_SHUTDOWN = "member_shutdown"
    MEMBER_CANCELED = "member_canceled"

    # Task
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_CLAIMED = "task_claimed"
    TASK_COMPLETED = "task_completed"
    TASK_CANCELLED = "task_cancelled"
    TASK_UNBLOCKED = "task_unblocked"

    # Message
    MESSAGE = "message"
    BROADCAST = "broadcast"


_MONITOR_EVENT_VALUES = frozenset(e.value for e in MonitorEventType)


class MonitorEvent(BaseModel):
    """Real-time event emitted by the monitor.

    All payload fields from the four event categories (team, member,
    task, message) are flattened into explicit optional fields.
    Each event type only populates the relevant subset.

    Common fields (always present):
        event_type, team_id, timestamp

    Team event fields:
        TEAM_CREATED: name, leader_id, created

    Member event fields:
        MEMBER_RESTARTED: reason, restart_count
        MEMBER_STATUS_CHANGED / MEMBER_EXECUTION_CHANGED: old_status, new_status
        MEMBER_SHUTDOWN: force

    Task event fields:
        All TASK_*: task_id
        TASK_CREATED: task_id, status

    Message event fields:
        MESSAGE: message_id, from_member, to_member
        BROADCAST: message_id, from_member
    """

    event_type: MonitorEventType
    team_id: str
    member_id: str | None = None
    timestamp: int = Field(description="Monitor receive time in milliseconds")

    # -- Team fields --
    name: str | None = None
    leader_id: str | None = None
    created: int | None = None

    # -- Member fields --
    old_status: str | None = None
    new_status: str | None = None
    reason: str | None = None
    restart_count: int | None = None
    force: bool | None = None

    # -- Task fields --
    task_id: str | None = None
    status: str | None = None

    # -- Message fields --
    message_id: str | None = None
    from_member: str | None = None
    to_member: str | None = None

    @classmethod
    def from_event_message(cls, event_message) -> MonitorEvent | None:
        """Build from internal ``EventMessage``.

        Returns None if the event type is not in MonitorEventType
        (i.e. internal events are silently dropped).

        Args:
            event_message: An ``EventMessage`` instance.
        """
        raw_type = event_message.event_type
        if raw_type not in _MONITOR_EVENT_VALUES:
            return None

        payload = event_message.payload
        return cls(
            event_type=MonitorEventType(raw_type),
            team_id=payload.get("team_name", ""),
            member_id=payload.get("member_name"),
            timestamp=int(round(time.time() * 1000)),
            # Team
            name=payload.get("display_name"),
            leader_id=payload.get("leader_member_name"),
            created=payload.get("created"),
            # Member
            old_status=payload.get("old_status"),
            new_status=payload.get("new_status"),
            reason=payload.get("reason"),
            restart_count=payload.get("restart_count"),
            force=payload.get("force"),
            # Task
            task_id=payload.get("task_id"),
            status=payload.get("status"),
            # Message
            message_id=payload.get("message_id"),
            from_member=payload.get("from_member_name"),
            to_member=payload.get("to_member_name"),
        )
