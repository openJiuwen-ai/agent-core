# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organization-level topics and event payloads."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OrgTopic(str, Enum):
    """Topic categories for organization-level pub/sub."""

    ORG = "org"
    TASK = "task"
    LEADER = "leader"
    TEAM_INBOX = "team_inbox"

    def build(self, session_id: str, organization_id: str, team_id: str | None = None) -> str:
        base = f"session:{session_id}:org:{organization_id}:{self.value}"
        if self is OrgTopic.TEAM_INBOX:
            return f"{base}:{team_id or ''}"
        return base


class OrgEvent:
    """Organization-level event type constants."""

    BROADCAST = "org_broadcast"
    TASK_CREATED = "org_task_created"
    TASK_CLAIMED = "org_task_claimed"
    TASK_DELEGATED = "org_task_delegated"
    TASK_COMPLETED = "org_task_completed"
    LEADER_MESSAGE = "org_leader_message"


class BaseOrgEvent(BaseModel):
    """Base payload for organization-level events."""

    organization_id: str
    team_id: str | None = None
    leader_id: str | None = None


class OrgBroadcastEvent(BaseOrgEvent):
    """Event used to wake all leaders without embedding large content."""

    event_id: str


class OrgTaskCreatedEvent(BaseOrgEvent):
    """Published after an org task row is created."""

    task_id: str
    parent_task_id: str | None = None
    root_task_id: str


class OrgTaskClaimedEvent(BaseOrgEvent):
    """Published after an open task is claimed by one leader."""

    task_id: str
    claimed_by_team_id: str
    claimed_by_leader_id: str


class OrgTaskDelegatedEvent(BaseOrgEvent):
    """Published after a task is delegated to another team leader."""

    task_id: str
    delegated_by_team_id: str
    delegated_to_team_id: str
    delegated_to_leader_id: str | None = None


class OrgTaskCompletedEvent(BaseOrgEvent):
    """Published after a task reaches COMPLETED.

    The result body stays in the DB. Consumers should fetch by ``task_id``.
    """

    task_id: str


class OrgLeaderMessageEvent(BaseOrgEvent):
    """Published after a leader-to-leader message row is persisted."""

    message_id: str
    from_team_id: str
    to_team_id: str | None = None


_EVENT_TYPE_MAP: dict[str, type[BaseOrgEvent]] = {
    OrgEvent.BROADCAST: OrgBroadcastEvent,
    OrgEvent.TASK_CREATED: OrgTaskCreatedEvent,
    OrgEvent.TASK_CLAIMED: OrgTaskClaimedEvent,
    OrgEvent.TASK_DELEGATED: OrgTaskDelegatedEvent,
    OrgEvent.TASK_COMPLETED: OrgTaskCompletedEvent,
    OrgEvent.LEADER_MESSAGE: OrgLeaderMessageEvent,
}
_EVENT_CLASS_MAP: dict[type[BaseOrgEvent], str] = {v: k for k, v in _EVENT_TYPE_MAP.items()}


class OrgEventMessage(BaseModel):
    """Transport wrapper matching the team EventMessage shape."""

    event_type: str = Field(..., description="Event type from OrgEvent constants")
    payload: dict[str, Any] = Field(..., description="Raw event payload data")
    sender_id: str = Field(default="", description="Node ID of the sender")

    @classmethod
    def from_event(cls, event: BaseOrgEvent) -> "OrgEventMessage":
        event_type = _EVENT_CLASS_MAP.get(type(event))
        if event_type is None:
            raise ValueError(f"Unknown org event class: {type(event).__name__}")
        return cls(event_type=event_type, payload=event.model_dump())

    def get_payload(self) -> BaseOrgEvent:
        event_cls = _EVENT_TYPE_MAP.get(self.event_type)
        if event_cls is None:
            raise ValueError(f"Unknown org event_type: {self.event_type}")
        return event_cls.model_validate(self.payload)


__all__ = [
    "BaseOrgEvent",
    "OrgBroadcastEvent",
    "OrgEvent",
    "OrgEventMessage",
    "OrgLeaderMessageEvent",
    "OrgTaskClaimedEvent",
    "OrgTaskCompletedEvent",
    "OrgTaskCreatedEvent",
    "OrgTaskDelegatedEvent",
    "OrgTopic",
]
