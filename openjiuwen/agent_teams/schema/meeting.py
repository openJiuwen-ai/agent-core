# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Value objects for persisted meeting speaking coordination."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error


@dataclass(frozen=True, slots=True)
class MeetingRoute:
    """Destination for the normal Team message sent after queue admission."""

    kind: Literal["direct", "multicast", "broadcast", "group_chat"] = "group_chat"
    recipients: tuple[str, ...] = ()
    protocol: str = "plain"

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipients", tuple(self.recipients))


@dataclass(frozen=True, slots=True)
class MeetingScope:
    """Explicit meeting identity, independent of the current session context."""

    session_id: str
    team_name: str
    meeting_id: str

    def __post_init__(self) -> None:
        if not all((self.session_id.strip(), self.team_name.strip(), self.meeting_id.strip())):
            raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID, reason="Meeting scope identifiers must be non-empty")


class MeetingStatus(StrEnum):
    """Team capability lifecycle; disabling preserves the meeting identity."""

    ACTIVE = "active"
    DISABLED = "disabled"
    CLOSED = "closed"


class SpeechRequestStatus(StrEnum):
    """Lifecycle of queued text publication and audio playback."""

    QUEUED = "queued"
    GRANTED = "granted"
    PUBLISHING = "publishing"
    TEXT_ONLY = "text_only"
    FLUSHING = "flushing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class SpeechInterruption:
    """Persisted barrier identity and the revoked turn to stop in the application."""

    interruption_id: str
    request_id: str | None
    lease_token: str | None


@dataclass(frozen=True, slots=True)
class MeetingState:
    """Detached observation of a meeting; reading it does not claim playback."""

    scope: MeetingScope
    leader_member_name: str
    addressed_members: tuple[str, ...]
    owner_request_id: str | None
    lease_token: str | None
    interruption_id: str | None
    revision: int
    status: MeetingStatus = MeetingStatus.ACTIVE

    @property
    def closed(self) -> bool:
        """Whether the meeting has permanently closed."""
        return self.status == MeetingStatus.CLOSED

    @property
    def enabled(self) -> bool:
        """Whether new speech requests may be played."""
        return self.status == MeetingStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    """One queued message and utterance, linked to Team messages after sending.

    ``publication_message_ids`` reserves IDs for sending queued text; it does
    not imply publication. ``source_message_ids`` links published messages.
    Speech-only requests have no publication IDs.
    """

    scope: MeetingScope
    request_id: str
    member_name: str
    source_message_id: str | None
    speech_text: str
    sequence: int
    status: SpeechRequestStatus
    lease_token: str | None
    created_at: int
    granted_at: int | None
    content: str | None = None
    mentions: tuple[str, ...] = ()
    route: MeetingRoute = MeetingRoute()
    source_message_ids: tuple[str, ...] = ()
    message: "SpeechSource | None" = None
    message_error: str | None = None
    delivery_error: str | None = None
    messages: tuple["SpeechSource", ...] = ()
    publication_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SpeechSource:
    """Read-only projection of a published normal Team message."""

    scope: MeetingScope
    message_id: str
    member_name: str
    content: str
    to_member_name: str | None
    broadcast: bool
    protocol: str
    timestamp: int
