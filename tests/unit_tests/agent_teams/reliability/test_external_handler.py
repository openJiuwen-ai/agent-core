# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the leader-side ExternalRuntimeHandler."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.i18n import get_language, set_language
from openjiuwen.agent_teams.reliability.external_handler import ExternalRuntimeHandler
from openjiuwen.agent_teams.schema.events import EventMessage, ExternalRuntimeRetryingEvent
from openjiuwen.agent_teams.schema.team import TeamRole


class _Host:
    """Round-controller stub recording delivered inputs."""

    def __init__(self) -> None:
        self.delivered: list[str] = []

    async def deliver_input(self, content: str, *, use_steer: bool = False) -> None:
        self.delivered.append(content)


class _Blueprint:
    def __init__(self, role: TeamRole) -> None:
        self.role = role
        self.member_name = "team_leader"


def _make_handler(role: TeamRole = TeamRole.LEADER) -> tuple[ExternalRuntimeHandler, _Host]:
    host = _Host()
    handler = ExternalRuntimeHandler(host, _Blueprint(role), infra=None, poll_ctrl=None)
    return handler, host


def _retrying_event() -> EventMessage:
    return EventMessage.from_event(
        ExternalRuntimeRetryingEvent(
            team_name="t",
            member_name="worker1",
            agent_kind="codex",
            model="gpt-effective",
            phase="turn",
            category="server_unavailable",
            summary="overloaded",
            reason={
                "message": "upstream overloaded",
                "sdk_error_code": "serverOverloaded",
                "http_status": 529,
            },
            attempt=2,
            max_attempts=5,
            round_id=3,
        )
    )


@pytest.fixture
def _lang():
    saved = get_language()
    set_language("cn")
    yield
    set_language(saved)


@pytest.mark.asyncio
async def test_leader_receives_retrying_nudge(_lang):
    handler, host = _make_handler(TeamRole.LEADER)
    await handler.on_external_retry(_retrying_event())
    assert len(host.delivered) == 1
    assert "worker1" in host.delivered[0]
    assert "模型 gpt-effective" in host.delivered[0]
    assert "server_unavailable" in host.delivered[0]
    assert "upstream overloaded" in host.delivered[0]
    assert "2/5" in host.delivered[0]
    assert "http_status=529" in host.delivered[0]
    assert "sdk_error_code=serverOverloaded" in host.delivered[0]


@pytest.mark.asyncio
async def test_non_leader_is_gated(_lang):
    handler, host = _make_handler(TeamRole.TEAMMATE)
    await handler.on_external_retry(_retrying_event())
    assert host.delivered == []


def test_event_method_map_registered():
    from openjiuwen.agent_teams.schema.events import TeamEvent

    assert ExternalRuntimeHandler.EVENT_METHOD_MAP == {
        TeamEvent.EXTERNAL_RUNTIME_RETRYING: "on_external_retry",
    }
