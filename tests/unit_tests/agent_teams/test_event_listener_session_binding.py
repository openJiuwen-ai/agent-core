# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Events delivered by a session's subscription are handled in that session."""

from __future__ import annotations

import asyncio
import contextvars
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.context import get_session_id, reset_session_id, set_session_id
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from tests.test_logger import logger

_SESSION_ID = "listener-binding-session"
_TEAM_NAME = "binding-team"
_LEADER = "leader"


class _CapturingMessager:
    """Record topic handlers the way a messager would hold them."""

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    async def register_direct_message_handler(self, handler: Any) -> None:
        """Accept the direct-message handler without using it."""
        del handler

    async def subscribe(self, topic: str, handler: Any) -> None:
        """Keep the handler so the test can deliver events through it."""
        self.handlers[topic] = handler


async def _subscribed_handler(host: SimpleNamespace) -> Any:
    """Subscribe a leader kernel under the test session and return its topic handler."""
    kernel = CoordinationKernel(host)
    kernel._event_bus = SimpleNamespace(enqueue=AsyncMock())
    token = set_session_id(_SESSION_ID)
    try:
        await kernel.subscribe_transport(_TEAM_NAME)
    finally:
        reset_session_id(token)
    return next(iter(host.infra.messager.handlers.values()))


async def _deliver_unbound(handler: Any, event: EventMessage) -> None:
    """Deliver from a fresh context, as a receive loop started elsewhere would."""
    await asyncio.create_task(handler(event), context=contextvars.Context())


@pytest.mark.asyncio
@pytest.mark.level1
async def test_listeners_run_under_the_subscription_session() -> None:
    """A delivery from an unbound receive loop still reaches listeners in-session.

    The messager invokes topic handlers from its own receive loop (or the
    publisher's task), neither bound to the subscribing session. Listeners such
    as the observability monitor resolve the Team root by that session, so an
    unbound delivery used to leave them resolving nothing — or another session.
    """
    seen_sessions: list[str] = []

    async def listener(event: EventMessage) -> None:
        del event
        seen_sessions.append(get_session_id())

    host = SimpleNamespace(
        member_name=_LEADER,
        role=TeamRole.LEADER,
        infra=SimpleNamespace(messager=_CapturingMessager()),
        state=SimpleNamespace(event_listeners=[listener]),
    )
    handler = await _subscribed_handler(host)

    await _deliver_unbound(handler, EventMessage(event_type=TeamEvent.STANDBY, payload={"team_name": _TEAM_NAME}))
    logger.info("sessions seen by listener: {}", seen_sessions)

    assert seen_sessions == [_SESSION_ID]
    assert get_session_id() == ""


@pytest.mark.asyncio
@pytest.mark.level1
async def test_self_broadcast_lookup_reads_the_subscription_session_tables() -> None:
    """The group-chat check on a self-sent broadcast queries this session's tables.

    Message tables are per session and named from the session contextvar, so
    the lookup must run with the subscription's session bound, not the
    receive loop's empty one.
    """
    lookup_sessions: list[str] = []

    async def get_message(message_id: str) -> None:
        del message_id
        lookup_sessions.append(get_session_id())

    host = SimpleNamespace(
        member_name=_LEADER,
        role=TeamRole.LEADER,
        infra=SimpleNamespace(
            messager=_CapturingMessager(),
            team_backend=SimpleNamespace(db=SimpleNamespace(message=SimpleNamespace(get_message=get_message))),
        ),
        state=SimpleNamespace(event_listeners=[]),
    )
    handler = await _subscribed_handler(host)
    event = EventMessage(
        event_type=TeamEvent.BROADCAST,
        payload={"team_name": _TEAM_NAME, "message_id": "msg-1", "from_member_name": _LEADER},
        sender_id=_LEADER,
    )

    await _deliver_unbound(handler, event)
    logger.info("sessions seen by message lookup: {}", lookup_sessions)

    assert lookup_sessions == [_SESSION_ID]
