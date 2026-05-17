# coding: utf-8
"""Tests for TeamAgent configuration and spawn payloads."""
from __future__ import annotations

import json

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.prompts import TeamSectionName
from openjiuwen.agent_teams.rails import TeamPolicyRail
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
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


def _dummy_agents(**overrides) -> dict[str, DeepAgentSpec]:
    """Build a minimal agents dict for unit tests (no real LLM)."""
    defaults = dict(overrides)
    return {"leader": DeepAgentSpec(**defaults)}


@pytest.mark.level0
def test_team_agent_leader_policy() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
    ).build()

    assert leader.role == TeamRole.LEADER
    # TeamLeader policy is injected by TeamPolicyRail as a PromptSection
    # before each model call, not stored in deep_config.system_prompt
    # (which stays None).
    policy_rail = next(
        r for r in leader.harness.inner_agent._pending_rails if isinstance(r, TeamPolicyRail)
    )
    role_section = next(
        s for s in policy_rail._static_sections if s.name == TeamSectionName.ROLE
    )
    assert "TeamLeader" in role_section.render("cn")


@pytest.mark.level0
def test_spawn_payload_contains_member_identity() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
        transport=TransportSpec(type="pyzmq", params={
            "team_id": "delivery-team",
            "node_id": "leader",
            "direct_addr": "tcp://127.0.0.1:19001",
            "pubsub_publish_addr": "tcp://127.0.0.1:19100",
            "pubsub_subscribe_addr": "tcp://127.0.0.1:19101",
        }),
    ).build()
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
    leader = TeamAgentSpec(
        agents=_dummy_agents(workspace=None),
        team_name="delivery",
        transport=TransportSpec(type="pyzmq", params={
            "team_id": "delivery-team",
            "node_id": "leader",
            "direct_addr": "tcp://127.0.0.1:19001",
            "pubsub_publish_addr": "tcp://127.0.0.1:19100",
            "pubsub_subscribe_addr": "tcp://127.0.0.1:19101",
        }),
    ).build()
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


@pytest.mark.level0
def test_setup_agent_builds_leader_member_handle() -> None:
    """Leader gets a TeamMember handle eagerly during configure().

    Regression guard: the leader's status / execution transitions are
    silent no-ops unless ``_state.team_member`` is populated. The handle
    must be built for the leader just like for teammates, not deferred
    to a callback path that never fires for a BUSY-registered leader.
    """
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
    ).build()

    assert leader.role == TeamRole.LEADER
    handle = leader._state.team_member
    assert handle is not None
    assert handle.member_name == leader.member_name


@pytest.mark.asyncio
@pytest.mark.level1
async def test_setup_agent_builds_teammate_member_handle() -> None:
    """Teammate still gets its TeamMember handle from configure() (no regression)."""
    leader = TeamAgentSpec(
        agents=_dummy_agents(workspace=None),
        team_name="delivery",
        transport=TransportSpec(
            type="pyzmq",
            params={
                "team_id": "delivery-team",
                "node_id": "leader",
                "direct_addr": "tcp://127.0.0.1:19002",
                "pubsub_publish_addr": "tcp://127.0.0.1:19102",
                "pubsub_subscribe_addr": "tcp://127.0.0.1:19103",
            },
        ),
    ).build()
    ctx = leader.build_member_context(
        TeamMemberSpec(
            member_name="be-1",
            display_name="Backend Expert",
            role_type=TeamRole.TEAMMATE,
            persona="严谨的后端架构师",
        )
    )
    spawn_config = leader.build_spawn_config(ctx)

    teammate = await TeamAgent.from_spawn_payload(spawn_config.payload)

    assert teammate.role == TeamRole.TEAMMATE
    handle = teammate._state.team_member
    assert handle is not None
    assert handle.member_name == "be-1"
