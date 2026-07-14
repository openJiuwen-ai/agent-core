# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured payloads and result types for ``interact_team``.

The runtime exposes three interaction perspectives:

* **God view** — speak directly to the team's leader DeepAgent. Equivalent
  to the historical ``invoke``/``deliver_to_leader`` channel.
* **Operator view** — speak as the external user, addressing one member
  with ``@member_name`` semantics or the whole team via broadcast.
* **Direct-control view** — speak as a registered human-agent team
  member. Routed through ``HumanAgentInbox`` and gated by HITT.

Each interact call carries one concrete payload. ``DeliverResult``
captures the outcome uniformly so callers do not need to special-case
return types per channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Any,
    Optional,
    Union,
)

from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic


_EXTERNAL_TEAM_EVENT_TYPE = "team.external_event"


@dataclass(frozen=True, slots=True)
class GodViewMessage:
    """Speak directly to the team's leader DeepAgent."""

    body: str


@dataclass(frozen=True, slots=True)
class OperatorMessage:
    """Speak as the external user.

    Attributes:
        body: Message content.
        target: Member name to address; ``None`` broadcasts to the team.
    """

    body: str
    target: Optional[str] = None


@dataclass(frozen=True, slots=True)
class HumanAgentMessage:
    """Speak as a registered human-agent team member.

    Attributes:
        body: Message content.
        sender: Member name of the human-agent speaking.
        target: Target member; ``None`` broadcasts to the team.
    """

    body: str
    sender: str
    target: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ExternalTeamEvent:
    """A standard team event received through the external interact channel."""

    topic: TeamTopic
    event: EventMessage

    def to_wire(self) -> dict[str, Any]:
        """Return the JSON-safe payload carried by a normal ``chat.send`` request."""
        return {
            "type": _EXTERNAL_TEAM_EVENT_TYPE,
            "topic": self.topic.value,
            "event": self.event.model_dump(mode="json"),
        }

    @classmethod
    def from_wire(cls, payload: object) -> "ExternalTeamEvent | None":
        """Parse this payload shape, or return ``None`` for other interact inputs."""
        if not isinstance(payload, dict) or payload.get("type") != _EXTERNAL_TEAM_EVENT_TYPE:
            return None
        topic = payload.get("topic")
        event = payload.get("event")
        if not isinstance(topic, str) or not isinstance(event, dict):
            raise ValueError("malformed external team event")
        return cls(topic=TeamTopic(topic), event=EventMessage.model_validate(event))


InteractPayload = Union[GodViewMessage, OperatorMessage, HumanAgentMessage, ExternalTeamEvent]
"""Discriminated union of supported interact payload shapes."""


@dataclass(frozen=True, slots=True)
class HumanAgentInboundEvent:
    """Notification that a team-side message reached a human agent.

    Phase-2 HITT does not let a human agent's LLM autonomously consume
    incoming messages — they are passed straight through to the
    corresponding external user. This dataclass is what the runtime
    feeds to the ``on_inbound`` callback registered with
    ``HumanAgentInbox`` so the SDK / business layer can deliver the
    message wherever the user is.

    Attributes:
        member_name: The human-agent member that received the message.
        sender: The team member that sent the message (or the literal
            ``"user"`` pseudo-member for user-side broadcasts).
        body: Message content.
        broadcast: ``True`` when the message arrived via broadcast.
        message_id: Identifier persisted on the message bus, useful for
            deduplication and read-state correlation.
        timestamp: Millisecond wall-clock timestamp when the message
            row was created.
    """

    member_name: str
    sender: str
    body: str
    broadcast: bool
    message_id: str
    timestamp: int


@dataclass(frozen=True, slots=True)
class DeliverResult:
    """Outcome of a single payload delivery.

    On success ``ok`` is True and ``message_id`` carries the assigned
    message id (``None`` for channels that do not produce a message,
    e.g. ``deliver_to_leader``). On failure ``ok`` is False and ``reason``
    carries a short stable token suitable for surfacing to the caller
    (``human_agent_not_enabled``, ``unknown_human_agent``, ``send_failed``,
    ...).
    """

    ok: bool
    message_id: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def success(cls, message_id: Optional[str] = None) -> "DeliverResult":
        """Build a success result, optionally carrying a message id."""
        return cls(ok=True, message_id=message_id)

    @classmethod
    def failure(cls, reason: str) -> "DeliverResult":
        """Build a failure result with a short reason token."""
        return cls(ok=False, reason=reason)

    def __bool__(self) -> bool:
        return self.ok


__all__ = [
    "DeliverResult",
    "ExternalTeamEvent",
    "GodViewMessage",
    "HumanAgentInboundEvent",
    "HumanAgentMessage",
    "InteractPayload",
    "OperatorMessage",
]
