# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Database authority for audio queue admission, ownership, and recovery.

Mutations lock the meeting row before reading it; creation locks the owning
Team row. These database locks also serialize independent member processes.
Callers are trusted runtime code; agent tools must not expose this DAO directly.
"""

import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import TypeVar
from uuid import uuid4

from sqlalchemy import case, inspect, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from openjiuwen.agent_teams.schema.meeting import (
    MeetingRoute,
    MeetingScope,
    MeetingStatus,
    SpeechInterruption,
    SpeechRequestStatus,
)
from openjiuwen.agent_teams.tools.database.engine import DbSessions, create_meeting_tables, get_current_time
from openjiuwen.agent_teams.tools.models import (
    Team,
    TeamMeetingBase,
    TeamMember,
    TeamSpeechRequestBase,
    _get_meeting_model,
    _get_message_model,
    _get_speech_request_model,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

_MeetingRow = TypeVar("_MeetingRow", TeamMeetingBase, TeamSpeechRequestBase)


class _MeetingNotFoundError(ValueError):
    """An explicit scope no longer has persisted meeting state."""


def _scope_filter(model: type[_MeetingRow], scope: MeetingScope) -> tuple:
    return model.team_name == scope.team_name, model.meeting_id == scope.meeting_id


def _scope_values(scope: MeetingScope) -> dict[str, str]:
    return {"team_name": scope.team_name, "meeting_id": scope.meeting_id}


class MeetingDao:
    """Short atomic queue operations without model execution or audio playback."""

    def __init__(self, sessions: DbSessions) -> None:
        self._sessions = sessions

    @staticmethod
    async def _table_exists(session: AsyncSession, model: type[_MeetingRow]) -> bool:
        conn = await session.connection()
        return await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table(model.__tablename__))

    @asynccontextmanager
    async def _locked(self, scope: MeetingScope) -> AsyncIterator[tuple[AsyncSession, TeamMeetingBase]]:
        model = _get_meeting_model(scope.session_id)
        try:
            async with self._sessions.write() as session:
                # Write first to avoid upgrading a stale SQLite read snapshot.
                # A lock alone does not change the externally observed revision.
                result = await session.execute(
                    update(model).where(*_scope_filter(model, scope)).values(revision=model.revision)
                )
                if result.rowcount != 1:
                    raise _MeetingNotFoundError("Meeting does not exist")
                meeting = await session.get(model, _scope_values(scope))
                yield session, meeting
                await session.commit()
        except DBAPIError as exc:
            # Inspect after rollback: late calls after session release must not
            # recreate tables, and unrelated database failures must still surface.
            async with self._sessions.read() as session:
                if not await self._table_exists(session, model):
                    raise _MeetingNotFoundError("Meeting does not exist") from exc
            raise

    async def _get_row(self, model: type[_MeetingRow], key: dict[str, str]) -> _MeetingRow | None:
        try:
            async with self._sessions.read() as session:
                return await session.get(model, key)
        except DBAPIError:
            async with self._sessions.read() as session:
                if not await self._table_exists(session, model):
                    return None
            raise

    @staticmethod
    async def _find_team_meeting(
        session: AsyncSession, model: type[TeamMeetingBase], team_name: str, *, lock: bool = False,
    ) -> TeamMeetingBase | None:
        query = select(model).where(model.team_name == team_name, model.status != MeetingStatus.CLOSED).limit(2)
        if lock:
            query = query.with_for_update()
        rows = list(await session.scalars(query))
        if len(rows) > 1:
            raise_error(
                StatusCode.AGENT_TEAM_STATE_INVALID,
                reason="Team has multiple non-closed meetings; close the obsolete instances explicitly",
            )
        return rows[0] if rows else None

    async def create_meeting(self, scope: MeetingScope) -> TeamMeetingBase:
        """Create once, allowing one non-closed meeting per Team/session.

        Retrying an existing ID returns its persisted state without resuming a
        disabled or closed meeting. The leader identity comes from the Team.
        """
        return await self._create_or_enable(scope, enable=False, reuse_current=False)

    async def _create_or_enable(
        self, scope: MeetingScope, *, enable: bool, reuse_current: bool,
    ) -> TeamMeetingBase:
        await create_meeting_tables(self._sessions, scope.session_id)
        model = _get_meeting_model(scope.session_id)
        async with self._sessions.write() as session:
            locked = await session.execute(
                update(Team).where(Team.team_name == scope.team_name).values(team_name=Team.team_name)
            )
            if locked.rowcount != 1:
                raise_error(StatusCode.AGENT_TEAM_STATE_INVALID, reason="Meeting requires an existing Team")
            current = await self._find_team_meeting(session, model, scope.team_name, lock=True)
            row = current if reuse_current and current is not None else await session.get(
                model, _scope_values(scope), with_for_update=True,
            )
            if row is None:
                if current is not None:
                    raise_error(StatusCode.AGENT_TEAM_STATE_INVALID, reason="Team already has a non-closed meeting")
                leader = await session.scalar(
                    select(Team.leader_member_name).where(Team.team_name == scope.team_name)
                )
                row = model(**_scope_values(scope), leader_member_name=leader)
                session.add(row)
            elif enable:
                if row.status == MeetingStatus.CLOSED:
                    raise_error(StatusCode.AGENT_TEAM_STATE_INVALID, reason="Closed meeting IDs cannot be re-enabled")
                if row.status == MeetingStatus.DISABLED:
                    row.status = MeetingStatus.ACTIVE
                    row.revision += 1
            await session.commit()
            return row

    async def enable_team_meeting(
        self, session_id: str, team_name: str, *, meeting_id: str | None = None,
    ) -> TeamMeetingBase:
        """Create or resume the Team/session's non-closed meeting, preserving barriers."""
        scope = MeetingScope(session_id, team_name, meeting_id if meeting_id is not None else uuid4().hex)
        return await self._create_or_enable(scope, enable=True, reuse_current=meeting_id is None)

    async def get_meeting(self, scope: MeetingScope) -> TeamMeetingBase | None:
        """Inspect a meeting without acquiring ownership or creating tables."""
        model = _get_meeting_model(scope.session_id)
        return await self._get_row(model, _scope_values(scope))

    async def get_team_meeting(self, session_id: str, team_name: str) -> TeamMeetingBase | None:
        """Inspect the single active or disabled meeting; reject ambiguous rows."""
        model = _get_meeting_model(session_id)
        try:
            async with self._sessions.read() as session:
                return await self._find_team_meeting(session, model, team_name)
        except DBAPIError:
            async with self._sessions.read() as session:
                if not await self._table_exists(session, model):
                    return None
            raise

    async def enqueue(
        self,
        scope: MeetingScope,
        request_id: str,
        member_name: str,
        *,
        speech_text: str,
        content: str | None = None,
        mentions: Iterable[str] = (),
        route: MeetingRoute | None = None,
        source_message_id: str | None = None,
    ) -> TeamSpeechRequestBase:
        """Admit pending text and speech without requiring a sent message.

        Identical request-ID retries return the stored request in any status;
        conflicting retries fail. Open disabled meetings still admit text.
        Database constraints reject invalid or duplicate non-null message references.
        """
        if not request_id.strip():
            raise ValueError("Request ID must be non-empty")
        if not speech_text.strip():
            raise ValueError("Speech text must be non-empty")
        if source_message_id is not None and not source_message_id.strip():
            raise ValueError("Source message ID must be non-empty")
        if source_message_id is None:
            if not isinstance(content, str) or (content and not content.strip()):
                raise ValueError("Queued content must be text or empty for speech-only requests")
        route = route or MeetingRoute()
        mentions = tuple(mentions)
        encoded_mentions = json.dumps(mentions)
        encoded_route = json.dumps(asdict(route))
        model = _get_speech_request_model(scope.session_id)
        async with self._locked(scope) as (session, meeting):
            key = {**_scope_values(scope), "request_id": request_id}
            existing = await session.get(model, key)
            if existing is not None:
                if existing.content is None and existing.source_message_id is not None:
                    source = await session.get(_get_message_model(scope.session_id), existing.source_message_id)
                    if (source is not None and source.content == content and
                            (existing.member_name, existing.speech_text,
                             existing.mentions, existing.routing) == (
                                member_name, speech_text, encoded_mentions, encoded_route,
                            )):
                        return existing
                if (existing.member_name, existing.speech_text, existing.source_message_id,
                        existing.content, existing.mentions, existing.routing) != (
                    member_name, speech_text, source_message_id, content, encoded_mentions, encoded_route,
                ):
                    raise ValueError("Request ID already belongs to a different speech request")
                return existing
            if meeting.status == MeetingStatus.CLOSED:
                raise_error(StatusCode.AGENT_TEAM_STATE_INVALID, reason="Meeting must be open to queue speech")
            role = await session.scalar(
                select(TeamMember.role).where(
                    TeamMember.team_name == scope.team_name, TeamMember.member_name == member_name,
                )
            )
            if role not in {"leader", "teammate", "bridge_agent", "external_cli"}:
                raise ValueError("Only registered agent members may enter the speaking queue")
            meeting.next_sequence += 1
            meeting.revision += 1
            request = model(
                **key,
                member_name=member_name,
                source_message_id=source_message_id,
                content=content,
                speech_text=speech_text,
                mentions=encoded_mentions,
                routing=encoded_route,
                sequence=meeting.next_sequence,
                created_at=get_current_time(),
            )
            session.add(request)
            return request

    async def set_addressed_members(self, scope: MeetingScope, member_names: Iterable[str]) -> bool:
        """Replace trusted host metadata without changing existing FIFO positions."""
        addressed = json.dumps(sorted(set(member_names)))
        async with self._locked(scope) as (_, meeting):
            if meeting.status != MeetingStatus.ACTIVE:
                return False
            if meeting.addressed_members != addressed:
                meeting.addressed_members = addressed
                meeting.revision += 1
            return True

    @staticmethod
    def _pending_query(scope: MeetingScope, meeting: TeamMeetingBase):
        model = _get_speech_request_model(scope.session_id)
        priority = case(
            (model.member_name == meeting.leader_member_name, 0),
            (model.member_name.in_(json.loads(meeting.addressed_members)), 1),
            else_=2,
        )
        return (
            select(model)
            .where(*_scope_filter(model, scope), model.status == SpeechRequestStatus.QUEUED)
            .order_by(priority, model.sequence)
        )

    async def list_pending(self, scope: MeetingScope) -> list[TeamSpeechRequestBase]:
        """Inspect leader/addressed/other priority, then FIFO, in one transaction."""
        async with self._locked(scope) as (session, meeting):
            return list(await session.scalars(self._pending_query(scope, meeting)))

    async def get_request(self, scope: MeetingScope, request_id: str) -> TeamSpeechRequestBase | None:
        """Inspect a request in any status without claiming or changing it."""
        model = _get_speech_request_model(scope.session_id)
        return await self._get_row(model, {**_scope_values(scope), "request_id": request_id})

    async def grant_next(self, scope: MeetingScope, *, playback_ready: bool = True) -> TeamSpeechRequestBase | None:
        """Claim the highest-ranked request for publication and optional audio."""
        async with self._locked(scope) as (session, meeting):
            if meeting.status == MeetingStatus.CLOSED or meeting.interruption_id is not None:
                return None
            if meeting.owner_request_id is not None or meeting.lease_token is not None:
                return None
            request = await session.scalar(self._pending_query(scope, meeting).limit(1))
            if request is None:
                return None
            request.status = (
                SpeechRequestStatus.GRANTED if playback_ready and meeting.status == MeetingStatus.ACTIVE
                else SpeechRequestStatus.PUBLISHING
            )
            request.lease_token = uuid4().hex
            request.granted_at = get_current_time()
            meeting.owner_request_id = request.request_id
            meeting.lease_token = request.lease_token
            meeting.revision += 1
            return request

    async def finish_publication(
        self, scope: MeetingScope, request_id: str, lease_token: str, *,
        source_message_ids: tuple[str, ...] = (), playback_ready: bool = True,
    ) -> TeamSpeechRequestBase | None:
        """Link sent text, then finish only the matching current claim."""
        try:
            async with self._locked(scope) as (session, meeting):
                model = _get_speech_request_model(scope.session_id)
                request = await session.get(model, {**_scope_values(scope), "request_id": request_id})
                if request is None:
                    return None
                if request.granted_at is None:
                    raise ValueError("Publication requires a previously claimed request")
                if source_message_ids:
                    first_id = source_message_ids[0]
                    if request.source_message_id is not None and request.source_message_id != first_id:
                        raise ValueError("Request already has a different source message")
                    request.source_message_id = first_id
                    request.content = None
                if not lease_token or meeting.closed or meeting.lease_token != lease_token:
                    return None
                if meeting.owner_request_id != request_id or request.lease_token != lease_token:
                    return None
                if request.source_message_id is None and request.content != "":
                    raise ValueError("Cannot finish publication before text is recorded")
                if (request.status == SpeechRequestStatus.PUBLISHING
                        or meeting.status == MeetingStatus.DISABLED or not playback_ready):
                    await self._release(session, scope, meeting, SpeechRequestStatus.TEXT_ONLY)
                    request.status = SpeechRequestStatus.TEXT_ONLY
                    request.lease_token = None
                    meeting.revision += 1
                return request
        except _MeetingNotFoundError:
            return None

    async def cancel_request(self, scope: MeetingScope, request_id: str) -> bool:
        """Cancel only queued speech; an existing audio lease requires interruption."""
        model = _get_speech_request_model(scope.session_id)
        try:
            async with self._locked(scope) as (session, meeting):
                result = await session.execute(
                    update(model).where(
                        *_scope_filter(model, scope), model.request_id == request_id,
                        model.status == SpeechRequestStatus.QUEUED,
                    ).values(status=SpeechRequestStatus.CANCELLED)
                )
                if result.rowcount != 1:
                    return False
                meeting.revision += 1
                return True
        except _MeetingNotFoundError:
            return False

    @staticmethod
    async def _get_owner(
        session: AsyncSession, scope: MeetingScope, meeting: TeamMeetingBase,
    ) -> TeamSpeechRequestBase | None:
        if meeting.owner_request_id is None or meeting.lease_token is None:
            return None
        model = _get_speech_request_model(scope.session_id)
        request = await session.get(model, {**_scope_values(scope), "request_id": meeting.owner_request_id})
        if (request is None or request.status not in (SpeechRequestStatus.GRANTED, SpeechRequestStatus.PUBLISHING)
                or request.lease_token != meeting.lease_token):
            return None
        return request

    @staticmethod
    async def _release(
        session: AsyncSession, scope: MeetingScope, meeting: TeamMeetingBase, status: SpeechRequestStatus,
    ) -> None:
        model = _get_speech_request_model(scope.session_id)
        if meeting.owner_request_id is not None:
            await session.execute(
                update(model).where(
                    *_scope_filter(model, scope), model.request_id == meeting.owner_request_id,
                    model.status.in_((SpeechRequestStatus.GRANTED, SpeechRequestStatus.PUBLISHING)),
                    model.lease_token == meeting.lease_token,
                ).values(status=status)
            )
        # Retain the request's token/timestamp for inspection of the ended lease.
        meeting.owner_request_id = None
        meeting.lease_token = None

    async def release(self, scope: MeetingScope, lease_token: str) -> bool:
        """Complete only the matching current grant after the host acknowledges playback."""
        try:
            async with self._locked(scope) as (session, meeting):
                if not lease_token or meeting.closed or meeting.lease_token != lease_token:
                    return False
                if meeting.interruption_id is not None:
                    return False
                owner = await self._get_owner(session, scope, meeting)
                if owner is None or owner.status != SpeechRequestStatus.GRANTED:
                    return False
                if owner.source_message_id is None and owner.content != "":
                    return False
                await self._release(session, scope, meeting, SpeechRequestStatus.COMPLETED)
                meeting.revision += 1
                return True
        except _MeetingNotFoundError:
            return False

    async def interrupt_speech(
        self, scope: MeetingScope, *, expected_token: str | None = None,
    ) -> SpeechInterruption | None:
        """Revoke speech and block grants until audio-stop/input acknowledgement.

        The trusted host may also open an idle barrier. An optional observed
        token prevents a delayed interruption from revoking a successor lease.
        This operation does not interrupt agent execution or deliver text.
        """
        try:
            async with self._locked(scope) as (session, meeting):
                if meeting.closed or meeting.interruption_id is not None:
                    return None
                if expected_token is not None and (not expected_token or meeting.lease_token != expected_token):
                    return None
                interruption = SpeechInterruption(uuid4().hex, meeting.owner_request_id, meeting.lease_token)
                await self._release(session, scope, meeting, SpeechRequestStatus.INTERRUPTED)
                meeting.interruption_id = interruption.interruption_id
                meeting.revision += 1
                return interruption
        except _MeetingNotFoundError:
            return None

    async def resume_after_interruption(self, scope: MeetingScope, interruption_id: str) -> bool:
        """Acknowledge stopped audio/admitted input for this exact persisted barrier."""
        try:
            async with self._locked(scope) as (_, meeting):
                if not interruption_id or meeting.closed or meeting.interruption_id != interruption_id:
                    return False
                meeting.interruption_id = None
                meeting.revision += 1
                return True
        except _MeetingNotFoundError:
            return False

    async def force_release(
        self, scope: MeetingScope, *, expected_token: str | None = None,
        expected_interruption_id: str | None = None,
    ) -> bool:
        """Recover the exact observed ownership/barrier state without playback acknowledgement.

        Omitted selectors assert absence. The host must ensure audio is stopped
        before recovery allows another grant; leases never expire automatically.
        """
        if expected_token is None and expected_interruption_id is None:
            raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID, reason="Force release requires an expected identifier")
        for value in (expected_token, expected_interruption_id):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise_error(
                    StatusCode.AGENT_TEAM_CONFIG_INVALID, reason="Expected identifiers must be nonempty strings",
                )
        try:
            async with self._locked(scope) as (session, meeting):
                if (meeting.closed or meeting.lease_token != expected_token
                        or meeting.interruption_id != expected_interruption_id):
                    return False
                if (meeting.owner_request_id is not None) != (expected_token is not None):
                    return False
                if expected_token is not None:
                    if await self._get_owner(session, scope, meeting) is None:
                        return False
                    await self._release(session, scope, meeting, SpeechRequestStatus.INTERRUPTED)
                meeting.interruption_id = None
                meeting.revision += 1
                return True
        except _MeetingNotFoundError:
            return False

    async def disable(self, scope: MeetingScope) -> bool:
        """Disable audio while allowing queued text to drain without playback."""
        try:
            async with self._locked(scope) as (_, meeting):
                if meeting.closed:
                    return False
                if meeting.status != MeetingStatus.DISABLED:
                    meeting.status = MeetingStatus.DISABLED
                    meeting.revision += 1
                return True
        except _MeetingNotFoundError:
            return False

    async def close_meeting(self, scope: MeetingScope) -> bool:
        """Permanently revoke ownership and cancel queued speech; the host stops audio."""
        model = _get_speech_request_model(scope.session_id)
        try:
            async with self._locked(scope) as (session, meeting):
                if meeting.closed:
                    return False
                await self._release(session, scope, meeting, SpeechRequestStatus.INTERRUPTED)
                await session.execute(
                    update(model).where(
                        *_scope_filter(model, scope), model.status == SpeechRequestStatus.QUEUED,
                    ).values(status=SpeechRequestStatus.CANCELLED)
                )
                meeting.interruption_id = None
                meeting.status = MeetingStatus.CLOSED
                meeting.revision += 1
                return True
        except _MeetingNotFoundError:
            return False
