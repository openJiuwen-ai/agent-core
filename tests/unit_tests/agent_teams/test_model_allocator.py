# coding: utf-8
"""Unit tests for model allocator behavior.

Covers ``RoundRobinModelAllocator`` rotation, the ``build_model_allocator``
factory's pool-vs-no-pool branching, and ``ModelPoolEntry`` materialization
into ``TeamModelConfig``.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.agent.model_allocator import (
    ByModelNameAllocator,
    RoundRobinModelAllocator,
    build_model_allocator,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import ModelPoolEntry, TeamSpec


def _make_pool(n: int) -> list[ModelPoolEntry]:
    return [
        ModelPoolEntry(
            model_name=f"m{i}",
            api_key=f"k{i}",
            api_base_url=f"http://h{i}",
            api_provider="OpenAI",
        )
        for i in range(n)
    ]


def _make_named_entry(name: str, suffix: str) -> ModelPoolEntry:
    return ModelPoolEntry(
        model_name=name,
        api_key=f"k-{suffix}",
        api_base_url=f"http://{suffix}",
        api_provider="OpenAI",
    )


def test_round_robin_allocator_rotates_through_pool():
    pool = _make_pool(3)
    allocator = RoundRobinModelAllocator(pool)
    names = [allocator.allocate().model_request_config.model_name for _ in range(7)]
    assert names == ["m0", "m1", "m2", "m0", "m1", "m2", "m0"]


def test_round_robin_allocator_returns_none_when_pool_empty():
    allocator = RoundRobinModelAllocator([])
    assert allocator.allocate() is None
    assert allocator.allocate() is None


def test_model_pool_entry_assigns_unique_model_id_per_instance():
    a = ModelPoolEntry(
        model_name="m",
        api_key="k",
        api_base_url="http://x",
        api_provider="OpenAI",
    )
    b = ModelPoolEntry(
        model_name="m",
        api_key="k",
        api_base_url="http://x",
        api_provider="OpenAI",
    )
    assert a.model_id != b.model_id


def test_model_pool_entry_to_team_model_config_carries_credentials():
    entry = ModelPoolEntry(
        model_name="m1",
        api_key="secret",
        api_base_url="http://endpoint",
        api_provider="OpenAI",
    )
    cfg = entry.to_team_model_config()
    assert cfg.model_client_config.api_key == "secret"
    assert cfg.model_client_config.api_base == "http://endpoint"
    assert cfg.model_client_config.client_id == entry.model_id
    assert cfg.model_request_config.model_name == "m1"


def test_model_pool_entry_metadata_fills_client_and_request_configs():
    entry = ModelPoolEntry(
        model_name="m1",
        api_key="secret",
        api_base_url="http://endpoint",
        api_provider="OpenAI",
        metadata={
            "client": {
                "timeout": 30.0,
                "verify_ssl": False,
                "max_retries": 5,
            },
            "request": {
                "temperature": 0.2,
                "top_p": 0.9,
                "max_tokens": 1024,
            },
        },
    )
    cfg = entry.to_team_model_config()

    client = cfg.model_client_config
    assert client.timeout == 30.0
    assert client.verify_ssl is False
    assert client.max_retries == 5

    request = cfg.model_request_config
    assert request.temperature == 0.2
    assert request.top_p == 0.9
    assert request.max_tokens == 1024


def test_model_pool_entry_explicit_fields_override_metadata():
    entry = ModelPoolEntry(
        model_name="m1",
        api_key="real-key",
        api_base_url="http://real",
        api_provider="OpenAI",
        metadata={
            "client": {
                "api_key": "shadow-key",
                "api_base": "http://shadow",
            },
            "request": {"model": "shadow-model"},
        },
    )
    cfg = entry.to_team_model_config()
    assert cfg.model_client_config.api_key == "real-key"
    assert cfg.model_client_config.api_base == "http://real"
    assert cfg.model_request_config.model_name == "m1"


def test_model_pool_entry_metadata_extra_keys_are_ignored_by_materialization():
    entry = ModelPoolEntry(
        model_name="m1",
        api_key="k",
        api_base_url="http://x",
        api_provider="OpenAI",
        metadata={"weight": 5, "tags": ["fast"]},
    )
    cfg = entry.to_team_model_config()
    assert cfg.model_client_config.api_key == "k"
    assert cfg.model_request_config.model_name == "m1"


def test_build_model_allocator_returns_round_robin_when_pool_set():
    pool = _make_pool(2)
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()})
    team_spec = TeamSpec(team_name="t", display_name="t", model_pool=pool)
    allocator = build_model_allocator(spec, team_spec)
    assert isinstance(allocator, RoundRobinModelAllocator)


def test_build_model_allocator_returns_none_without_pool():
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()})
    team_spec = TeamSpec(team_name="t", display_name="t")
    allocator = build_model_allocator(spec, team_spec)
    assert allocator is None


def test_team_spec_model_pool_round_trips_through_json():
    pool = _make_pool(2)
    ts = TeamSpec(team_name="t", display_name="t", model_pool=pool)
    restored = TeamSpec.model_validate_json(ts.model_dump_json())
    assert len(restored.model_pool) == 2
    assert restored.model_pool[0].model_name == "m0"
    assert restored.model_pool[1].api_base_url == "http://h1"


def test_team_agent_spec_model_pool_round_trips_through_json():
    pool = _make_pool(3)
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, model_pool=pool)
    restored = TeamAgentSpec.model_validate_json(spec.model_dump_json())
    assert len(restored.model_pool) == 3
    assert [e.model_name for e in restored.model_pool] == ["m0", "m1", "m2"]


def test_by_model_name_allocator_rotates_within_named_group():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("gpt-4", "a3"),
        _make_named_entry("claude", "c1"),
    ]
    allocator = ByModelNameAllocator(pool)

    # Three calls for gpt-4 walk a1 → a2 → a3.
    bases = [
        allocator.allocate(model_name="gpt-4").model_client_config.api_base
        for _ in range(3)
    ]
    assert bases == ["http://a1", "http://a2", "http://a3"]

    # Wrap-around in the gpt-4 group.
    assert allocator.allocate(model_name="gpt-4").model_client_config.api_base == "http://a1"

    # claude has only one endpoint — repeated calls always return c1.
    for _ in range(2):
        cfg = allocator.allocate(model_name="claude")
        assert cfg.model_client_config.api_base == "http://c1"


def test_by_model_name_allocator_independent_counters_per_name():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("claude", "c1"),
        _make_named_entry("claude", "c2"),
    ]
    allocator = ByModelNameAllocator(pool)

    # Drive gpt-4 forward without touching claude.
    [allocator.allocate(model_name="gpt-4") for _ in range(5)]

    # claude counter should still be at 0 → first claude call returns c1.
    assert allocator.allocate(model_name="claude").model_client_config.api_base == "http://c1"


def test_by_model_name_allocator_returns_none_for_unknown_or_missing_name():
    pool = [_make_named_entry("gpt-4", "a1")]
    allocator = ByModelNameAllocator(pool)
    assert allocator.allocate(model_name=None) is None
    assert allocator.allocate() is None  # default arg
    assert allocator.allocate(model_name="") is None
    assert allocator.allocate(model_name="gemini") is None
    # Unknown-name calls must NOT advance any counter.
    assert allocator.allocate(model_name="gpt-4").model_client_config.api_base == "http://a1"


def test_by_model_name_allocator_handles_empty_pool():
    allocator = ByModelNameAllocator([])
    assert allocator.allocate(model_name="gpt-4") is None
    assert allocator.allocate() is None


def test_round_robin_allocator_ignores_model_name_argument():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("claude", "c1"),
    ]
    allocator = RoundRobinModelAllocator(pool)
    # Despite passing names, rotation walks the pool linearly.
    bases = [
        allocator.allocate(model_name="claude").model_client_config.api_base,
        allocator.allocate(model_name="gpt-4").model_client_config.api_base,
        allocator.allocate(model_name="claude").model_client_config.api_base,
    ]
    assert bases == ["http://a1", "http://c1", "http://a1"]


def test_build_model_allocator_dispatches_by_strategy():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("claude", "c1"),
    ]
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()})

    rr = TeamSpec(
        team_name="t",
        display_name="t",
        model_pool=pool,
        model_pool_strategy="round_robin",
    )
    assert isinstance(build_model_allocator(spec, rr), RoundRobinModelAllocator)

    bn = TeamSpec(
        team_name="t",
        display_name="t",
        model_pool=pool,
        model_pool_strategy="by_model_name",
    )
    assert isinstance(build_model_allocator(spec, bn), ByModelNameAllocator)


def test_build_model_allocator_rejects_unknown_strategy():
    pool = [_make_named_entry("gpt-4", "a1")]
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()})
    # ``model_pool_strategy`` is a Literal, so pydantic blocks invalid
    # values at TeamSpec construction time. Bypass validation to
    # exercise the factory's own guard.
    team_spec = TeamSpec.model_construct(
        team_name="t",
        display_name="t",
        model_pool=pool,
        model_pool_strategy="weighted",
    )
    with pytest.raises(ValueError, match="Unknown model_pool_strategy"):
        build_model_allocator(spec, team_spec)


def test_team_agent_spec_propagates_strategy_into_team_spec():
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        model_pool=[_make_named_entry("gpt-4", "a1")],
        model_pool_strategy="by_model_name",
    )
    restored = TeamAgentSpec.model_validate_json(spec.model_dump_json())
    assert restored.model_pool_strategy == "by_model_name"


# ---------------------------------------------------------------------------
# Allocator state_dict / load_state_dict — recovery semantics
# ---------------------------------------------------------------------------


def test_round_robin_state_dict_round_trip_resumes_rotation():
    pool = _make_pool(3)
    a = RoundRobinModelAllocator(pool)
    [a.allocate() for _ in range(2)]
    snapshot = a.state_dict()

    # Brand-new allocator continues from the snapshot.
    b = RoundRobinModelAllocator(pool)
    b.load_state_dict(snapshot)
    next_after_resume = b.allocate()
    next_without_resume = RoundRobinModelAllocator(pool).allocate()

    assert next_after_resume.model_request_config.model_name == "m2"
    assert next_without_resume.model_request_config.model_name == "m0"


def test_round_robin_state_dict_round_trips_through_json():
    import json

    pool = _make_pool(2)
    a = RoundRobinModelAllocator(pool)
    a.allocate()
    encoded = json.dumps(a.state_dict())
    decoded = json.loads(encoded)

    b = RoundRobinModelAllocator(pool)
    b.load_state_dict(decoded)
    assert b.allocate().model_request_config.model_name == "m1"


def test_round_robin_load_state_dict_tolerates_missing_or_bad_input():
    pool = _make_pool(2)
    a = RoundRobinModelAllocator(pool)
    a.load_state_dict({})  # missing key
    assert a.allocate().model_request_config.model_name == "m0"

    a.load_state_dict({"index": "not-an-int"})  # malformed
    assert a.allocate().model_request_config.model_name == "m0"


def test_by_model_name_state_dict_resumes_per_group_rotation():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("gpt-4", "a3"),
        _make_named_entry("claude", "c1"),
        _make_named_entry("claude", "c2"),
    ]
    a = ByModelNameAllocator(pool)
    # Drive gpt-4 twice (a1, a2) and claude once (c1).
    a.allocate(model_name="gpt-4")
    a.allocate(model_name="gpt-4")
    a.allocate(model_name="claude")
    snapshot = a.state_dict()

    b = ByModelNameAllocator(pool)
    b.load_state_dict(snapshot)
    # gpt-4 continues at a3, claude continues at c2.
    assert b.allocate(model_name="gpt-4").model_client_config.api_base == "http://a3"
    assert b.allocate(model_name="claude").model_client_config.api_base == "http://c2"


def test_by_model_name_load_state_dict_drops_stale_groups_and_seeds_new_ones():
    initial_pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("claude", "c1"),
    ]
    a = ByModelNameAllocator(initial_pool)
    a.allocate(model_name="gpt-4")
    a.allocate(model_name="claude")
    snapshot = a.state_dict()

    new_pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("gemini", "g1"),  # new name, was not in snapshot
        # claude removed from new_pool
    ]
    b = ByModelNameAllocator(new_pool)
    b.load_state_dict(snapshot)  # must not raise on stale claude entry

    # gpt-4 counter restored to 1 → next gpt-4 pick is a2.
    assert b.allocate(model_name="gpt-4").model_client_config.api_base == "http://a2"
    # gemini counter is fresh (0) → first gemini pick is g1.
    assert b.allocate(model_name="gemini").model_client_config.api_base == "http://g1"


def test_by_model_name_load_state_dict_tolerates_malformed_input():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("claude", "c1"),
    ]
    a = ByModelNameAllocator(pool)
    a.load_state_dict({"inner_indexes": "not-a-dict"})
    assert a.allocate(model_name="gpt-4").model_client_config.api_base == "http://a1"

    a.load_state_dict({"inner_indexes": {"gpt-4": "bogus"}})
    assert a.allocate(model_name="gpt-4").model_client_config.api_base == "http://a1"


# ---------------------------------------------------------------------------
# TeamAgent integration: persist + recover allocator state through session
# ---------------------------------------------------------------------------


class _StubSession:
    """Minimal stand-in for AgentTeamSession for persistence-only tests.

    Captures every ``update_state`` call into a single dict and replays
    it on ``get_state``. Mirrors the contract that ``TeamAgent`` relies
    on without bringing the full session runtime online.
    """

    def __init__(self) -> None:
        self.state: dict = {}

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)


def _bare_team_agent(allocator):
    """Build a TeamAgent shell with just the bits persistence reads.

    Skips ``configure`` (and the heavy DeepAgent / DB wiring it
    triggers) by injecting the spec, runtime context, and allocator
    directly. Sufficient for exercising the persist + load contract.
    """
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import (
        TeamRole,
        TeamRuntimeContext,
        TeamSpec,
    )
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("claude", "c1"),
    ]
    team_spec = TeamSpec(
        team_name="t",
        display_name="t",
        leader_member_name="leader",
        model_pool=pool,
        model_pool_strategy="by_model_name",
    )
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="t",
        model_pool=pool,
        model_pool_strategy="by_model_name",
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader",
        team_spec=team_spec,
    )
    agent = TeamAgent(AgentCard(id="t_leader", name="leader"))
    agent._spec = spec
    agent._ctx = ctx
    agent._model_allocator = allocator
    return agent


def test_persist_leader_config_includes_allocator_state():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("claude", "c1"),
    ]
    allocator = ByModelNameAllocator(pool)
    allocator.allocate(model_name="gpt-4")
    allocator.allocate(model_name="claude")

    agent = _bare_team_agent(allocator)
    session = _StubSession()
    agent._persist_leader_config(session)

    assert "model_allocator_state" in session.state
    snapshot = session.state["model_allocator_state"]
    assert snapshot == {"inner_indexes": {"gpt-4": 1, "claude": 1}}


def test_persist_allocator_state_writes_only_allocator_payload():
    pool = _make_pool(3)
    allocator = RoundRobinModelAllocator(pool)
    [allocator.allocate() for _ in range(2)]

    agent = _bare_team_agent(allocator)
    session = _StubSession()
    agent._team_session = session
    agent._persist_allocator_state()

    # Only allocator snapshot, no spec / context overwrite.
    assert set(session.state) == {"model_allocator_state"}
    assert session.state["model_allocator_state"] == {"index": 2}


def test_persist_allocator_state_no_op_without_session_or_allocator():
    agent_no_session = _bare_team_agent(RoundRobinModelAllocator(_make_pool(2)))
    agent_no_session._team_session = None
    # Should silently no-op, no AttributeError.
    agent_no_session._persist_allocator_state()

    agent_no_alloc = _bare_team_agent(RoundRobinModelAllocator(_make_pool(2)))
    agent_no_alloc._model_allocator = None
    agent_no_alloc._team_session = _StubSession()
    agent_no_alloc._persist_allocator_state()
    assert agent_no_alloc._team_session.state == {}


def test_persist_leader_config_omits_allocator_state_when_no_pool():
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import (
        TeamRole,
        TeamRuntimeContext,
        TeamSpec,
    )
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="t")
    team_spec = TeamSpec(team_name="t", display_name="t")
    ctx = TeamRuntimeContext(
        role=TeamRole.LEADER, member_name="leader", team_spec=team_spec,
    )
    agent = TeamAgent(AgentCard(id="t_leader", name="leader"))
    agent._spec = spec
    agent._ctx = ctx
    agent._model_allocator = None  # no pool configured

    session = _StubSession()
    agent._persist_leader_config(session)

    assert "model_allocator_state" not in session.state
    assert "spec" in session.state and "context" in session.state


def test_round_trip_persist_then_load_continues_rotation():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("claude", "c1"),
    ]
    # Phase 1: leader allocates one of each name and persists.
    allocator = ByModelNameAllocator(pool)
    allocator.allocate(model_name="gpt-4")  # → a1
    allocator.allocate(model_name="claude")  # → c1
    agent = _bare_team_agent(allocator)
    session = _StubSession()
    agent._persist_leader_config(session)

    # Phase 2: simulate process restart — fresh allocator, load state,
    # the next gpt-4 allocation must continue at a2 instead of restarting.
    fresh = ByModelNameAllocator(pool)
    fresh.load_state_dict(session.state["model_allocator_state"])
    cfg = fresh.allocate(model_name="gpt-4")
    assert cfg.model_request_config.model_name == "gpt-4"
    assert cfg.model_client_config.api_base == "http://a2"


def test_leader_spec_carries_model_name_for_pool_allocation():
    from openjiuwen.agent_teams.schema.blueprint import LeaderSpec

    leader = LeaderSpec(model_name="gpt-4")
    assert leader.model_name == "gpt-4"
    # Round-trips through JSON for spec serialization.
    restored = LeaderSpec.model_validate_json(leader.model_dump_json())
    assert restored.model_name == "gpt-4"


def test_team_member_spec_carries_model_name_for_pool_allocation():
    from openjiuwen.agent_teams.schema.team import TeamMemberSpec

    member = TeamMemberSpec(
        member_name="dev1",
        display_name="Dev 1",
        persona="backend",
        model_name="claude",
    )
    assert member.model_name == "claude"
    restored = TeamMemberSpec.model_validate_json(member.model_dump_json())
    assert restored.model_name == "claude"
