# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for DB-backed ``is_external_cli_agent`` / ``_lookup_role``.

``TeamBackend.is_external_cli_agent`` now queries
``member_options.cli_agent`` from DB on every call (mirroring
``is_human_agent``) instead of reading the in-memory ``_external_cli_specs``
cache. That cache is only repopulated by
``restore_external_cli_specs_from_db`` inside ``recover_team``; a caller
reaching the probe before that restore runs (fresh build, post-
``clean_team`` rebuild, or any path that skips recover) would otherwise get
a stale ``False`` for a real external-CLI member — mislabeling it as a plain
``TEAMMATE`` on the observability ``agentteam.agent.role`` stamp.

These tests build a real DB-backed ``TeamBackend``, spawn an external-CLI
member, then drop the in-memory cache to simulate the "restore not yet
run" window and assert the probe still answers from DB. End-to-end
coverage of ``_lookup_role`` (the consumer the PR touched) is included so
the stamp is verified, not just the predicate.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.team import (
    BridgeMailboxInjectMode,
    ExternalCliAgentSpec,
    TeamRole,
    TeamSpec,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.team import TeamBackend

_TEAM = "ext_cli_probe_team"


@pytest_asyncio.fixture
async def backend():
    token = set_session_id("ext_cli_probe_session")
    db = TeamDatabase(
        DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    )
    await db.initialize()
    messager = AsyncMock(spec=Messager)
    bk = TeamBackend(
        team_name=_TEAM,
        member_name="leader",
        is_leader=True,
        db=db,
        messager=messager,
        enable_bridge=True,
        external_cli_agents=[ExternalCliAgentSpec(cli_agent="claude")],
    )
    await bk.build_team(
        display_name="Probe Team",
        desc="goal",
        leader_display_name="L",
        leader_desc="leader persona",
    )
    await bk.spawn_external_cli_agent(
        member_name="cli-1",
        display_name="CLI One",
        cli_agent="claude",
        prompt="senior reviewer",
    )
    try:
        yield bk
    finally:
        await db.close()
        reset_session_id(token)


# ---------------------------------------------------------------------------
# is_external_cli_agent — the predicate itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_is_external_cli_agent_reads_db_when_cache_empty(backend):
    """Cache dropped → the probe still resolves from DB."""
    backend._external_cli_specs.clear()
    assert await backend.is_external_cli_agent("cli-1") is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_is_external_cli_agent_without_restore(backend):
    """Regression guard for the review concern: deliberately skip
    ``restore_external_cli_specs_from_db`` (cache never populated for a
    fresh-process leader that hasn't recovered yet) and assert the probe
    still answers ``True`` — the answer no longer depends on the
    in-memory cache restore timing."""
    backend._external_cli_specs.clear()
    assert await backend.is_external_cli_agent("cli-1") is True
    # And the cache stays empty — resolution did not consult it.
    assert "cli-1" not in backend._external_cli_specs


@pytest.mark.asyncio
@pytest.mark.level0
async def test_is_external_cli_agent_false_for_non_cli_member(backend):
    """A plain teammate (the leader) is not an external-CLI member."""
    assert await backend.is_external_cli_agent("leader") is False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_is_external_cli_agent_false_for_unknown_member(backend):
    """A member name with no DB row resolves to ``False``."""
    assert await backend.is_external_cli_agent("nobody") is False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_is_external_cli_agent_empty_name_is_false(backend):
    """Defensive guard mirroring ``is_human_agent``: empty / None name."""
    assert await backend.is_external_cli_agent("") is False


# ---------------------------------------------------------------------------
# _lookup_role — the consumer the PR touched (end-to-end stamp)
# ---------------------------------------------------------------------------


def _make_handler(backend: TeamBackend) -> MessageHandler:
    blueprint = SimpleNamespace(
        member_name="leader",
        role=TeamRole.LEADER,
        team_spec=TeamSpec(
            team_name=_TEAM,
            display_name="Probe Team",
            language="cn",
            leader_member_name="leader",
        ),
    )
    infra = SimpleNamespace(
        team_backend=backend,
        message_manager=backend.message_manager,
    )
    handler = MessageHandler.__new__(MessageHandler)
    handler._round = MagicMock()
    handler._lifecycle = MagicMock()
    handler._poll = MagicMock()
    handler._blueprint = blueprint
    handler._infra = infra
    return handler


@pytest.mark.asyncio
@pytest.mark.level0
async def test_lookup_role_stamps_external_cli_without_restore(backend):
    """End-to-end: with the cache empty, ``_lookup_role`` still stamps the
    member as ``EXTERNAL_CLI`` — the fix at the predicate propagates to the
    stamp."""
    backend._external_cli_specs.clear()
    handler = _make_handler(backend)
    assert await handler._lookup_role("cli-1") is TeamRole.EXTERNAL_CLI


@pytest.mark.asyncio
@pytest.mark.level0
async def test_lookup_role_unknown_member_defaults_to_teammate(backend):
    """A member name with no DB row resolves to ``TEAMMATE``."""
    handler = _make_handler(backend)
    assert await handler._lookup_role("nobody") is TeamRole.TEAMMATE
