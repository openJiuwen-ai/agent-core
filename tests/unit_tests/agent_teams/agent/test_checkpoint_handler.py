# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the CheckpointHandler (leader checkpoint announcements)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers.checkpoint import CheckpointHandler
from openjiuwen.agent_teams.schema.events import CheckpointCreatedEvent, EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole


class _FakePoll:
    async def pause_polls(self) -> None:
        return None

    async def resume_polls(self) -> None:
        return None


def _make_handler(role: TeamRole) -> tuple[CheckpointHandler, SimpleNamespace]:
    """Build a CheckpointHandler with a mocked host / blueprint / infra."""
    host = SimpleNamespace()
    host.deliver_input = AsyncMock()
    blueprint = SimpleNamespace(
        role=role,
        member_name="leader-1",
        spec=SimpleNamespace(reliability=None),
    )
    handler = CheckpointHandler(host, blueprint, SimpleNamespace(), _FakePoll())
    return handler, host


def _event(**kwargs) -> EventMessage:
    payload = {
        "team_name": "test-team",
        "member_name": "counter-1",
        "name": "count-1",
        "message_count": 7,
        "description": "",
    }
    payload.update(kwargs)
    return EventMessage.from_event(CheckpointCreatedEvent(**payload))


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_delivers_checkpoint_announcement():
    handler, host = _make_handler(TeamRole.LEADER)
    event = _event(description="报数1完成")

    await handler.on_checkpoint_created(event)

    host.deliver_input.assert_awaited_once()
    content = host.deliver_input.await_args.args[0]
    assert host.deliver_input.await_args.kwargs["use_steer"] is False
    assert '<team-event kind="checkpoint">' in content
    assert "count-1" in content
    assert "counter-1" in content
    assert '<team-note kind="announcement-only">' in content


@pytest.mark.asyncio
@pytest.mark.level1
async def test_teammate_does_not_deliver():
    handler, host = _make_handler(TeamRole.TEAMMATE)
    event = _event()

    await handler.on_checkpoint_created(event)

    host.deliver_input.assert_not_awaited()


@pytest.mark.level0
def test_checkpoint_event_type_map_roundtrip():
    event = CheckpointCreatedEvent(
        team_name="test-team",
        member_name="counter-1",
        name="count-1",
        message_count=7,
        description="报数1完成",
    )

    wrapped = EventMessage.from_event(event)
    assert wrapped.event_type == TeamEvent.CHECKPOINT_CREATED

    restored = wrapped.get_payload()
    assert isinstance(restored, CheckpointCreatedEvent)
    assert restored.name == "count-1"
    assert restored.message_count == 7
    assert restored.description == "报数1完成"
    assert restored.member_name == "counter-1"
