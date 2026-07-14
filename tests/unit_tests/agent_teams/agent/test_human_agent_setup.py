# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify Human Agent role-aware spawn / rail / role inference paths.

Phase 2 of HITT brings up a real DeepAgent for every registered human
agent so users can drive it through ``HumanAgentInbox``. This test
suite locks down the structural invariants that make that work:

* spawn payload carries ``role=human_agent`` (not ``teammate``) so the
  receiving process configures the right rails / tool surface;
* the configurator skips ``FirstIterationGate`` for human agents (no
  task loop, no autonomous mailbox poll);
* ``TeamToolApprovalRail`` continues to attach to teammates only and
  never to human agents (their tool calls are user-authorized);
* ``spawn_manager.build_context_from_db`` reads the role straight off
  the persisted ``team_member.role`` column so cold-recovery picks up
  dynamically-spawned humans without depending on the leader's
  in-memory HITT roster.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.constants import HUMAN_AGENT_MEMBER_NAME
from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.rails import (
    FirstIterationGate,
    TeamPolicyRail,
    TeamToolApprovalRail,
    TeamToolRail,
)
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.team import TeamBackend


@pytest_asyncio.fixture
async def db():
    token = set_session_id("setup_session")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
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
async def team_backend(db, messager):
    """Backend pre-seeded with one human-agent member name."""
    backend = TeamBackend(
        team_name="hitt_team",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
        predefined_members=[
            TeamMemberSpec(
                member_name="human_alice",
                display_name="Alice",
                role_type=TeamRole.HUMAN_AGENT,
                persona="user avatar",
            ),
        ],
        enable_hitt=True,
    )
    yield backend


def _human_agent_team_spec() -> TeamAgentSpec:
    """Construct a leader team spec that explicitly declares the default
    ``human_agent`` HUMAN_AGENT predefined member.
    """
    return TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="hitt_team",
        enable_hitt=True,
        predefined_members=[
            TeamMemberSpec(
                member_name=HUMAN_AGENT_MEMBER_NAME,
                display_name="Human",
                role_type=TeamRole.HUMAN_AGENT,
                persona="Default human collaborator",
            ),
        ],
    )


def _human_agent_team() -> TeamAgent:
    """Build the leader runtime so we can inspect predefined_members."""
    return _human_agent_team_spec().build()


@pytest.mark.level0
def test_predefined_human_member_carries_human_agent_role() -> None:
    """``TeamMemberSpec.role_type`` survives the build path so spawn
    payloads materialize the right role.
    """
    leader = _human_agent_team()

    member = next(m for m in leader.spec.predefined_members if m.member_name == HUMAN_AGENT_MEMBER_NAME)
    ctx = leader.build_member_context(member)
    assert ctx.role == TeamRole.HUMAN_AGENT


@pytest.mark.level0
def test_human_agent_spawn_payload_marks_role() -> None:
    """The cross-process payload labels the spawned member as ``human_agent``."""
    leader = _human_agent_team()
    member = next(m for m in leader.spec.predefined_members if m.member_name == HUMAN_AGENT_MEMBER_NAME)
    ctx = leader.build_member_context(member)

    payload = leader.build_spawn_payload(ctx)

    assert payload["coordination"]["role"] == "human_agent"
    assert payload["coordination"]["member_name"] == HUMAN_AGENT_MEMBER_NAME


@pytest.mark.asyncio
@pytest.mark.level0
async def test_human_agent_role_restored_from_member_row(team_backend, db):
    """spawn_manager reads ``TeamRole.HUMAN_AGENT`` straight off the
    persisted ``team_member.role`` column.

    This is the cold-recovery contract: a fresh leader process must
    rebuild a human agent's runtime profile (tools / rails / prompt
    sections) without depending on the previous leader process's
    in-memory HITT roster — that legacy hack lost any dynamically
    spawned human across restarts.
    """
    from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
    from openjiuwen.core.single_agent import AgentCard

    # Seed an UNSTARTED human agent row, mirroring what build_team /
    # spawn_human_agent would do — pass the role explicitly so it lands
    # on the persisted row.
    await db.team.create_team(
        team_name="hitt_team",
        display_name="HITT",
        leader_member_name="team_leader",
    )
    await team_backend.spawn_member(
        member_name="human_alice",
        display_name="Alice",
        agent_card=AgentCard(),
        desc="user avatar",
        role=TeamRole.HUMAN_AGENT,
    )

    leader = _human_agent_team()
    leader._configurator._infra.team_backend = team_backend
    spawn_manager = SpawnManager(
        state=leader._state,
        configurator=leader._configurator,
        team_agent_getter=lambda: leader,
    )

    ctx = await spawn_manager.build_context_from_db("human_alice")
    assert ctx is not None
    assert ctx.role == TeamRole.HUMAN_AGENT


@pytest.mark.asyncio
@pytest.mark.level0
async def test_teammate_role_inferred_from_backend(team_backend, db):
    """Plain teammates retain the TEAMMATE role even when the team has
    other human-agent members registered."""
    from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
    from openjiuwen.core.single_agent import AgentCard

    await db.team.create_team(
        team_name="hitt_team",
        display_name="HITT",
        leader_member_name="team_leader",
    )
    await team_backend.spawn_member(
        member_name="dev-1",
        display_name="Dev 1",
        agent_card=AgentCard(),
        desc="backend dev",
    )

    leader = _human_agent_team()
    leader._configurator._infra.team_backend = team_backend
    spawn_manager = SpawnManager(
        state=leader._state,
        configurator=leader._configurator,
        team_agent_getter=lambda: leader,
    )

    ctx = await spawn_manager.build_context_from_db("dev-1")
    assert ctx is not None
    assert ctx.role == TeamRole.TEAMMATE


def _build_human_agent_runtime() -> TeamAgent:
    """Materialize a configured ``TeamAgent`` whose ``role=HUMAN_AGENT``."""
    spec = _human_agent_team_spec()
    leader = spec.build()
    member = next(m for m in spec.predefined_members if m.role_type == TeamRole.HUMAN_AGENT)
    ctx = leader.build_member_context(member)

    from openjiuwen.core.single_agent import AgentCard

    card = AgentCard(
        id=f"hitt_team_{member.member_name}",
        name=member.member_name,
        description=member.persona,
    )
    avatar = TeamAgent(card)
    avatar.configure(spec, ctx)
    return avatar


@pytest.mark.level0
def test_human_agent_skips_first_iteration_gate() -> None:
    """Human agents must not hold a FirstIterationGate — they have no
    task loop the gate would open."""
    avatar = _build_human_agent_runtime()
    rails = list(avatar.harness.inner_agent._pending_rails)

    assert not any(isinstance(r, FirstIterationGate) for r in rails)
    assert avatar.resources.first_iter_gate is None


@pytest.mark.level0
def test_human_agent_attaches_team_tool_and_policy_rails() -> None:
    """TeamToolRail (filtered by role) and TeamPolicyRail must still be
    on the avatar so it gets its tool surface and prompt sections."""
    avatar = _build_human_agent_runtime()
    rails = list(avatar.harness.inner_agent._pending_rails)

    assert any(isinstance(r, TeamToolRail) for r in rails)
    assert any(isinstance(r, TeamPolicyRail) for r in rails)


@pytest.mark.level0
def test_human_agent_never_attaches_tool_approval_rail() -> None:
    """Tool approval is a teammate-only flow; the avatar's tool calls are
    user-authorized and must never round-trip to the leader for sign-off.
    """
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(approval_required_tools=["write_file"])},
        team_name="hitt_team",
        enable_hitt=True,
        predefined_members=[
            TeamMemberSpec(
                member_name=HUMAN_AGENT_MEMBER_NAME,
                display_name="Human",
                role_type=TeamRole.HUMAN_AGENT,
                persona="Default human collaborator",
            ),
        ],
    )
    leader = spec.build()
    member = next(m for m in spec.predefined_members if m.role_type == TeamRole.HUMAN_AGENT)
    ctx = leader.build_member_context(member)

    from openjiuwen.core.single_agent import AgentCard

    card = AgentCard(
        id=f"hitt_team_{member.member_name}",
        name=member.member_name,
        description=member.persona,
    )
    avatar = TeamAgent(card)
    avatar.configure(spec, ctx)

    rails = list(avatar.harness.inner_agent._pending_rails)
    assert not any(isinstance(r, TeamToolApprovalRail) for r in rails)
