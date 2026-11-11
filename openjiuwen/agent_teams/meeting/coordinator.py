# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Trusted application boundary over the persisted audio queue."""

import json
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy.exc import SQLAlchemyError

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.group_chat.handler import group_metadata
from openjiuwen.agent_teams.schema.events import EventMessage, MeetingQueueChangedEvent, TeamTopic
from openjiuwen.agent_teams.schema.meeting import (
    MeetingRoute,
    MeetingScope,
    MeetingState,
    MeetingStatus,
    SpeechInterruption,
    SpeechRequest,
    SpeechRequestStatus,
    SpeechSource,
)
from openjiuwen.agent_teams.schema.status import MEMBER_DEPARTED_STATUSES
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.meeting_dao import MeetingDao
from openjiuwen.agent_teams.tools.models import TeamMeetingBase, TeamSpeechRequestBase
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.group_chat.conversation import GroupConversationLog
    from openjiuwen.agent_teams.meeting.member import MeetingMemberClient
    from openjiuwen.agent_teams.messager import Messager
    from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager


def _require_text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


def _publication_ids(scope: MeetingScope, request_id: str, route: MeetingRoute) -> tuple[str, ...]:
    """Stable Team message IDs for publication recovery without another table."""
    if route.kind == "group_chat":
        parts = [scope.team_name, scope.session_id, request_id]
        return (str(uuid5(NAMESPACE_URL, json.dumps(parts, ensure_ascii=False))),)
    recipients = route.recipients if route.kind in {"direct", "multicast"} else (None,)
    return tuple(str(uuid5(NAMESPACE_URL, json.dumps(
        [scope.team_name, scope.session_id, scope.meeting_id, request_id, route.kind, recipient],
        ensure_ascii=False,
    ))) for recipient in recipients)


class MeetingCoordinator:
    """Host-owned admission, observation, and playback controls for one meeting.

    The host derives member identity from its trusted runtime and gives agents
    only a client created by ``for_member``. This object never sends text,
    consumes mailboxes, starts agent execution, or automatically plays audio.
    The caller owns the database, scheduling, and audio-stop acknowledgement.
    """

    def __init__(
        self, database: TeamDatabase, scope: MeetingScope, *,
        messager: "Messager | None" = None,
        message_manager: "TeamMessageManager | None" = None,
        conversation: "GroupConversationLog | None" = None,
        on_state_change: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if message_manager is not None and (
            message_manager.db is not database or message_manager.team_name != scope.team_name
        ):
            raise ValueError("Message manager must belong to the coordinator's database and Team")
        if conversation is not None and (
            conversation.team_name != scope.team_name or conversation.session_id != scope.session_id
        ):
            raise ValueError("Conversation must belong to the coordinator's Team and session")
        self._database = database
        self._scope = scope
        self._messager = messager if messager is not None else (
            message_manager.messager if message_manager is not None else None
        )
        self._message_manager = message_manager
        self._conversation = conversation
        self._on_state_change = on_state_change

    @property
    def scope(self) -> MeetingScope:
        """Explicit session/team/meeting identity; independent of ambient context."""
        return self._scope

    @property
    def queue_topic(self) -> str:
        """Host consumer topic; subscribe before inspecting the pending queue."""
        return TeamTopic.MEETING_QUEUE.build(self.scope.session_id, self.scope.team_name)

    async def notify_queue_changed(self) -> None:
        """Wake host consumers after a committed queue or lifecycle change."""
        if self._messager is None:
            return
        try:
            await self._messager.publish(
                topic_id=self.queue_topic,
                message=EventMessage.from_event(MeetingQueueChangedEvent(
                    team_name=self.scope.team_name, meeting_id=self.scope.meeting_id,
                )),
            )
        except Exception as exc:
            # The database mutation is committed; a failed wake must not invite a duplicate retry.
            team_logger.warning("Meeting queue wake failed for %s: %s", self.scope.meeting_id, exc)

    async def _notify_lifecycle_changed(self) -> None:
        try:
            if self._on_state_change is not None:
                await self._on_state_change()
        finally:
            await self.notify_queue_changed()

    def for_member(self, member_name: str) -> "MeetingMemberClient":
        """Bind a trusted runtime identity; membership is rechecked on every call."""
        from openjiuwen.agent_teams.meeting.member import MeetingMemberClient

        _require_text(member_name, "Member name")
        return MeetingMemberClient(self, member_name)

    async def _dao(self) -> MeetingDao:
        await self._database.initialize()
        return self._database.meeting

    async def _read_message(self, message_id: str):
        token = set_session_id(self.scope.session_id)
        try:
            return await self._database.message.get_message(message_id)
        finally:
            reset_session_id(token)

    async def get_source_message(self, source_message_id: str) -> SpeechSource | None:
        """Read published Team text by ID without acknowledging delivery."""
        _require_text(source_message_id, "Source message ID")
        await self._dao()
        row = await self._read_message(source_message_id)
        if row is None:
            return None
        if row.team_name != self.scope.team_name:
            raise ValueError("Source message belongs to another Team")
        return SpeechSource(
            scope=self.scope, message_id=row.message_id, member_name=row.from_member_name,
            content=row.content, to_member_name=row.to_member_name, broadcast=row.broadcast,
            protocol=row.protocol, timestamp=row.timestamp,
        )

    async def require_member(self, member_name: str) -> str:
        """Return the role of a registered agent that has not departed."""
        _require_text(member_name, "Member name")
        await self._dao()
        identity = await self._database.member.get_member_role_status(member_name, self.scope.team_name)
        if (identity is None or identity[0] not in {"leader", "teammate", "bridge_agent", "external_cli"}
                or identity[1] in MEMBER_DEPARTED_STATUSES):
            raise ValueError("Meeting member must be a registered agent which has not departed")
        return identity[0]

    def _state(self, row: TeamMeetingBase) -> MeetingState:
        return MeetingState(
            scope=self.scope,
            leader_member_name=row.leader_member_name,
            addressed_members=tuple(json.loads(row.addressed_members)),
            owner_request_id=row.owner_request_id,
            lease_token=row.lease_token,
            interruption_id=row.interruption_id,
            revision=row.revision,
            status=MeetingStatus(row.status),
        )

    async def _request(self, row: TeamSpeechRequestBase) -> SpeechRequest:
        route = MeetingRoute(**json.loads(row.routing))
        expected_ids = _publication_ids(self.scope, row.request_id, route)
        source_ids = (
            expected_ids if row.source_message_id == expected_ids[0] else (row.source_message_id,)
            if row.source_message_id is not None else ()
        )
        messages = []
        message_error = None
        for message_id in source_ids:
            try:
                source = await self.get_source_message(message_id)
                if source is None:
                    message_error = "source_message_unavailable"
                else:
                    messages.append(source)
            except (ValueError, RuntimeError, OSError, SQLAlchemyError):
                message_error = "source_message_unavailable"
        first = messages[0] if messages else None
        return SpeechRequest(
            scope=self.scope, request_id=row.request_id, member_name=row.member_name,
            source_message_id=row.source_message_id, speech_text=row.speech_text, sequence=row.sequence,
            status=SpeechRequestStatus(row.status), lease_token=row.lease_token,
            created_at=row.created_at, granted_at=row.granted_at,
            content=first.content if first is not None else row.content,
            mentions=tuple(json.loads(row.mentions)), route=route,
            source_message_ids=source_ids, message=first, messages=tuple(messages),
            message_error=message_error,
            publication_message_ids=expected_ids if row.content != "" else (),
        )

    async def start(self) -> MeetingState:
        """Create once or inspect the existing meeting, including terminal state."""
        dao = await self._dao()
        state = self._state(await dao.create_meeting(self.scope))
        await self._notify_lifecycle_changed()
        return state

    async def enable(self) -> MeetingState:
        """Create or resume this exact meeting ID, preserving any interruption barrier."""
        dao = await self._dao()
        state = self._state(await dao.enable_team_meeting(
            self.scope.session_id, self.scope.team_name, meeting_id=self.scope.meeting_id,
        ))
        await self._notify_lifecycle_changed()
        return state

    async def get_state(self) -> MeetingState | None:
        """Observe persisted state without claiming audio or creating a meeting."""
        dao = await self._dao()
        row = await dao.get_meeting(self.scope)
        return self._state(row) if row is not None else None

    async def enqueue_message(
        self, member_name: str, content: str, *, speech_text: str,
        route: MeetingRoute, mentions: tuple[str, ...] = (), request_id: str | None = None,
    ) -> SpeechRequest:
        """Queue full content for later Team publication and audio playback."""
        _require_text(member_name, "Member name")
        _require_text(speech_text, "Speech text")
        if request_id is not None:
            _require_text(request_id, "Request ID")
        if not isinstance(content, str) or (content and not content.strip()):
            raise ValueError("Content must be text or empty for speech-only requests")
        if not isinstance(route, MeetingRoute) or route.kind not in {"direct", "multicast", "broadcast", "group_chat"}:
            raise ValueError("A supported Meeting route is required")
        if not isinstance(route.protocol, str) or not route.protocol.strip():
            raise ValueError("Route protocol must be nonempty")
        if any(not isinstance(name, str) or not name.strip() for name in route.recipients):
            raise ValueError("Route recipients must be nonempty")
        if len(set(route.recipients)) != len(route.recipients):
            raise ValueError("Route recipients must be distinct")
        recipient_count = len(route.recipients)
        invalid_direct = route.kind == "direct" and recipient_count != 1
        empty_multicast = route.kind == "multicast" and recipient_count == 0
        unexpected_recipients = route.kind in {"broadcast", "group_chat"} and recipient_count != 0
        if invalid_direct or empty_multicast or unexpected_recipients:
            raise ValueError("Route recipients do not match route kind")
        if not isinstance(mentions, tuple) or any(not isinstance(name, str) or not name.strip() for name in mentions):
            raise ValueError("Mentions must be a tuple of nonempty names")
        if len(set(mentions)) != len(mentions) or (mentions and route.kind != "group_chat"):
            raise ValueError("Only group routes support distinct mentions")
        await self.require_member(member_name)
        dao = await self._dao()
        state = await dao.get_meeting(self.scope)
        if state is None or state.closed:
            raise ValueError("Meeting must exist and be open to queue content")
        if request_id is not None:
            existing = await self.get_request(request_id)
            if existing is not None:
                original_content = existing.content
                if original_content is None and existing.source_message_id is not None:
                    source = await self._read_message(existing.source_message_id)
                    original_content = source.content if source is not None else None
                if (existing.member_name, original_content, existing.speech_text,
                        existing.route, existing.mentions) != (member_name, content, speech_text, route, mentions):
                    raise ValueError("Request ID already belongs to different Meeting content")
                return existing
        result = await self._request(await dao.enqueue(
            self.scope, request_id if request_id is not None else uuid4().hex, member_name,
            speech_text=speech_text, content=content, mentions=mentions, route=route,
        ))
        await self.notify_queue_changed()
        return result

    async def get_request(self, request_id: str) -> SpeechRequest | None:
        """Inspect a queued, granted, or terminal request without reading its source text."""
        _require_text(request_id, "Request ID")
        dao = await self._dao()
        row = await dao.get_request(self.scope, request_id)
        return await self._request(row) if row is not None else None

    async def list_pending(self) -> tuple[SpeechRequest, ...]:
        """Inspect the current priority/FIFO order without claiming playback."""
        dao = await self._dao()
        return tuple([await self._request(row) for row in await dao.list_pending(self.scope)])

    async def get_speaker(self) -> SpeechRequest | None:
        """Inspect the current audio owner, including while disabled."""
        dao = await self._dao()
        meeting = await dao.get_meeting(self.scope)
        if meeting is None or meeting.closed or meeting.owner_request_id is None:
            return None
        row = await dao.get_request(self.scope, meeting.owner_request_id)
        if row is None or row.status not in (SpeechRequestStatus.GRANTED, SpeechRequestStatus.PUBLISHING):
            return None
        if row.lease_token is None or row.lease_token != meeting.lease_token:
            return None
        return await self._request(row)

    async def set_addressed_members(self, member_names: Iterable[str]) -> bool:
        """Replace trusted host priority metadata without preempting current audio."""
        if isinstance(member_names, str):
            raise ValueError("Addressed members must be an iterable of member names")
        names = tuple(member_names)
        for name in names:
            _require_text(name, "Addressed member name")
        dao = await self._dao()
        changed = await dao.set_addressed_members(self.scope, names)
        if changed:
            await self.notify_queue_changed()
        return changed

    async def grant_next(self, *, playback_ready: bool = True) -> SpeechRequest | None:
        """Claim one item for text publication, optionally with audio ready."""
        if not isinstance(playback_ready, bool):
            raise ValueError("Playback readiness must be boolean")
        dao = await self._dao()
        row = await dao.grant_next(self.scope, playback_ready=playback_ready)
        return await self._request(row) if row is not None else None

    async def finish_publication(
        self, request_id: str, lease_token: str, *, source_message_ids: tuple[str, ...] = (),
        playback_ready: bool = True,
    ) -> SpeechRequest | None:
        """Record normal Team messages and finish the matching publication claim."""
        _require_text(request_id, "Request ID")
        _require_text(lease_token, "Lease token")
        if not isinstance(playback_ready, bool):
            raise ValueError("Playback readiness must be boolean")
        if (not isinstance(source_message_ids, tuple)
                or any(not isinstance(value, str) or not value.strip() for value in source_message_ids)
                or len(set(source_message_ids)) != len(source_message_ids)):
            raise ValueError("Publication IDs must be distinct nonempty strings")
        request = await self.get_request(request_id)
        if request is None or request.granted_at is None:
            raise ValueError("Publication requires a claimed request")
        if source_message_ids:
            if request.content == "" and request.source_message_id is None:
                raise ValueError("Speech-only requests do not publish a Team message")
            expected = _publication_ids(self.scope, request_id, request.route)
            if source_message_ids != expected:
                raise ValueError("Publication IDs do not match the queued request and route")
            recipients = request.route.recipients if request.route.kind in {"direct", "multicast"} else (None,)
            for message_id, recipient in zip(source_message_ids, recipients):
                source = await self._read_message(message_id)
                if source is None or (source.team_name, source.from_member_name) != (
                    self.scope.team_name, request.member_name,
                ):
                    raise ValueError("Publication source must belong to the requesting member and Team")
                if request.content is not None and source.content != request.content:
                    raise ValueError("Publication source must match queued content")
                if (source.to_member_name, source.broadcast, source.protocol) != (
                    recipient, request.route.kind in {"broadcast", "group_chat"}, request.route.protocol,
                ):
                    raise ValueError("Publication source must match queued route")
                metadata = group_metadata(source)
                if request.route.kind == "group_chat":
                    if not metadata or tuple(metadata.get("mentions", ())) != request.mentions:
                        raise ValueError("Publication source must match queued mentions")
                elif metadata:
                    raise ValueError("Non-group publication cannot carry group metadata")
        elif not request.source_message_ids and request.content != "":
            raise ValueError("Text must be published before finishing the claim")
        dao = await self._dao()
        row = await dao.finish_publication(
            self.scope, request_id, lease_token, source_message_ids=source_message_ids,
            playback_ready=playback_ready,
        )
        if row is None:
            return None
        result = await self._request(row)
        if result.status == SpeechRequestStatus.TEXT_ONLY:
            await self.notify_queue_changed()
        return result

    async def cancel_request(self, request_id: str) -> bool:
        """Cancel a queued request before its text is published or audio starts."""
        _require_text(request_id, "Request ID")
        dao = await self._dao()
        cancelled = await dao.cancel_request(self.scope, request_id)
        if cancelled:
            await self.notify_queue_changed()
        return cancelled

    async def release(self, lease_token: str) -> bool:
        """Acknowledge completed playback for the matching lease; never auto-grant."""
        dao = await self._dao()
        released = await dao.release(self.scope, lease_token)
        if released:
            await self.notify_queue_changed()
        return released

    async def interrupt_speech(self, *, expected_token: str | None = None) -> SpeechInterruption | None:
        """Trusted host interruption; stop audio before acknowledging the returned barrier."""
        dao = await self._dao()
        interruption = await dao.interrupt_speech(self.scope, expected_token=expected_token)
        if interruption is not None:
            await self.notify_queue_changed()
        return interruption

    async def interrupt_from_leader(self, member_name: str, *, expected_token: str) -> SpeechInterruption | None:
        """Authorize a live Leader and interrupt only the observed lease."""
        _require_text(expected_token, "Observed lease token")
        role = await self.require_member(member_name)
        state = await self.get_state()
        if state is None:
            return None
        if role != "leader" or member_name != state.leader_member_name:
            raise ValueError("Only the meeting Leader may interrupt through the agent boundary")
        dao = await self._dao()
        interruption = await dao.interrupt_speech(self.scope, expected_token=expected_token)
        if interruption is not None:
            await self.notify_queue_changed()
        return interruption

    async def resume_after_interruption(self, interruption_id: str) -> bool:
        """Host confirms stopped audio and, for human interruption, admitted input."""
        dao = await self._dao()
        resumed = await dao.resume_after_interruption(self.scope, interruption_id)
        if resumed:
            await self.notify_queue_changed()
        return resumed

    async def force_release(
        self, *, expected_token: str | None = None, expected_interruption_id: str | None = None,
    ) -> bool:
        """Recover only the exact observed lease/barrier; the host must stop abandoned audio."""
        dao = await self._dao()
        released = await dao.force_release(
            self.scope, expected_token=expected_token, expected_interruption_id=expected_interruption_id,
        )
        if released:
            await self.notify_queue_changed()
        return released

    async def disable(self) -> bool:
        """Disable new speech, retaining the current lease, queue, and interruption barrier."""
        dao = await self._dao()
        disabled = await dao.disable(self.scope)
        if disabled:
            await self._notify_lifecycle_changed()
        return disabled

    async def close(self) -> bool:
        """Permanently close audio coordination; the host stops audio and keeps the database."""
        dao = await self._dao()
        closed = await dao.close_meeting(self.scope)
        if closed:
            await self._notify_lifecycle_changed()
        return closed
