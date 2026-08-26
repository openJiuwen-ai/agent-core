# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the simplified Organization TransportAPI."""

from __future__ import annotations

from typing import Any

import pytest

from openjiuwen.agent_teams.organization.events import OrgEvent, OrgTopic
from openjiuwen.agent_teams.organization.transport_api import (
    NegotiationRequest,
    TransportAPI,
)


class FakeMessager:
    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish(self, topic_id: str, message: Any) -> None:
        self.published.append((topic_id, message))


@pytest.mark.asyncio
async def test_negotiate_defaults_to_inprocess() -> None:
    api = TransportAPI(
        organization_id="org-1",
        session_id="sess-1",
        from_team_id="team-a",
        messager=FakeMessager(),  # type: ignore[arg-type]
    )
    result = await api.negotiate(
        NegotiationRequest(from_team_id="team-a", to_team_id="team-b")
    )
    assert result.transport_type == "inprocess"


@pytest.mark.asyncio
async def test_deliver_publishes_to_team_inbox() -> None:
    messager = FakeMessager()
    api = TransportAPI(
        organization_id="org-1",
        session_id="sess-1",
        from_team_id="team-a",
        messager=messager,  # type: ignore[arg-type]
    )
    result = await api.deliver(content="hello", to_team_id="team-b", message_id="org-msg-fixed")
    assert result.success is True
    assert result.message_id == "org-msg-fixed"
    assert len(messager.published) == 1
    topic, message = messager.published[0]
    assert topic == OrgTopic.TEAM_INBOX.build("sess-1", "org-1", "team-b")
    assert message.event_type == OrgEvent.LEADER_MESSAGE
    assert "content" not in message.payload
    assert message.payload["from_team_id"] == "team-a"
    assert message.payload["to_team_id"] == "team-b"
    assert message.payload["message_id"] == "org-msg-fixed"