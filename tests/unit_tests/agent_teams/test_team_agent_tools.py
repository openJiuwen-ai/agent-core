# coding: utf-8
"""Tests for role-based tool registration."""
from __future__ import annotations

from openjiuwen.agent_teams import create_agent_team
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    TransportSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamRole,
    TeamRuntimeContext,
)


def _tool_names(agent) -> set[str]:
    """Extract registered tool names from the agent's
    ability manager."""
    return set(agent.deep_agent.ability_manager._tools.keys())


def _dummy_agents() -> dict[str, DeepAgentSpec]:
    """Build a minimal agents dict for unit tests (no real LLM)."""
    return {"leader": DeepAgentSpec()}


_PYZMQ_TRANSPORT = TransportSpec(type="pyzmq", params={
    "team_id": "test",
    "node_id": "team_leader",
})


# === Leader gets full tool set ===


def test_leader_gets_management_tools():
    """Leader should have team management and
    messaging tools."""
    leader = create_agent_team(
        _dummy_agents(),
        team_name="test",
        transport=_PYZMQ_TRANSPORT,
    )
    names = _tool_names(leader)
    assert "create_task" in names
    assert "build_team" in names
    assert "spawn_member" in names
    assert "approve_tool" in names
    assert "send_message" in names
    assert "view_task" in names


# === Teammate gets execution-only tools ===


def test_teammate_gets_execution_tools():
    """Teammate should have task execution and
    messaging tools but not management-only tools."""
    leader = create_agent_team(
        _dummy_agents(),
        team_name="test",
        transport=_PYZMQ_TRANSPORT,
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_id="dev-1",
        name="Dev",
        persona="dev",
        team_spec=leader._ctx.team_spec,
        messager_config=leader._ctx.messager_config,
        db_config=leader._ctx.db_config,
    )
    card = leader.card.model_copy(update={
        "id": "dev-1",
        "name": "Dev",
        "description": "Teammate: dev",
    })
    teammate = TeamAgent(card)
    teammate.configure(leader._spec, ctx)
    names = _tool_names(teammate)

    # Execution tools present
    assert "claim_task" in names
    assert "send_message" in names
    assert "view_task" in names

    # Leader-only tools absent
    assert "create_task" not in names
    assert "build_team" not in names
    assert "spawn_member" not in names


# === Manager instances are stored ===


def test_task_and_message_managers_are_stored():
    """After configuration, _task_manager and
    _message_manager should be set on the
    TeamAgent."""
    leader = create_agent_team(
        _dummy_agents(),
        team_name="test",
        transport=_PYZMQ_TRANSPORT,
    )
    assert leader._task_manager is not None
    assert leader._message_manager is not None


def test_teammate_registers_tool_approval_rail_from_deep_agent_spec():
    """Configured teammate approval tools should attach TeamToolApprovalRail."""
    leader = create_agent_team(
        {
            "leader": DeepAgentSpec(),
            "teammate": DeepAgentSpec(approval_required_tools=["send_message"]),
        },
        team_name="test",
        transport=_PYZMQ_TRANSPORT,
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_id="dev-1",
        name="Dev",
        persona="dev",
        team_spec=leader._ctx.team_spec,
        messager_config=leader._ctx.messager_config,
        db_config=leader._ctx.db_config,
    )
    card = leader.card.model_copy(update={
        "id": "dev-1",
        "name": "Dev",
        "description": "Teammate: dev",
    })
    teammate = TeamAgent(card)
    teammate.configure(leader._spec, ctx)

    rail_names = {type(r).__name__ for r in teammate.deep_agent._pending_rails}
    assert "TeamToolApprovalRail" in rail_names
