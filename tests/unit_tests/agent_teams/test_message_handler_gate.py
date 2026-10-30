# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``MessageHandler._harness_input_blocked`` (S_03 mechanism 18).

The gate decides, from the member's persisted status, whether the local
harness may be fed. F_118 adds the dormant exception: a dormant-bound member
with unread input is fed one last round instead of settling on top of it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole


def _gate(*, status: str, lifetime: str | None, unread: set[str], in_flight: bool = False) -> MessageHandler:
    handler = object.__new__(MessageHandler)
    handler._blueprint = SimpleNamespace(role=TeamRole.TEAMMATE)
    handler._infra = SimpleNamespace(
        team_backend=SimpleNamespace(
            get_member_status=AsyncMock(return_value=status),
            get_member_lifetime=AsyncMock(return_value=lifetime),
        ),
        message_manager=SimpleNamespace(members_with_unread=AsyncMock(return_value=unread)),
    )
    handler._round = SimpleNamespace(has_in_flight_round=lambda: in_flight)
    handler._lifecycle = SimpleNamespace(shutdown_self=AsyncMock())
    return handler


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("status", [MemberStatus.SHUTDOWN.value, MemberStatus.DORMANT.value])
async def test_gate_drops_delivery_to_a_member_without_runtime(status):
    handler = _gate(status=status, lifetime=None, unread=set())

    assert await handler._harness_input_blocked("dev-1") is True
    handler._lifecycle.shutdown_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_gate_settles_an_idle_shutdown_request_without_a_round():
    handler = _gate(status=MemberStatus.SHUTDOWN_REQUESTED.value, lifetime=None, unread={"dev-1"})

    assert await handler._harness_input_blocked("dev-1") is True
    handler._lifecycle.shutdown_self.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_gate_defers_a_dormant_settle_while_input_is_unread():
    """F_118: the message that arrived after the retire request is delivered, not stranded."""
    handler = _gate(status=MemberStatus.SHUTDOWN_REQUESTED.value, lifetime="dormant", unread={"dev-1"})

    assert await handler._harness_input_blocked("dev-1") is False
    handler._lifecycle.shutdown_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_gate_settles_a_dormant_member_with_nothing_unread():
    handler = _gate(status=MemberStatus.SHUTDOWN_REQUESTED.value, lifetime="dormant", unread=set())

    assert await handler._harness_input_blocked("dev-1") is True
    handler._lifecycle.shutdown_self.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_gate_lets_a_running_round_absorb_the_request():
    handler = _gate(status=MemberStatus.SHUTDOWN_REQUESTED.value, lifetime="dormant", unread=set(), in_flight=True)

    assert await handler._harness_input_blocked("dev-1") is False
    handler._lifecycle.shutdown_self.assert_not_awaited()
