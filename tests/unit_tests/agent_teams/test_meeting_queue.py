# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavioral coverage for queue admission, ownership, and persisted recovery."""

import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams import MeetingCoordinator, MeetingScope, SpeechRequestStatus
from openjiuwen.agent_teams.context import get_session_id, reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.schema.events import MeetingQueueChangedEvent, TeamTopic
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.tools.database import DatabaseConfig, TeamDatabase
from openjiuwen.agent_teams.tools.models import _get_meeting_model, _get_speech_request_model


SCOPE = MeetingScope("queue_session", "queue_team", "meeting")


async def _seed_team(db):
    await db.team.create_team(SCOPE.team_name, "Queue Team", "leader")
    for name in ("leader", "first", "second"):
        await db.member.create_member(
            name, SCOPE.team_name, name, "{}", MemberStatus.READY,
            role="leader" if name == "leader" else "teammate",
        )


@pytest_asyncio.fixture
async def queue_db():
    db = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
    try:
        await db.initialize()
        await _seed_team(db)
        yield db
    finally:
        await db.close()


@pytest_asyncio.fixture
async def meeting(queue_db):
    coordinator = MeetingCoordinator(queue_db, SCOPE)
    await coordinator.start()
    return coordinator


@pytest.mark.asyncio
async def test_priority_changes_reorder_pending_without_preempting_owner(meeting):
    first = meeting.for_member("first")
    second = meeting.for_member("second")
    owner = await first.request_speech(speech_text="Already playing", request_id="owner")
    claim = await meeting.grant_next()
    assert claim.request_id == owner.request_id

    await first.request_speech(speech_text="First in ordinary queue", request_id="ordinary")
    await second.request_speech(speech_text="First addressed turn", request_id="addressed-1")
    await second.request_speech(speech_text="Second addressed turn", request_id="addressed-2")
    await meeting.for_member("leader").request_speech(speech_text="Leader turn", request_id="leader")

    assert [request.request_id for request in await meeting.list_pending()] == [
        "leader", "ordinary", "addressed-1", "addressed-2",
    ]
    assert await meeting.set_addressed_members(["second"])
    assert [request.request_id for request in await meeting.list_pending()] == [
        "leader", "addressed-1", "addressed-2", "ordinary",
    ]
    assert (await meeting.get_speaker()).lease_token == claim.lease_token
    assert await meeting.grant_next() is None
    assert await meeting.release(claim.lease_token)
    assert await meeting.get_speaker() is None
    for request_id in ("leader", "addressed-1", "addressed-2", "ordinary"):
        next_claim = await meeting.grant_next()
        assert next_claim.request_id == request_id
        assert await meeting.release(next_claim.lease_token)
    assert await meeting.grant_next() is None


@pytest.mark.asyncio
async def test_concurrent_grants_and_stale_recovery_leave_one_owner(queue_db, meeting):
    member = meeting.for_member("first")
    first = await member.request_speech(speech_text="First turn")
    second = await member.request_speech(speech_text="Second turn")
    other_host = MeetingCoordinator(queue_db, SCOPE)

    results = await asyncio.gather(meeting.grant_next(), other_host.grant_next())
    claim, = [result for result in results if result is not None]
    assert claim.request_id == first.request_id
    assert claim.lease_token
    assert (await other_host.get_speaker()).request_id == first.request_id
    assert not await other_host.force_release(expected_token="stale")
    assert await other_host.force_release(expected_token=claim.lease_token)
    assert (await meeting.get_request(first.request_id)).status == SpeechRequestStatus.INTERRUPTED

    successor = await meeting.grant_next()
    assert successor.request_id == second.request_id
    assert successor.lease_token != claim.lease_token
    assert not await other_host.release(claim.lease_token)
    assert not await other_host.force_release(expected_token=claim.lease_token)
    assert await other_host.interrupt_speech(expected_token=claim.lease_token) is None
    assert (await other_host.get_speaker()).lease_token == successor.lease_token


@pytest.mark.asyncio
async def test_member_cannot_cancel_or_interrupt_another_members_turn(queue_db, meeting):
    first = meeting.for_member("first")
    second = meeting.for_member("second")
    queued = await first.request_speech(speech_text="First turn")
    cancelled = await first.request_speech(speech_text="Discard this turn")
    assert await second.get_request(queued.request_id) is None
    assert not await second.cancel_request(queued.request_id)
    assert await first.cancel_request(cancelled.request_id)
    assert (await first.get_request(cancelled.request_id)).status == SpeechRequestStatus.CANCELLED
    assert not await first.cancel_request(cancelled.request_id)

    claim = await meeting.grant_next()
    assert claim.request_id == queued.request_id
    assert not await first.cancel_request(claim.request_id)
    with pytest.raises(ValueError, match="Only the meeting Leader"):
        await first.interrupt_speech(expected_token=claim.lease_token)
    leader = meeting.for_member("leader")
    assert await queue_db.member.update_member_status("leader", SCOPE.team_name, MemberStatus.SHUTDOWN_REQUESTED)
    with pytest.raises(ValueError, match="has not departed"):
        await leader.interrupt_speech(expected_token=claim.lease_token)
    with pytest.raises(ValueError, match="has not departed"):
        await leader.request_speech(speech_text="Departed leader")
    assert (await meeting.get_speaker()).lease_token == claim.lease_token


@pytest.mark.asyncio
async def test_failed_queue_wake_keeps_admission_and_request_retry_idempotent(queue_db):
    messager = AsyncMock(spec=Messager)
    coordinator = MeetingCoordinator(queue_db, SCOPE, messager=messager)
    await coordinator.start()
    messager.publish.reset_mock()
    messager.publish.side_effect = RuntimeError("transport unavailable")

    member = coordinator.for_member("first")
    request = await member.request_speech(speech_text="Original", request_id="retry")
    messager.publish.assert_awaited_once()
    wake = messager.publish.await_args.kwargs
    assert wake["topic_id"] == TeamTopic.MEETING_QUEUE.build(SCOPE.session_id, SCOPE.team_name)
    event = wake["message"].get_payload()
    assert isinstance(event, MeetingQueueChangedEvent)
    assert (event.team_name, event.meeting_id) == (SCOPE.team_name, SCOPE.meeting_id)

    assert await member.request_speech(speech_text="Original", request_id="retry") == request
    with pytest.raises(ValueError, match="different Meeting content"):
        await member.request_speech(speech_text="Changed", request_id="retry")
    assert await coordinator.list_pending() == (request,)
    messager.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_session_upgrade_and_interruption_recovery_after_reopen(tmp_path):
    config = DatabaseConfig(connection_string=str(tmp_path / "team.db"))
    home = set_task_openjiuwen_home(tmp_path / "home")
    token = set_session_id(SCOPE.session_id)
    original_db = TeamDatabase(config)
    try:
        await original_db.initialize()
        await _seed_team(original_db)
        assert await original_db.message.create_message(
            "legacy-message", SCOPE.team_name, "leader", "Existing text", to_member_name="first",
        )
        # Keep the existing Team/session schema and data, with the two new tables absent.
        async with original_db.engine.begin() as connection:
            for model in (_get_speech_request_model(), _get_meeting_model()):
                await connection.run_sync(model.__table__.drop)
        await original_db.close()

        ambient = set_session_id("unrelated_session")
        try:
            await _verify_upgrade_and_recovery(config)
            assert get_session_id() == "unrelated_session"
        finally:
            reset_session_id(ambient)
    finally:
        await original_db.close()
        reset_session_id(token)
        reset_task_openjiuwen_home(home)


async def _verify_upgrade_and_recovery(config):
    upgraded_db = TeamDatabase(config)
    try:
        meeting = MeetingCoordinator(upgraded_db, SCOPE)
        assert await meeting.get_state() is None
        assert (await meeting.start()).scope == SCOPE
        assert (await meeting.get_source_message("legacy-message")).content == "Existing text"

        member = meeting.for_member("first")
        owner = await member.request_speech(speech_text="Interrupted turn")
        waiting = await member.request_speech(speech_text="Waiting turn")
        claim = await meeting.grant_next()
        interruption = await meeting.interrupt_speech(expected_token=claim.lease_token)
        assert interruption.request_id == owner.request_id
        assert interruption.lease_token == claim.lease_token
    finally:
        await upgraded_db.close()

    reopened_db = TeamDatabase(config)
    try:
        restored = MeetingCoordinator(reopened_db, SCOPE)
        assert (await restored.get_state()).interruption_id == interruption.interruption_id
        assert (await restored.get_request(owner.request_id)).status == SpeechRequestStatus.INTERRUPTED
        assert await restored.get_speaker() is None
        assert not await restored.resume_after_interruption("stale")
        assert not await restored.force_release(expected_token=claim.lease_token)
        assert await restored.grant_next() is None
        assert await restored.resume_after_interruption(interruption.interruption_id)
        assert not await restored.resume_after_interruption(interruption.interruption_id)
        assert (await restored.grant_next()).request_id == waiting.request_id
    finally:
        await reopened_db.close()
