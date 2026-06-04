# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``MessageHandler._bridge_deliverable_for``.

Verifies the auto-forward + compose pipeline that wraps a team-side
mailbox message into the text injected into the bridge avatar's
DeepAgent context:

1. ``wrap_outbound_to_remote`` shapes the outbound payload (PASSTHROUGH
   vs REPHRASE).
2. ``BridgeProtocolAdapter.relay`` produces the remote reply.
3. ``compose_bridge_inbound`` assembles the final deliverable that
   carries both halves into the avatar's context.

Adapter-absent path falls back to ``REMOTE_UNAVAILABLE_SENTINEL`` so
the bridge degrades to a normal teammate without crashing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.interaction import REMOTE_UNAVAILABLE_SENTINEL
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.team import (
    BridgeMailboxInjectMode,
    TeamRole,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.team import TeamBackend

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    token = set_session_id("bridge_inbound_session")
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        reset_session_id(token)


@pytest_asyncio.fixture
async def messager():
    yield AsyncMock(spec=Messager)


async def _make_backend_with_bridge(db, messager) -> TeamBackend:
    backend = TeamBackend(
        team_name="bt",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
        enable_bridge=True,
    )
    await backend.build_team(
        display_name="bt",
        desc="goal",
        leader_display_name="L",
        leader_desc="leader persona",
    )
    await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex",
        persona="senior python reviewer",
        mailbox_inject_mode=BridgeMailboxInjectMode.PASSTHROUGH,
    )
    return backend


def _make_handler_for_bridge(backend: TeamBackend) -> MessageHandler:
    """Build a MessageHandler instance with just enough wiring to
    exercise ``_bridge_deliverable_for`` directly."""
    blueprint = SimpleNamespace(
        member_name="codex",
        role=TeamRole.BRIDGE_AGENT,
        team_spec=TeamSpec(team_name="bt", display_name="bt", language="cn"),
    )
    infra = SimpleNamespace(
        team_backend=backend,
        message_manager=backend.message_manager,
    )
    host = MagicMock()
    poll_ctrl = MagicMock()
    handler = MessageHandler.__new__(MessageHandler)
    handler._round = host
    handler._lifecycle = host
    handler._poll = poll_ctrl
    handler._blueprint = blueprint
    handler._infra = infra
    return handler


def _fake_msg(sender: str = "leader", body: str = "review pr 42", broadcast: bool = False):
    return SimpleNamespace(
        message_id="m1",
        from_member_name=sender,
        to_member_name="codex",
        content=body,
        timestamp=123456789,
        broadcast=broadcast,
    )


# ---------------------------------------------------------------------------
# Adapter present — remote reply flows through
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """In-test adapter capturing the outbound text for assertion."""

    def __init__(self, reply: str = "diff looks clean. lgtm.") -> None:
        self.reply = reply
        self.last_text: str | None = None
        self.last_member: str | None = None

    async def connect(
        self,
        *,
        member_name: str,
        adapter_config: dict[str, object],
        bridge_persona: str,
        team_overview: str,
    ) -> None:
        """No-op."""
        del member_name, adapter_config, bridge_persona, team_overview

    async def relay(self, *, member_name: str, text: str) -> str:
        """Capture + canned reply."""
        self.last_member = member_name
        self.last_text = text
        return self.reply

    async def close(self) -> None:
        """No-op."""


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_deliverable_with_adapter_carries_remote_reply(db, messager):
    backend = await _make_backend_with_bridge(db, messager)
    adapter = _FakeAdapter()
    backend.set_bridge_adapter("codex", adapter)
    handler = _make_handler_for_bridge(backend)
    msg = _fake_msg(sender="leader", body="review pr 42")

    text = await handler._bridge_deliverable_for("codex", msg)

    # The remote was actually called and saw the wrapped outbound.
    assert adapter.last_member == "codex"
    assert "review pr 42" in adapter.last_text
    assert "[" in adapter.last_text  # PASSTHROUGH header

    # The composed deliverable contains both halves + the scheduling
    # contract instructions (verbatim pass-through).
    assert "review pr 42" in text
    assert "diff looks clean. lgtm." in text
    assert "原样" in text or "verbatim" in text.lower()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_deliverable_without_adapter_uses_sentinel(db, messager):
    backend = await _make_backend_with_bridge(db, messager)
    # No adapter registered.
    handler = _make_handler_for_bridge(backend)
    msg = _fake_msg(sender="leader", body="status?")

    text = await handler._bridge_deliverable_for("codex", msg)

    assert "status?" in text
    assert REMOTE_UNAVAILABLE_SENTINEL in text


# ---------------------------------------------------------------------------
# Adapter raises — falls back to sentinel, never propagates
# ---------------------------------------------------------------------------


class _RaisingAdapter:
    async def connect(
        self,
        *,
        member_name: str,
        adapter_config: dict[str, object],
        bridge_persona: str,
        team_overview: str,
    ) -> None:
        """No-op."""
        del member_name, adapter_config, bridge_persona, team_overview

    async def relay(self, *, member_name: str, text: str) -> str:
        """Always fails."""
        del member_name, text
        raise RuntimeError("remote down")

    async def close(self) -> None:
        """No-op."""


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_deliverable_swallows_adapter_exception(db, messager):
    backend = await _make_backend_with_bridge(db, messager)
    backend.set_bridge_adapter("codex", _RaisingAdapter())
    handler = _make_handler_for_bridge(backend)
    msg = _fake_msg(sender="leader", body="hi")
    text = await handler._bridge_deliverable_for("codex", msg)
    # The avatar still receives a usable deliverable; the sentinel
    # marks the degraded state so the LLM can react.
    assert "hi" in text
    assert REMOTE_UNAVAILABLE_SENTINEL in text


# ---------------------------------------------------------------------------
# REPHRASE injection mode includes sender role / persona
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_deliverable_rephrase_mode_includes_sender_context(db, messager):
    backend = TeamBackend(
        team_name="bt",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
        enable_bridge=True,
    )
    await backend.build_team(
        display_name="bt",
        desc="goal",
        leader_display_name="L",
        leader_desc="leader persona",
    )
    await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex",
        persona="reviewer",
        mailbox_inject_mode=BridgeMailboxInjectMode.REPHRASE,
    )
    adapter = _FakeAdapter()
    backend.set_bridge_adapter("codex", adapter)
    handler = _make_handler_for_bridge(backend)
    msg = _fake_msg(sender="team_leader", body="please review pr 42")

    await handler._bridge_deliverable_for("codex", msg)

    # The REPHRASE outbound includes the sender's role + persona so the
    # remote sees who it's talking to.
    assert "leader" in adapter.last_text.lower()
    assert "human_agent" not in adapter.last_text
    assert "leader persona" in adapter.last_text or "L" in adapter.last_text
