# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team Event Types Module

This module defines team event type constants for use with Messager.
Events are published via Messager.publish() with topic_id as event type
and session_id as team_id for team isolation.
"""

from enum import Enum
from typing import (
    Any,
    Dict,
    Optional,
    Type,
)

from pydantic import BaseModel, Field


class TeamTopic(str, Enum):
    """Topic categories for team event routing."""

    TEAM = "team"
    TASK = "task"
    MESSAGE = "message"

    def build(self, session_id: str, team_id: str) -> str:
        """Build the final topic string.

        Args:
            session_id: The session identifier.
            team_id: The team identifier.

        Returns:
            Topic string in the format "{session_id}:{team_id}:{topic}".
        """
        return f"session:{session_id}:team:{team_id}:{self.value}"


class TeamEvent:
    """Team event types for cross-process communication

    These events are published via Messager.publish() where:
    - event_type is used as topic_id
    - team_id is used as session_id for team isolation
    """

    # Team lifecycle events
    CREATED = "team_created"
    CLEANED = "team_cleaned"

    # Member lifecycle events
    MEMBER_SPAWNED = "member_spawned"
    MEMBER_RESTARTED = "member_restarted"
    MEMBER_STATUS_CHANGED = "member_status_changed"
    MEMBER_EXECUTION_CHANGED = "member_execution_changed"
    MEMBER_SHUTDOWN = "member_shutdown"
    MEMBER_CANCELED = "member_canceled"

    # Collaboration events
    PLAN_APPROVAL = "plan_approval"

    # Messaging events
    MESSAGE = "message"
    BROADCAST = "broadcast"

    # Task events
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_CLAIMED = "task_claimed"
    TASK_COMPLETED = "task_completed"
    TASK_CANCELLED = "task_cancelled"
    TASK_UNBLOCKED = "task_unblocked"


# ============== Event Message Schemas ==============
# These schemas are used for Messager.publish() messages
class BaseEventMessage(BaseModel):
    """Base class for all team event messages

    All events include team_id for routing and tracking purposes.
    member_id is optional — present on member-scoped events.
    """
    team_id: str = Field(..., description="Team identifier for event routing")
    member_id: Optional[str] = Field(default=None, description="Member identifier, present on member-scoped events")


class TeamCreatedEvent(BaseEventMessage):
    """Event published when a team is created"""
    name: str = Field(..., description="Team name")
    leader_id: str = Field(..., description="Leader member ID")
    created: int = Field(..., description="Creation timestamp")


class TeamCleanedEvent(BaseEventMessage):
    """Event published when a team is cleaned up"""
    pass


class MemberSpawnedEvent(BaseEventMessage):
    """Event published when a team member is spawned"""


class MemberRestartedEvent(BaseEventMessage):
    """Event published when a member process is restarted after failure"""
    reason: str = Field(default="health_check_failure", description="Reason for restart")
    restart_count: int = Field(default=1, description="How many times this member has been restarted")


class MemberStatusChangedEvent(BaseEventMessage):
    """Event published when a member's status changes"""
    old_status: str = Field(..., description="Previous status")
    new_status: str = Field(..., description="New status")


class MemberExecutionChangedEvent(BaseEventMessage):
    """Event published when a member's execution status changes"""
    old_status: str = Field(..., description="Previous execution status")
    new_status: str = Field(..., description="New execution status")


class MemberShutdownEvent(BaseEventMessage):
    """Event published when a member is shut down"""
    force: bool = Field(..., description="Force member shut down")


class MemberCanceledEvent(BaseEventMessage):
    """Event published when a member is canceled"""


class PlanApprovalEvent(BaseEventMessage):
    """Event published when a plan is approved/rejected"""
    approved: bool = Field(..., description="Whether the plan was approved")


class MessageEvent(BaseEventMessage):
    """Event published when a point-to-point message is sent"""
    message_id: str = Field(..., description="Message unique identifier")
    from_member: str = Field(..., description="Sender member ID")
    to_member: str = Field(..., description="Receiver member ID")


class BroadcastEvent(BaseEventMessage):
    """Event published when a broadcast message is sent"""
    message_id: str = Field(..., description="Message unique identifier")
    from_member: str = Field(..., description="Sender member ID")


class TaskCreatedEvent(BaseEventMessage):
    """Event published when a task is created"""
    task_id: str = Field(..., description="Task unique identifier")
    status: str = Field(..., description="Initial task status")


class TaskUpdatedEvent(BaseEventMessage):
    """Event published when a task is updated"""
    task_id: str = Field(..., description="Task unique identifier")


class TaskClaimedEvent(BaseEventMessage):
    """Event published when a task is claimed by a member"""
    task_id: str = Field(..., description="Task unique identifier")


class TaskCompletedEvent(BaseEventMessage):
    """Event published when a task is completed"""
    task_id: str = Field(..., description="Task unique identifier")


class TaskCancelledEvent(BaseEventMessage):
    """Event published when a task is cancelled"""
    task_id: str = Field(..., description="Task unique identifier")


class TaskUnblockedEvent(BaseEventMessage):
    """Event published when a task becomes unblocked"""
    task_id: str = Field(..., description="Task unique identifier")


_EVENT_TYPE_MAP: Dict[str, Type[BaseEventMessage]] = {  # event_type -> model class
    TeamEvent.CREATED: TeamCreatedEvent,
    TeamEvent.CLEANED: TeamCleanedEvent,
    TeamEvent.MEMBER_SPAWNED: MemberSpawnedEvent,
    TeamEvent.MEMBER_RESTARTED: MemberRestartedEvent,
    TeamEvent.MEMBER_STATUS_CHANGED: MemberStatusChangedEvent,
    TeamEvent.MEMBER_EXECUTION_CHANGED: MemberExecutionChangedEvent,
    TeamEvent.MEMBER_SHUTDOWN: MemberShutdownEvent,
    TeamEvent.MEMBER_CANCELED: MemberCanceledEvent,
    TeamEvent.PLAN_APPROVAL: PlanApprovalEvent,
    TeamEvent.MESSAGE: MessageEvent,
    TeamEvent.BROADCAST: BroadcastEvent,
    TeamEvent.TASK_CREATED: TaskCreatedEvent,
    TeamEvent.TASK_UPDATED: TaskUpdatedEvent,
    TeamEvent.TASK_CLAIMED: TaskClaimedEvent,
    TeamEvent.TASK_COMPLETED: TaskCompletedEvent,
    TeamEvent.TASK_CANCELLED: TaskCancelledEvent,
    TeamEvent.TASK_UNBLOCKED: TaskUnblockedEvent,
}

_EVENT_CLASS_MAP: Dict[Type[BaseEventMessage], str] = {  # model class -> event_type
    v: k for k, v in _EVENT_TYPE_MAP.items()
}


class EventMessage(BaseModel):
    """Wrapper that pairs a TeamEvent type with its event payload."""

    event_type: str = Field(..., description="Event type from TeamEvent constants")
    payload: Dict[str, Any] = Field(..., description="Raw event payload data")
    sender_id: str = Field(default="", description="Node ID of the sender, used to filter self-published messages")

    @classmethod
    def from_event(cls, event: BaseEventMessage) -> "EventMessage":
        """Construct an EventMessage from a concrete BaseEventMessage instance.

        Args:
            event: A concrete BaseEventMessage subclass instance.

        Raises:
            ValueError: If the event class is not recognized.
        """
        event_type = _EVENT_CLASS_MAP.get(type(event))
        if event_type is None:
            raise ValueError(f"Unknown event class: {type(event).__name__}")
        return cls(event_type=event_type, payload=event.model_dump())

    def get_payload(self) -> BaseEventMessage:
        """Deserialize payload to the concrete BaseEventMessage subclass based on event_type.

        Returns:
            The typed event message instance.

        Raises:
            ValueError: If event_type is not recognized.
        """
        cls = _EVENT_TYPE_MAP.get(self.event_type)
        if cls is None:
            raise ValueError(f"Unknown event_type: {self.event_type}")
        return cls.model_validate(self.payload)

    def serialize(self) -> bytes:
        """Serialize to UTF-8 encoded JSON bytes."""
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def deserialize(cls, data: bytes) -> "EventMessage":
        """Deserialize from UTF-8 encoded JSON bytes.

        Args:
            data: UTF-8 encoded JSON bytes.
        """
        return cls.model_validate_json(data.decode("utf-8"))

