# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``TeamBackend`` bridge-agent surface.

Covers:
- Capability ceiling enforcement (``enable_bridge``).
- ``spawn_bridge_agent`` happy path + persona requirement.
- ``_bridge_member_specs`` indexing from predefined + dynamic spawn.
- ``set_bridge_adapter`` / ``get_bridge_adapter`` registration surface.
- ``bridge_enabled`` / ``is_bridge_agent`` / ``bridge_agent_names``
  query helpers.
- ``build_team(enable_bridge=False)`` skipping predefined bridges.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.interaction import (
    BridgeProtocolAdapter,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.team import (
    BridgeMailboxInjectMode,
    BridgeMemberSpec,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.team import CapabilityOverrides, TeamBackend

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    token = set_session_id("bridge_session")
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


def _make_backend(
    *,
    db,
    messager,
    enable_bridge: bool = True,
    predefined_members=None,
) -> TeamBackend:
    return TeamBackend(
        team_name="bridge_team",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
        predefined_members=predefined_members or [],
        enable_bridge=enable_bridge,
    )


async def _build_empty_team(backend: TeamBackend) -> None:
    """Create the underlying team + leader rows so subsequent
    ``spawn_bridge_agent`` calls can satisfy the team_member foreign
    key constraint. Skips bridge / human registration so each test
    can drive that path explicitly."""
    await backend.build_team(
        display_name="bridge_team",
        desc="goal",
        leader_display_name="L",
        leader_desc="leader persona",
    )


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_disabled_by_default(db, messager):
    backend = TeamBackend(
        team_name="t",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
    )
    assert backend.bridge_enabled() is False
    assert backend.bridge_agent_names() == frozenset()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_predefined_bridge_indexed_at_init(db, messager):
    spec = BridgeMemberSpec(
        member_name="codex",
        display_name="Codex",
        persona="reviewer",
        protocol="codex",
    )
    backend = _make_backend(db=db, messager=messager, predefined_members=[spec])
    assert backend.is_bridge_agent("codex") is True
    assert backend.bridge_agent_names() == frozenset({"codex"})
    indexed = backend.get_bridge_member_spec("codex")
    assert indexed is not None
    assert indexed.protocol == "codex"


# ---------------------------------------------------------------------------
# spawn_bridge_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_bridge_agent_fails_when_disabled(db, messager):
    backend = _make_backend(db=db, messager=messager, enable_bridge=False)
    result = await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex",
        persona="reviewer",
    )
    assert result.ok is False
    assert "disabled" in result.reason


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_bridge_agent_requires_persona(db, messager):
    backend = _make_backend(db=db, messager=messager)
    result = await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex",
        persona="",
    )
    assert result.ok is False
    assert "persona" in result.reason


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_bridge_agent_registers_index_and_db(db, messager):
    backend = _make_backend(db=db, messager=messager)
    await _build_empty_team(backend)
    result = await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex",
        persona="senior python reviewer",
        mailbox_inject_mode=BridgeMailboxInjectMode.REPHRASE,
        protocol="codex",
        adapter_config={"endpoint": "stdio://codex"},
    )
    assert result.ok, result.reason
    assert backend.is_bridge_agent("codex") is True
    indexed = backend.get_bridge_member_spec("codex")
    assert indexed is not None
    assert indexed.mailbox_inject_mode == BridgeMailboxInjectMode.REPHRASE
    assert indexed.protocol == "codex"
    assert indexed.adapter_config == {"endpoint": "stdio://codex"}
    # The DB row must exist so the rest of the team treats it as a member.
    member = await db.member.get_member("codex", "bridge_team")
    assert member is not None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_bridge_agent_duplicate_fails(db, messager):
    backend = _make_backend(db=db, messager=messager)
    await _build_empty_team(backend)
    r1 = await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex",
        persona="r",
    )
    assert r1.ok
    r2 = await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex Again",
        persona="r2",
    )
    assert r2.ok is False
    assert "already exists" in r2.reason


# ---------------------------------------------------------------------------
# Adapter registration
# ---------------------------------------------------------------------------


class _StubAdapter:
    """Minimal BridgeProtocolAdapter for set/get tests."""

    async def connect(
        self,
        *,
        member_name: str,
        adapter_config: dict[str, object],
        bridge_persona: str,
        team_overview: str,
    ) -> None:
        """Open transport."""
        del member_name, adapter_config, bridge_persona, team_overview

    async def relay(self, *, member_name: str, text: str) -> str:
        """Return canned reply."""
        del member_name
        return f"got: {text}"

    async def close(self) -> None:
        """Idempotent teardown."""


@pytest.mark.asyncio
@pytest.mark.level0
async def test_set_bridge_adapter_unknown_member_raises(db, messager):
    backend = _make_backend(db=db, messager=messager)
    with pytest.raises(KeyError):
        backend.set_bridge_adapter("missing", _StubAdapter())


@pytest.mark.asyncio
@pytest.mark.level0
async def test_set_bridge_adapter_round_trip(db, messager):
    backend = _make_backend(db=db, messager=messager)
    await _build_empty_team(backend)
    await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex",
        persona="r",
    )
    assert backend.get_bridge_adapter("codex") is None
    adapter = _StubAdapter()
    backend.set_bridge_adapter("codex", adapter)
    fetched = backend.get_bridge_adapter("codex")
    assert fetched is adapter
    # Structural Protocol check — the registered adapter satisfies the
    # BridgeProtocolAdapter shape.
    assert isinstance(fetched, BridgeProtocolAdapter)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_set_bridge_adapter_none_clears(db, messager):
    backend = _make_backend(db=db, messager=messager)
    await _build_empty_team(backend)
    await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex",
        persona="r",
    )
    backend.set_bridge_adapter("codex", _StubAdapter())
    backend.set_bridge_adapter("codex", None)
    assert backend.get_bridge_adapter("codex") is None


# ---------------------------------------------------------------------------
# build_team interaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_team_with_predefined_bridge_persists(db, messager):
    spec = BridgeMemberSpec(
        member_name="codex",
        display_name="Codex",
        persona="r",
        protocol="codex",
    )
    backend = _make_backend(db=db, messager=messager, predefined_members=[spec])
    await backend.build_team(
        display_name="bt",
        desc="goal",
        leader_display_name="L",
        leader_desc="leader persona",
    )
    # Member row should exist as a normal teammate.
    member = await db.member.get_member("codex", "bridge_team")
    assert member is not None
    # And the index should survive build_team.
    assert backend.is_bridge_agent("codex") is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_team_skips_predefined_bridge_when_disabled(db, messager):
    spec = BridgeMemberSpec(
        member_name="codex",
        display_name="Codex",
        persona="r",
    )
    backend = _make_backend(db=db, messager=messager, predefined_members=[spec])
    await backend.build_team(
        display_name="bt",
        desc="goal",
        leader_display_name="L",
        leader_desc="leader persona",
        overrides=CapabilityOverrides(enable_bridge=False),
    )
    # No member row.
    member = await db.member.get_member("codex", "bridge_team")
    assert member is None
    # Index cleared so downstream code doesn't try to look it up.
    assert backend.is_bridge_agent("codex") is False
    assert backend.bridge_enabled() is False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_team_enable_bridge_above_ceiling_raises(db, messager):
    """``build_team(enable_bridge=True)`` can not exceed the spec ceiling."""
    backend = _make_backend(db=db, messager=messager, enable_bridge=False)
    from openjiuwen.core.common.exception.errors import BaseError

    with pytest.raises(BaseError, match="enable_bridge=True"):
        await backend.build_team(
            display_name="bt",
            desc="goal",
            leader_display_name="L",
            leader_desc="leader persona",
            overrides=CapabilityOverrides(enable_bridge=True),
        )
