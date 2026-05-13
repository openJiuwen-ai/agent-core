# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Coverage for ``TeamRuntimeManager._dispatch_payload`` and the
``interact(str, ...)`` prefix grammar.

Routing decisions live at the dispatch boundary:

- Structured payloads (:class:`GodViewMessage`, :class:`OperatorMessage`,
  :class:`HumanAgentMessage`) carry their semantics on the type itself —
  ``_dispatch_payload`` does not re-parse the body.
- A bare ``str`` is translated by :func:`parse_interact_str` into one
  of those typed payloads via the explicit prefixes ``@``, ``#``, ``$``;
  unrecognised inputs default to :class:`GodViewMessage`.
"""

from __future__ import annotations

from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from openjiuwen.agent_teams.interaction import (
    GodViewMessage,
    HumanAgentMessage,
    OperatorMessage,
)
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import (
    ActiveTeam,
    RuntimeState,
)


def _make_agent(*, known_members: set[str] | None = None) -> MagicMock:
    """Build a fake TeamAgent exposing the surface dispatch_payload uses."""
    members = known_members or set()
    agent = MagicMock(name="TeamAgent")
    agent.team_backend = MagicMock(name="TeamBackend")
    agent.team_backend.message_manager = MagicMock(name="TeamMessageManager")
    agent.team_backend.message_manager.send_message = AsyncMock(return_value="msg-id")
    agent.team_backend.message_manager.broadcast_message = AsyncMock(return_value="bcast-id")
    agent.deliver_input = AsyncMock()
    agent.has_team_member = AsyncMock(side_effect=lambda name: name in members)
    avatar = AsyncMock(name="HumanAgentRuntime")
    agent.lookup_human_agent_runtime = MagicMock(return_value=avatar)
    agent._avatar = avatar  # exposed for tests that want to assert on it
    return agent


# ----------------------------------------------------------------------
# Structured payloads — dispatch_payload routes by type, no re-parsing.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_god_view_always_goes_to_leader():
    """GodViewMessage is the explicit leader-direct channel — no @-parsing."""
    agent = _make_agent(known_members={"dev-1"})
    payload = GodViewMessage(body="@dev-1 should not be parsed here")

    result = await TeamRuntimeManager._dispatch_payload(agent, payload)

    assert result.ok
    agent.deliver_input.assert_awaited_once_with("@dev-1 should not be parsed here")
    agent.team_backend.message_manager.send_message.assert_not_called()
    agent.team_backend.message_manager.broadcast_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_operator_message_direct_routes_to_member():
    """OperatorMessage with a target writes a user→target message."""
    agent = _make_agent()

    result = await TeamRuntimeManager._dispatch_payload(
        agent,
        OperatorMessage(body="ping", target="dev-1"),
    )

    assert result.ok
    agent.team_backend.message_manager.send_message.assert_awaited_once_with(
        content="ping",
        to_member_name="dev-1",
        from_member_name="user",
    )
    agent.deliver_input.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_operator_message_broadcasts_when_target_none():
    """OperatorMessage with target=None broadcasts as the user."""
    agent = _make_agent()

    result = await TeamRuntimeManager._dispatch_payload(
        agent,
        OperatorMessage(body="hello team"),
    )

    assert result.ok
    agent.team_backend.message_manager.broadcast_message.assert_awaited_once_with(
        content="hello team",
        from_member_name="user",
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_human_agent_message_drives_avatar_when_no_target():
    """HumanAgentMessage with no target/mention drives the avatar's DeepAgent."""
    agent = _make_agent()
    agent.team_backend.human_agent_names = MagicMock(return_value={"human_alice"})

    result = await TeamRuntimeManager._dispatch_payload(
        agent,
        HumanAgentMessage(body="please summarise design.md", sender="human_alice"),
    )

    assert result.ok
    agent._avatar.deliver_input.assert_awaited_once_with("please summarise design.md")
    agent.team_backend.message_manager.send_message.assert_not_called()


# ----------------------------------------------------------------------
# str input prefix grammar — interact(str, ...) → typed payload → dispatch
# ----------------------------------------------------------------------


async def _make_manager_with_agent(agent: MagicMock) -> TeamRuntimeManager:
    """Build a manager with one running ActiveTeam entry for ``alpha/s1``."""
    manager = TeamRuntimeManager()
    entry = ActiveTeam(
        team_name="alpha",
        agent=agent,
        current_session_id="s1",
        state=RuntimeState.RUNNING,
    )
    await manager.pool.add(entry)
    return manager


@pytest.mark.asyncio
@pytest.mark.level0
async def test_interact_str_at_member_routes_via_operator():
    """``interact("@dev-1 hi")`` parses to OperatorMessage(target='dev-1')."""
    agent = _make_agent(known_members={"dev-1"})
    manager = await _make_manager_with_agent(agent)

    result = await manager.interact("@dev-1 hi", team_name="alpha", session_id="s1")

    assert result.ok
    agent.team_backend.message_manager.send_message.assert_awaited_once_with(
        content="hi",
        to_member_name="dev-1",
        from_member_name="user",
    )
    agent.deliver_input.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_interact_str_at_all_broadcasts_via_operator():
    """``interact("@all status")`` parses to OperatorMessage(target=None)."""
    agent = _make_agent()
    manager = await _make_manager_with_agent(agent)

    result = await manager.interact("@all status", team_name="alpha", session_id="s1")

    assert result.ok
    agent.team_backend.message_manager.broadcast_message.assert_awaited_once_with(
        content="status",
        from_member_name="user",
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_interact_str_hash_prefix_routes_to_god_view():
    """``interact("# leader-only thought")`` parses to GodViewMessage."""
    agent = _make_agent()
    manager = await _make_manager_with_agent(agent)

    result = await manager.interact("# raw question", team_name="alpha", session_id="s1")

    assert result.ok
    agent.deliver_input.assert_awaited_once_with("raw question")
    agent.team_backend.message_manager.send_message.assert_not_called()
    agent.team_backend.message_manager.broadcast_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_interact_str_dollar_prefix_drives_human_agent():
    """``interact("$alice please summarise")`` parses to HumanAgentMessage."""
    agent = _make_agent()
    agent.team_backend.human_agent_names = MagicMock(return_value={"alice"})
    manager = await _make_manager_with_agent(agent)

    result = await manager.interact("$alice please summarise", team_name="alpha", session_id="s1")

    assert result.ok
    agent._avatar.deliver_input.assert_awaited_once_with("please summarise")
    agent.deliver_input.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_interact_str_no_prefix_falls_back_to_god_view():
    """Inputs with no recognised prefix preserve the bare-str shortcut."""
    agent = _make_agent()
    manager = await _make_manager_with_agent(agent)

    result = await manager.interact("just a plain question", team_name="alpha", session_id="s1")

    assert result.ok
    agent.deliver_input.assert_awaited_once_with("just a plain question")
    agent.team_backend.message_manager.send_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_interact_str_at_target_without_body_is_unparseable():
    """``@dev-1`` alone (no body) does not match the regex; falls back to god-view."""
    agent = _make_agent(known_members={"dev-1"})
    manager = await _make_manager_with_agent(agent)

    result = await manager.interact("@dev-1", team_name="alpha", session_id="s1")

    assert result.ok
    agent.deliver_input.assert_awaited_once_with("@dev-1")
    agent.team_backend.message_manager.send_message.assert_not_called()
