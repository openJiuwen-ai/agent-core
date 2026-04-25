# coding: utf-8
"""Tests for TeamAgent configuration and spawn payloads."""
from __future__ import annotations

import json

import pytest

from openjiuwen.agent_teams import create_agent_team
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.agent.team_rail import TeamRail, TeamSectionName
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    TransportSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.core.runner.spawn import SpawnAgentKind


def _dummy_agents(**overrides) -> dict[str, DeepAgentSpec]:
    """Build a minimal agents dict for unit tests (no real LLM)."""
    defaults = dict(overrides)
    return {"leader": DeepAgentSpec(**defaults)}


@pytest.mark.level0
def test_team_agent_leader_policy() -> None:
    leader = create_agent_team(
        _dummy_agents(),
        team_name="delivery",
    )

    assert leader.role == TeamRole.LEADER
    # TeamLeader policy is injected by TeamRail as a PromptSection before each
    # model call, not stored in deep_config.system_prompt (which stays None).
    team_rail = next(
        r for r in leader.deep_agent._pending_rails if isinstance(r, TeamRail)
    )
    role_section = next(
        s for s in team_rail._static_sections if s.name == TeamSectionName.ROLE
    )
    assert "TeamLeader" in role_section.render("cn")


@pytest.mark.level0
def test_spawn_payload_contains_member_identity() -> None:
    leader = create_agent_team(
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
        persona="追求交互质量的前端工程师",
    ))

    payload = leader.build_spawn_payload(
        ctx,
        initial_message="Review the design system impact.",
    )

    assert payload["coordination"]["role"] == "teammate"
    assert payload["coordination"]["persona"] == "追求交互质量的前端工程师"
    assert payload["coordination"]["transport"]["node_id"] == "fe-1"
    assert payload["query"] == "Review the design system impact."


@pytest.mark.asyncio
@pytest.mark.level1
async def test_spawn_config_contains_serializable_team_agent_payload() -> None:
    leader = create_agent_team(
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
        persona="严谨的后端架构师",
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


@pytest.mark.level1
def test_runtime_context_roundtrips_with_pydantic_serialization() -> None:
    context = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader-1",
        persona="pm",
        team_spec=TeamSpec(team_name="demo", display_name="demo"),
    )

    restored = TeamRuntimeContext.model_validate(context.model_dump(mode="json"))

    assert restored.role == TeamRole.LEADER
    assert restored.member_name == "leader-1"
    assert restored.persona == "pm"
