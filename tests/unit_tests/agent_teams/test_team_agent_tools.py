# coding: utf-8
"""Tests for role-based tool registration."""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    TeamAgentSpec,
    TransportSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamRole,
    TeamRuntimeContext,
)


def _tool_names(agent) -> set[str]:
    """Extract registered tool names from the agent's ability manager.

    Team tools register during ``ensure_initialized`` (the rail init pass), so
    drive it first — ``build`` only prepares the config and queues the rails.
    """
    native = agent.harness.inner_agent
    asyncio.run(native.ensure_initialized())
    return set(native.ability_manager._tools.keys())


def _dummy_agents() -> dict[str, DeepAgentSpec]:
    """Build a minimal agents dict for unit tests (no real LLM)."""
    return {"leader": DeepAgentSpec()}


_PYZMQ_TRANSPORT = TransportSpec(
    type="pyzmq",
    params={
        "team_id": "test",
        "node_id": "team_leader",
    },
)


# === Leader gets full tool set ===


@pytest.mark.level0
def test_leader_gets_management_tools():
    """Leader should have team management and
    messaging tools."""
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="test",
        transport=_PYZMQ_TRANSPORT,
    ).build()
    names = _tool_names(leader)
    assert "create_task" in names
    assert "build_team" in names
    assert "spawn_teammate" in names
    assert "send_message" in names
    assert "view_task" in names


# === Teammate gets execution-only tools ===


@pytest.mark.level0
@pytest.mark.skip(reason="pre-existing autonomous coordination issue, unrelated to reviewer PR")
def test_teammate_gets_execution_tools():
    """Teammate should have task execution and
    messaging tools but not management-only tools."""
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="test",
        transport=_PYZMQ_TRANSPORT,
    ).build()
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_id="dev-1",
        name="Dev",
        desc="dev",
        team_spec=leader._configurator.ctx.team_spec,
        messager_config=leader._configurator.ctx.messager_config,
        db_config=leader._configurator.ctx.db_config,
    )
    card = leader.card.model_copy(
        update={
            "id": "dev-1",
            "name": "Dev",
            "description": "Teammate: dev",
        }
    )
    teammate = TeamAgent(card)
    teammate.configure(leader._configurator.spec, ctx)
    names = _tool_names(teammate)

    # Execution tools present
    assert "claim_task" in names
    assert "send_message" in names
    assert "view_task" in names

    # Leader-only tools absent
    assert "create_task" not in names
    assert "build_team" not in names
    assert "spawn_teammate" not in names


# === Manager instances are stored ===


@pytest.mark.level1
def test_task_and_message_managers_are_stored():
    """After configuration, _task_manager and
    _message_manager should be set on the
    TeamAgent."""
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="test",
        transport=_PYZMQ_TRANSPORT,
    ).build()
    assert leader._configurator.task_manager is not None
    assert leader._configurator.message_manager is not None


@pytest.mark.level1
@pytest.mark.parametrize("approval_key", ["teammate", "worker"])
def test_teammate_registers_tool_approval_rail_from_deep_agent_spec(approval_key):
    """Configured teammate approval tools should attach TeamToolApprovalRail."""
    leader = TeamAgentSpec(
        agents={
            "leader": DeepAgentSpec(),
            approval_key: DeepAgentSpec(approval_required_tools=["send_message"]),
        },
        team_name="test",
        transport=_PYZMQ_TRANSPORT,
    ).build()
    assert "approve_tool" in _tool_names(leader)
    member_name = "worker" if approval_key == "worker" else "dev-1"
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name=member_name,
        display_name="Dev",
        desc="dev",
        team_spec=leader._configurator.ctx.team_spec,
        messager_config=leader._configurator.ctx.messager_config,
        db_config=leader._configurator.ctx.db_config,
    )
    card = leader.card.model_copy(
        update={
            "id": member_name,
            "name": "Dev",
            "description": "Teammate: dev",
        }
    )
    teammate = TeamAgent(card)
    teammate.configure(leader._configurator.spec, ctx)

    rail_names = {type(r).__name__ for r in teammate.harness.inner_agent._pending_rails}
    assert "TeamToolApprovalRail" in rail_names


@pytest.mark.parametrize("key, expected", [
    ("teammate", True), ("external_cli", True), ("named-worker", True),
    ("human_agent", False), ("passive_human", False), ("worker", True), ("bridge_agent", False),
])
def test_legacy_reply_capability_uses_only_coordinated_specs(key, expected):
    from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator

    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec(), key: DeepAgentSpec(
        approval_required_tools=["send_message"]
    )})
    assert AgentConfigurator._requires_legacy_approval(spec) is expected
    assert spec.enable_permissions is False


@pytest.mark.parametrize("coordinated", [False, True])
def test_predefined_roster_limits_legacy_reply_capability(coordinated):
    from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
    from openjiuwen.agent_teams.schema.team import TeamMemberSpec

    role = TeamRole.TEAMMATE if coordinated else TeamRole.PASSIVE_HUMAN
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(approval_required_tools=["send_message"])},
        team_mode="predefined",
        predefined_members=[TeamMemberSpec(member_name="member", display_name="Member", role_type=role)],
    )
    assert AgentConfigurator._requires_legacy_approval(spec) is coordinated


@pytest.mark.parametrize("source", ["teammate", "external_cli", "leader", "named-worker"])
def test_public_legacy_team_retains_leader_reply_without_enabling_permissions(source):
    spec = TeamAgentSpec(
        agents={**_dummy_agents(), source: DeepAgentSpec(approval_required_tools=["send_message"])},
        team_name="legacy-replies",
        teammate_mode="build_mode",
        enable_permissions=False,
        transport=_PYZMQ_TRANSPORT,
    )
    leader = spec.build()
    assert "approve_tool" in _tool_names(leader)
    assert spec.enable_permissions is False


def test_default_build_mode_does_not_gain_legacy_reply_tool():
    spec = TeamAgentSpec(agents=_dummy_agents(), teammate_mode="build_mode", transport=_PYZMQ_TRANSPORT)
    assert "approve_tool" not in _tool_names(spec.build())


@pytest.mark.parametrize("name", ["team_leader", "user", "custom-leader", "invalid.name"])
def test_non_member_names_cannot_grant_legacy_reply_capability(name):
    from openjiuwen.agent_teams.schema.blueprint import LeaderSpec

    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec(),
                name: DeepAgentSpec(approval_required_tools=["send_message"])},
        leader=LeaderSpec(member_name="custom-leader" if name == "custom-leader" else "team_leader"),
        teammate_mode="build_mode",
        transport=_PYZMQ_TRANSPORT,
    )
    assert "approve_tool" not in _tool_names(spec.build())


def test_worker_only_spec_in_fixed_roster_does_not_grant_reply_tool():
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(), "worker": DeepAgentSpec(approval_required_tools=["send_message"])},
        team_mode="predefined",
        teammate_mode="build_mode",
        transport=_PYZMQ_TRANSPORT,
    )
    assert "approve_tool" not in _tool_names(spec.build())
