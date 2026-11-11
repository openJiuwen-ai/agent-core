# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Meeting controls through external member connections and tool calls."""

from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams import MeetingRoute, MeetingStatus, SpeechRequestStatus, create_meeting_tools
from openjiuwen.agent_teams.context import get_session_id, reset_session_id, set_session_id
from openjiuwen.agent_teams.external import ExternalTeamClient
from openjiuwen.agent_teams.external.tool_gateway import build_external_team_tool_gateway
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig, create_messager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, FrameworkError
from openjiuwen.harness_protocol import ToolInvocation


@pytest.mark.asyncio
async def test_external_member_observes_lifecycle_and_closed_meeting_removes_tools(
    team_db, make_descriptor, monkeypatch,
):
    descriptor = make_descriptor(scope="member")
    transport = MessagerTransportConfig(backend="inprocess", team_name=descriptor.team_name, node_id="meeting-host")
    messager = create_messager(transport)
    await messager.start()
    host = TeamBackend(descriptor.team_name, "leader", True, team_db, messager)
    host.bind_group_session(descriptor.session_id)
    try:
        async with ExternalTeamClient(descriptor) as client:
            assert not client.meeting_enabled
            assert await client.get_meeting_member() is None
            with pytest.raises(ValueError, match="An open meeting is required"):
                await client.install_meeting_tools()

            meeting = await host.enable_meeting(meeting_id="meeting")
            assert client.meeting_enabled
            member = await client.get_meeting_member()
            assert member.member_name == descriptor.member_name
            assert member.scope == meeting.scope
            assert "meeting_get_speaker" not in client.tools
            tools = await client.install_meeting_tools()
            assert set(tools) == {"meeting_get_speaker"}

            owner = await member.enqueue_message(
                "Private body", speech_text="Private speech", route=MeetingRoute("direct", ("leader",)),
            )
            waiting = await member.request_speech(speech_text="Waiting turn")
            claim = await meeting.grant_next()
            result = await client.tools["meeting_get_speaker"].invoke({})
            assert result.success
            assert result.data["request_id"] == owner.request_id
            assert result.data["lease_token"] == claim.lease_token
            assert result.data["content"] is None
            assert result.data["speech_text"] == ""
            assert result.data["publication_message_ids"] == ()

            assert await host.disable_meeting(meeting_id="meeting")
            assert not client.meeting_enabled
            assert (await member.get_state()).status == MeetingStatus.DISABLED
            assert (await meeting.get_speaker()).lease_token == claim.lease_token
            assert await meeting.grant_next() is None
            resumed = await host.enable_meeting()
            assert resumed.scope == meeting.scope
            assert client.meeting_enabled
            assert (await resumed.get_speaker()).lease_token == claim.lease_token

            get_meeting = client._backend.get_meeting

            async def get_meeting_in_client_session():
                assert get_session_id() == descriptor.session_id
                return await get_meeting()

            monkeypatch.setattr(client._backend, "get_meeting", get_meeting_in_client_session)
            token = set_session_id("unrelated_callback_session")
            try:
                assert await host.close_meeting(meeting_id="meeting")
                assert get_session_id() == "unrelated_callback_session"
            finally:
                reset_session_id(token)
            assert not client.meeting_enabled
            assert "meeting_get_speaker" not in client.tools
            assert await client.get_meeting_member() is None
            assert (await meeting.get_state()).status == MeetingStatus.CLOSED
            assert (await meeting.get_request(owner.request_id)).status == SpeechRequestStatus.INTERRUPTED
            assert (await meeting.get_request(waiting.request_id)).status == SpeechRequestStatus.CANCELLED
            with pytest.raises(BaseError, match="Closed meeting IDs cannot be re-enabled"):
                await host.enable_meeting(meeting_id="meeting")
    finally:
        await messager.stop()


@pytest.mark.asyncio
async def test_meeting_tool_returns_failed_output_for_framework_error(team_db, make_descriptor, monkeypatch):
    descriptor = make_descriptor(member="leader", role="leader")
    host = TeamBackend(descriptor.team_name, descriptor.member_name, True, team_db, None)
    host.bind_group_session(descriptor.session_id)
    meeting = await host.enable_meeting(meeting_id="meeting")
    member = meeting.for_member(descriptor.member_name)
    tools = await create_meeting_tools(member)
    failure = FrameworkError(StatusCode.AGENT_TEAM_STATE_INVALID, reason="backend failure")
    get_speaker = AsyncMock(side_effect=failure)
    monkeypatch.setattr(member, "get_speaker", get_speaker)
    tool = tools["meeting_get_speaker"]

    invalid = await tool.invoke({"unexpected": True})
    assert invalid.error == "Only the declared meeting tool arguments are supported"
    get_speaker.assert_not_awaited()
    result = await tool.invoke({})
    assert not result.success
    assert result.error == "meeting_operation_failed"


@pytest.mark.asyncio
async def test_external_gateway_leader_interrupts_only_the_observed_lease(team_db, make_descriptor):
    descriptor = make_descriptor(member="leader", role="leader")
    host = TeamBackend(descriptor.team_name, descriptor.member_name, True, team_db, None)
    host.bind_group_session(descriptor.session_id)
    meeting = await host.enable_meeting(meeting_id="meeting")
    await meeting.for_member("dev-1").request_speech(speech_text="First turn")
    await meeting.for_member("dev-1").request_speech(speech_text="Waiting turn")
    claim = await meeting.grant_next()
    gateway = build_external_team_tool_gateway(
        session_id=descriptor.session_id, team_backend=host, role="leader",
        teammate_mode="build_mode", dispatch_mode="autonomous", lifecycle="temporary", language="en",
    )

    token = set_session_id("unrelated_callback_session")
    try:
        await gateway.install_meeting_tools()
        definitions = {tool.name: tool for tool in await gateway.definitions()}
        name = "meeting_interrupt_speech"
        assert definitions[name].input_schema["required"] == ("expected_token",)

        await gateway.invoke(ToolInvocation(call_id="stale", name=name, arguments={"expected_token": "stale"}))
        assert (await meeting.get_speaker()).lease_token == claim.lease_token
        result = await gateway.invoke(ToolInvocation(
            call_id="interrupt", name=name, arguments={"expected_token": claim.lease_token},
        ))
        assert get_session_id() == "unrelated_callback_session"
    finally:
        reset_session_id(token)

    state = await meeting.get_state()
    assert not result.is_error
    assert state.interruption_id and state.interruption_id in result.content
    assert await meeting.get_speaker() is None
    assert await meeting.grant_next() is None
