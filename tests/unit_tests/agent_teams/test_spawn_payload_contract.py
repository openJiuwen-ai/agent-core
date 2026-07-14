# coding: utf-8
"""Contract tests for the cross-process spawn payload wire format.

The output of ``SpawnPayloadBuilder.build_spawn_payload`` is the wire
contract consumed by ``TeamAgent.from_spawn_payload`` running in a
spawned process. Adding/removing/renaming any key here will silently
break already-running children — these tests freeze the schema.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.agent.payload import SpawnPayloadBuilder
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)


def _make_builder() -> SpawnPayloadBuilder:
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="t")
    team_spec = TeamSpec(team_name="t", display_name="t-display", leader_member_name="leader")
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", team_spec=team_spec)
    return SpawnPayloadBuilder(spec, ctx)


def _make_member_ctx(member_name: str) -> TeamRuntimeContext:
    team_spec = TeamSpec(team_name="t", display_name="t-display", leader_member_name="leader")
    return TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name=member_name,
        persona="worker persona",
        team_spec=team_spec,
    )


def test_spawn_payload_top_level_keys_are_frozen():
    """build_spawn_payload must emit exactly {coordination, query} at top level."""
    builder = _make_builder()
    ctx = _make_member_ctx("worker_a")

    payload = builder.build_spawn_payload(ctx)

    assert set(payload.keys()) == {"coordination", "query"}


def test_spawn_payload_coordination_keys_are_frozen():
    """Coordination block keys form a hard wire contract."""
    builder = _make_builder()
    ctx = _make_member_ctx("worker_a")

    payload = builder.build_spawn_payload(ctx, initial_message="hello")

    coordination = payload["coordination"]
    assert set(coordination.keys()) == {
        "team_name",
        "display_name",
        "leader_member_name",
        "member_name",
        "role",
        "persona",
        "transport",
    }
    assert coordination["team_name"] == "t"
    assert coordination["display_name"] == "t-display"
    assert coordination["leader_member_name"] == "leader"
    assert coordination["member_name"] == "worker_a"
    assert coordination["role"] == "teammate"
    assert coordination["persona"] == "worker persona"
    assert coordination["transport"] is None  # no messager_config in this builder
    assert payload["query"] == "hello"


def test_spawn_payload_query_default_when_no_initial_message():
    builder = _make_builder()
    ctx = _make_member_ctx("worker_a")

    payload = builder.build_spawn_payload(ctx)

    assert payload["query"] == "Join the team and wait for your first assignment."


def test_spawn_payload_with_empty_team_spec():
    """When team_spec is None, team_name/display_name fall back to empty strings."""
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="t")
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", team_spec=None)
    builder = SpawnPayloadBuilder(spec, ctx)
    member_ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="worker",
        team_spec=None,
    )

    payload = builder.build_spawn_payload(member_ctx)

    coordination = payload["coordination"]
    assert coordination["team_name"] == ""
    assert coordination["display_name"] == ""
    assert coordination["leader_member_name"] is None


def test_member_port_allocation_is_stable():
    """Repeated lookups for the same member_name must return the same port."""
    builder = _make_builder()
    # No messager_config -> returns None; we can't verify ports without one
    # but we can verify the absence path.
    assert builder.build_member_messager_config("a") is None
    assert builder.build_member_messager_config("a") is None


def test_build_spawn_config_payload_has_spec_and_context():
    """Spawn config payload contract: must contain {spec, context} as JSON dicts."""
    pytest.importorskip("openjiuwen.core.runner.runner")
    builder = _make_builder()
    ctx = _make_member_ctx("worker_a")

    spawn_config = builder.build_spawn_config(ctx)

    assert set(spawn_config.payload.keys()) == {"spec", "context"}
    assert isinstance(spawn_config.payload["spec"], dict)
    assert isinstance(spawn_config.payload["context"], dict)
    assert spawn_config.payload["context"]["member_name"] == "worker_a"
    assert spawn_config.payload["context"]["role"] == "teammate"
