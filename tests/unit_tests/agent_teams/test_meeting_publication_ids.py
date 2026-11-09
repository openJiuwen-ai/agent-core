# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public Meeting publication IDs bridge queue claims and Team messaging."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.meeting import (
    MeetingCoordinator,
    MeetingRoute,
    MeetingScope,
    SpeechRequestStatus,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager


@pytest_asyncio.fixture
async def runtime():
    scope = MeetingScope("meeting_publication_ids", "publication_team", "meeting")
    token = set_session_id(scope.session_id)
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    try:
        await db.initialize()
        # Keep this queue/message contract test independent of content-file I/O.
        db.message._file_store = None
        await db.team.create_team(scope.team_name, "Publication Team", "speaker")
        await db.member.create_member("speaker", scope.team_name, "Speaker", "{}", "ready", role="leader")
        await db.member.create_member("observer", scope.team_name, "Observer", "{}", "ready")
        manager = TeamMessageManager(scope.team_name, "speaker", db, AsyncMock(spec=Messager))
        meeting = MeetingCoordinator(db, scope, message_manager=manager)
        await meeting.start()
        yield SimpleNamespace(db=db, meeting=meeting, manager=manager, scope=scope)
    finally:
        await db.close()
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [
    MeetingRoute("direct", ("alice",)),
    MeetingRoute("multicast", ("alice", "bob")),
    MeetingRoute("broadcast"),
])
async def test_reserved_ids_complete_public_send_flow(runtime, route):
    queued = await runtime.meeting.for_member("speaker").enqueue_message(
        "Queued body", speech_text="Spoken summary", route=route, request_id="request-1",
    )
    expected_count = len(route.recipients) if route.recipients else 1
    assert len(queued.publication_message_ids) == expected_count
    assert len(set(queued.publication_message_ids)) == expected_count
    assert queued.source_message_ids == ()
    for message_id in queued.publication_message_ids:
        assert await runtime.db.message.get_message(message_id) is None

    claimed = await runtime.meeting.grant_next()
    assert claimed.publication_message_ids == queued.publication_message_ids
    assert claimed.source_message_ids == ()
    assert not await runtime.meeting.release(claimed.lease_token)
    with pytest.raises(ValueError, match="Publication source must belong"):
        await runtime.meeting.finish_publication(
            claimed.request_id, claimed.lease_token, source_message_ids=claimed.publication_message_ids,
        )
    if route.kind == "direct":
        message_id = await runtime.manager.send_message(
            claimed.content, route.recipients[0], message_id=claimed.publication_message_ids[0],
        )
        sent_ids = (message_id,)
    elif route.kind == "multicast":
        sent_ids = tuple(await runtime.manager.multicast_message(
            claimed.content, list(route.recipients), message_ids=list(claimed.publication_message_ids),
        ))
    else:
        message_id = await runtime.manager.broadcast_message(
            claimed.content, message_id=claimed.publication_message_ids[0],
        )
        sent_ids = (message_id,)
    assert sent_ids == claimed.publication_message_ids

    finished = await runtime.meeting.finish_publication(
        claimed.request_id, claimed.lease_token, source_message_ids=sent_ids,
    )
    assert finished.source_message_ids == sent_ids
    assert finished.publication_message_ids == queued.publication_message_ids
    for message_id in sent_ids:
        row = await runtime.db.message.get_message(message_id)
        assert row.content == "Queued body"
    assert await runtime.meeting.release(claimed.lease_token)
    assert await runtime.meeting.get_speaker() is None

    restored = await MeetingCoordinator(runtime.db, runtime.scope).get_request(claimed.request_id)
    assert restored.status == SpeechRequestStatus.COMPLETED
    assert restored.publication_message_ids == sent_ids
    assert restored.source_message_ids == sent_ids


@pytest.mark.asyncio
async def test_group_reservation_survives_reconstruction_and_retry(runtime):
    member = runtime.meeting.for_member("speaker")
    queued = await member.enqueue_message(
        "Group body", speech_text="Group summary", route=MeetingRoute(), request_id="group-1",
    )
    assert len(queued.publication_message_ids) == 1
    assert queued.source_message_ids == ()
    retried = await member.enqueue_message(
        "Group body", speech_text="Group summary", route=MeetingRoute(), request_id="group-1",
    )
    assert retried.publication_message_ids == queued.publication_message_ids
    reconstructed = MeetingCoordinator(runtime.db, runtime.scope)
    pending, = await reconstructed.list_pending()
    assert pending.publication_message_ids == queued.publication_message_ids
    claimed = await reconstructed.grant_next()
    assert claimed.publication_message_ids == queued.publication_message_ids
    observed = await reconstructed.get_speaker()
    assert observed.publication_message_ids == queued.publication_message_ids


@pytest.mark.asyncio
async def test_speech_only_has_no_publication_ids(runtime):
    queued = await runtime.meeting.for_member("speaker").request_speech(speech_text="Audio only")
    assert queued.publication_message_ids == ()
    claimed = await runtime.meeting.grant_next()
    assert claimed.publication_message_ids == ()
    finished = await runtime.meeting.finish_publication(claimed.request_id, claimed.lease_token)
    assert finished.publication_message_ids == ()
    assert finished.source_message_ids == ()
    assert await runtime.meeting.release(claimed.lease_token)


@pytest.mark.asyncio
async def test_member_speaker_view_redacts_reserved_ids(runtime):
    await runtime.meeting.for_member("speaker").enqueue_message(
        "Private body", speech_text="Private summary", route=MeetingRoute("direct", ("alice",)),
    )
    claimed = await runtime.meeting.grant_next()
    assert claimed.publication_message_ids
    observed = await runtime.meeting.for_member("observer").get_speaker()
    assert observed.request_id == claimed.request_id
    assert observed.publication_message_ids == ()
    assert observed.source_message_ids == ()
    assert observed.content is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("changed_fields", "error"), [
    ({"from_member_name": "observer"}, "belong to the requesting member"),
    ({"content": "Different body"}, "match queued content"),
    ({"to_member_name": "bob"}, "match queued route"),
])
async def test_reserved_id_does_not_allow_mismatched_publication(runtime, changed_fields, error):
    meeting = runtime.meeting
    await meeting.for_member("speaker").enqueue_message(
        "Queued body", speech_text="Spoken summary", route=MeetingRoute("direct", ("alice",)),
    )
    claim = await meeting.grant_next()
    message_fields = {
        "from_member_name": "speaker", "content": "Queued body", "to_member_name": "alice",
        **changed_fields,
    }
    assert await runtime.db.message.create_message(
        claim.publication_message_ids[0], runtime.scope.team_name, **message_fields,
    )

    with pytest.raises(ValueError, match=error):
        await meeting.finish_publication(
            claim.request_id, claim.lease_token, source_message_ids=claim.publication_message_ids,
        )

    current = await meeting.get_speaker()
    assert current.lease_token == claim.lease_token
    assert current.source_message_ids == ()
    assert current.content == "Queued body"
    assert not await meeting.release(claim.lease_token)


@pytest.mark.asyncio
@pytest.mark.parametrize(("disabled", "playback_ready"), [
    (False, False),
    (True, True),
], ids=["audio-not-ready", "meeting-disabled"])
async def test_text_can_publish_without_a_playback_grant(runtime, disabled, playback_ready):
    meeting = runtime.meeting
    if disabled:
        assert await meeting.disable()
    queued = await meeting.for_member("speaker").enqueue_message(
        "Publish this text", speech_text="Skip this audio", route=MeetingRoute("broadcast"),
    )

    claim = await meeting.grant_next(playback_ready=playback_ready)
    assert claim.request_id == queued.request_id
    assert claim.status == SpeechRequestStatus.PUBLISHING
    assert not await meeting.release(claim.lease_token)
    message_id = await runtime.manager.broadcast_message(claim.content, message_id=claim.publication_message_ids[0])
    finished = await meeting.finish_publication(claim.request_id, claim.lease_token, source_message_ids=(message_id,))

    assert finished.status == SpeechRequestStatus.TEXT_ONLY
    assert finished.lease_token is None
    assert finished.source_message_ids == (message_id,)
    assert (await runtime.db.message.get_message(message_id)).content == "Publish this text"
    assert await meeting.get_speaker() is None
