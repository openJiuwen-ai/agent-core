# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for bridge avatar agent-configurator wiring.

Covers:
- ``_resolve_team_mode`` ignores BRIDGE_AGENT predefined entries the
  same way it ignores HUMAN_AGENT — they are avatar-roster declarations
  and don't force the team into "predefined" mode.
- ``create_team_tools(role="bridge_agent", ...)`` yields the exact same
  tool set as ``role="teammate"`` (bridge avatars are full teammates
  locally).
- ``spawn_bridge_agent`` persists ``role='bridge_agent'`` on the member
  row so ``SpawnManager.build_context_from_db`` recovers the BRIDGE_AGENT
  role from the DB (symmetric to HUMAN_AGENT).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.agent_configurator import _resolve_team_mode
from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.blueprint import (
    LeaderSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import (
    BridgeMemberSpec,
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import create_team_tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    token = set_session_id("bridge_setup_session")
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


@pytest_asyncio.fixture
async def backend(db, messager):
    yield TeamBackend(
        team_name="bt",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
        enable_bridge=True,
    )


# ---------------------------------------------------------------------------
# _resolve_team_mode
# ---------------------------------------------------------------------------


def _spec_with_predefined(members) -> TeamAgentSpec:
    return TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="t",
        leader=LeaderSpec(),
        enable_bridge=True,
        enable_hitt=True,
        predefined_members=members,
    )


@pytest.mark.level0
def test_resolve_team_mode_bridge_only_stays_default():
    """A team whose only predefined members are bridges keeps the
    ``default`` mode — the leader retains ``spawn_member`` so it can
    still build out the regular teammate roster at runtime."""
    spec = _spec_with_predefined(
        [
            BridgeMemberSpec(
                member_name="codex",
                display_name="Codex",
                persona="r",
            ),
        ],
    )
    assert _resolve_team_mode(spec) == "default"


@pytest.mark.level0
def test_resolve_team_mode_human_only_stays_default():
    spec = _spec_with_predefined(
        [
            TeamMemberSpec(
                member_name="alice",
                display_name="Alice",
                persona="x",
                role_type=TeamRole.HUMAN_AGENT,
            ),
        ],
    )
    assert _resolve_team_mode(spec) == "default"


@pytest.mark.level0
def test_resolve_team_mode_mixed_bridge_human_still_default():
    spec = _spec_with_predefined(
        [
            BridgeMemberSpec(
                member_name="codex",
                display_name="Codex",
                persona="r",
            ),
            TeamMemberSpec(
                member_name="alice",
                display_name="Alice",
                persona="x",
                role_type=TeamRole.HUMAN_AGENT,
            ),
        ],
    )
    assert _resolve_team_mode(spec) == "default"


@pytest.mark.level0
def test_resolve_team_mode_with_teammate_predefined_is_hybrid():
    spec = _spec_with_predefined(
        [
            TeamMemberSpec(
                member_name="alice",
                display_name="Alice",
                persona="x",
                role_type=TeamRole.TEAMMATE,
            ),
        ],
    )
    assert _resolve_team_mode(spec) == "hybrid"


@pytest.mark.level0
def test_resolve_team_mode_explicit_override_wins():
    spec = _spec_with_predefined(
        [
            BridgeMemberSpec(
                member_name="codex",
                display_name="Codex",
                persona="r",
            ),
        ],
    )
    spec.team_mode = "hybrid"
    assert _resolve_team_mode(spec) == "hybrid"


# ---------------------------------------------------------------------------
# create_team_tools — bridge_agent tool set equality with teammate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_agent_tool_set_matches_teammate(backend):
    bridge_tools = create_team_tools(role="bridge_agent", agent_team=backend)
    teammate_tools = create_team_tools(role="teammate", agent_team=backend)
    bridge_names = sorted(t.card.name for t in bridge_tools if t.card is not None)
    teammate_names = sorted(t.card.name for t in teammate_tools if t.card is not None)
    assert bridge_names == teammate_names
    # The bridge avatar must have the full teammate kit (send_message,
    # claim_task, ...) — without these it cannot schedule.
    assert "send_message" in bridge_names
    assert "claim_task" in bridge_names
    assert "view_task" in bridge_names


@pytest.mark.asyncio
@pytest.mark.level0
async def test_bridge_agent_has_no_consult_external_tool(backend):
    """Per the design contract, bridge avatars do NOT get a dedicated
    'consult external' tool — all communication with the remote
    happens automatically on the mailbox forward path."""
    tools = create_team_tools(role="bridge_agent", agent_team=backend)
    names = {t.card.name for t in tools if t.card is not None}
    assert "consult_external_agent" not in names


# ---------------------------------------------------------------------------
# spawn_manager role inference (smoke)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_manager_role_inference_for_bridge(db, messager):
    """End-to-end smoke: after ``spawn_bridge_agent`` the member exists
    on the team, the bridge index agrees, and the ``bridge_agent`` role
    is persisted on the member row — the source ``SpawnManager`` reads
    back via ``TeamRole(teammate.role)`` to rebuild the avatar context."""
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
    result = await backend.spawn_bridge_agent(
        member_name="codex",
        display_name="Codex",
        persona="r",
    )
    assert result.ok
    assert backend.is_bridge_agent("codex") is True
    assert await backend.is_human_agent("codex") is False
    # The persisted role is what SpawnManager.build_context_from_db reads
    # to assign BRIDGE_AGENT on cold recovery — assert it directly.
    member = await backend.get_member("codex")
    assert member is not None
    assert member.role == TeamRole.BRIDGE_AGENT.value
