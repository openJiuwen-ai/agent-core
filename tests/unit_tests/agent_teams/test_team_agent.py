# coding: utf-8
"""Tests for TeamAgent configuration and spawn payloads."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.prompts.sections import TeamSectionName
from openjiuwen.agent_teams.rails.team_policy_rail import TeamPolicyRail
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    LeaderSpec,
    TeamAgentSpec,
    TransportSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.core.runner.spawn import SpawnAgentKind
from openjiuwen.core.session import InteractiveInput


def _dummy_agents(**overrides) -> dict[str, DeepAgentSpec]:
    """Build a minimal agents dict for unit tests (no real LLM)."""
    defaults = dict(overrides)
    return {"leader": DeepAgentSpec(**defaults)}


def _build_leader(
    agents: dict[str, DeepAgentSpec],
    *,
    team_name: str = "agent_team",
    transport: TransportSpec | None = None,
    **kwargs: Any,
) -> TeamAgent:
    spec = TeamAgentSpec(
        agents=agents,
        team_name=team_name,
        transport=transport,
        leader=kwargs.get("leader") or LeaderSpec(),
        predefined_members=kwargs.get("predefined_members") or [],
        lifecycle=kwargs.get("lifecycle", "temporary"),
        teammate_mode=kwargs.get("teammate_mode", "build_mode"),
        spawn_mode=kwargs.get("spawn_mode", "process"),
        storage=kwargs.get("storage"),
        worktree=kwargs.get("worktree"),
        metadata=kwargs.get("metadata") or {},
    )
    return spec.build()


def test_initial_leader_route_skips_interactive_input() -> None:
    agent = SimpleNamespace(role=TeamRole.LEADER, team_backend=object())

    result = TeamAgent._initial_leader_route_payloads(agent, InteractiveInput())

    assert result is None


def test_team_agent_leader_policy() -> None:
    leader = _build_leader(
        _dummy_agents(),
        team_name="delivery",
    )

    assert leader.role == TeamRole.LEADER
    native = leader.harness._native
    rails = list(native._pending_rails) + list(native._registered_rails)
    team_rail = next(r for r in rails if isinstance(r, TeamPolicyRail))
    role_section = next(
        s for s in team_rail._static_sections if s.name == TeamSectionName.ROLE
    )
    assert "TeamLeader" in role_section.render("cn")


def test_spawn_payload_contains_member_identity() -> None:
    leader = _build_leader(
        _dummy_agents(),
        team_name="delivery",
        transport=TransportSpec(type="pyzmq", params={
            "team_id": "delivery-team",
            "node_id": "leader",
            "direct_addr": "tcp://127.0.0.1:19001",
            "pubsub_publish_addr": "tcp://127.0.0.1:19100",
            "pubsub_subscribe_addr": "tcp://127.0.0.1:19101",
        }),
    )
    ctx = leader.build_member_context(TeamMemberSpec(
        member_name="fe-1",
        display_name="Frontend Expert",
        role_type=TeamRole.TEAMMATE,
        desc="追求交互质量的前端工程师",
    ))

    payload = leader.build_spawn_payload(
        ctx,
        initial_message="Review the design system impact.",
    )

    assert payload["coordination"]["role"] == "teammate"
    assert payload["coordination"]["desc"] == "追求交互质量的前端工程师"
    assert payload["coordination"]["transport"]["node_id"] == "fe-1"
    assert payload["query"] == "Review the design system impact."


@pytest.mark.asyncio
async def test_spawn_config_contains_serializable_team_agent_payload() -> None:
    leader = _build_leader(
        _dummy_agents(workspace=None),
        team_name="delivery",
        transport=TransportSpec(type="pyzmq", params={
            "team_id": "delivery-team",
            "node_id": "leader",
            "direct_addr": "tcp://127.0.0.1:19001",
            "pubsub_publish_addr": "tcp://127.0.0.1:19100",
            "pubsub_subscribe_addr": "tcp://127.0.0.1:19101",
        }),
    )
    ctx = leader.build_member_context(TeamMemberSpec(
        member_name="be-1",
        display_name="Backend Expert",
        role_type=TeamRole.TEAMMATE,
        desc="严谨的后端架构师",
    ))

    spawn_config = leader.build_spawn_config(ctx)

    assert spawn_config.agent_kind == SpawnAgentKind.TEAM_AGENT
    assert spawn_config.runner_config is not None
    assert "spec" in spawn_config.payload
    assert "context" in spawn_config.payload
    assert spawn_config.payload["context"]["role"] == "teammate"
    assert spawn_config.payload["context"]["messager_config"]["node_id"] == "be-1"

    json.dumps(spawn_config.model_dump(mode="json"))

    teammate = await TeamAgent.from_spawn_payload(spawn_config.payload)

    assert teammate.role == TeamRole.TEAMMATE
    assert teammate.card.name == "be-1"
    assert teammate.runtime_context is not None
    assert teammate.runtime_context.messager_config is not None
    assert teammate.runtime_context.messager_config.node_id == "be-1"


def test_runtime_context_roundtrips_with_pydantic_serialization() -> None:
    context = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader-1",
        desc="pm",
        team_spec=TeamSpec(team_name="demo", display_name="demo"),
    )

    restored = TeamRuntimeContext.model_validate(context.model_dump(mode="json"))

    assert restored.role == TeamRole.LEADER
    assert restored.member_name == "leader-1"
    assert restored.desc == "pm"
