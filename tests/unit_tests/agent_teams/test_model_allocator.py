# coding: utf-8
"""Unit tests for model allocator behavior.

Covers the two shipped strategies (``RoundRobinModelAllocator``,
``ByModelNameAllocator``), the ``build_model_allocator`` factory's
pool-vs-no-pool branching, pool-entry materialization with metadata
merging, session persistence (``state_dict`` / ``load_state_dict`` with
pool-digest driven reset), and the positional DB resolver
``resolve_member_model``.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.agent.model_allocator import (
    Allocation,
    ByModelNameAllocator,
    RoundRobinModelAllocator,
    build_model_allocator,
    resolve_member_model,
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


# ---------------------------------------------------------------------------
# ModelPoolEntry materialization + metadata merge
# ---------------------------------------------------------------------------


def test_round_robin_allocator_rotates_through_pool():
    pool = _make_pool(3)
    allocator = RoundRobinModelAllocator(pool)
    names = [
        allocator.allocate().to_team_model_config().model_request_config.model_name
        for _ in range(7)
    ]
    assert names == ["m0", "m1", "m2", "m0", "m1", "m2", "m0"]


def test_round_robin_allocator_returns_none_when_pool_empty():
    allocator = RoundRobinModelAllocator([])
    assert allocator.allocate() is None
    assert allocator.allocate() is None


def test_model_pool_entry_assigns_unique_model_id_per_instance():
    a = ModelPoolEntry(
        model_name="m", api_key="k", api_base_url="http://x", api_provider="OpenAI",
    )
    b = ModelPoolEntry(
        model_name="m", api_key="k", api_base_url="http://x", api_provider="OpenAI",
    )
    # model_id is runtime-only client identity: auto-uuid, per-instance,
    # not persisted to DB. Distinct instances must get distinct ids so
    # the foundation resource manager doesn't collapse independent
    # endpoints onto one cached client.
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
            "client": {"timeout": 30.0, "verify_ssl": False, "max_retries": 5},
            "request": {"temperature": 0.2, "top_p": 0.9, "max_tokens": 1024},
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
            "client": {"api_key": "shadow-key", "api_base": "http://shadow"},
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


# ---------------------------------------------------------------------------
# Factory + configuration round-trips
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Allocation result + allocator behavior
# ---------------------------------------------------------------------------


def test_allocation_to_db_ref_is_name_plus_group_index():
    entry = _make_named_entry("gpt-4", "a1")
    alloc = Allocation(entry=entry, group_index=2)
    assert alloc.to_db_ref() == {"model_name": "gpt-4", "model_index": 2}


def test_round_robin_allocation_carries_group_index_within_name():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("claude", "c1"),
        _make_named_entry("gpt-4", "a2"),
    ]
    allocator = RoundRobinModelAllocator(pool)
    # Sequence: gpt-4/a1 (gpt-4 group idx=0), claude/c1 (claude idx=0),
    # gpt-4/a2 (gpt-4 group idx=1).
    alloc1 = allocator.allocate()
    alloc2 = allocator.allocate()
    alloc3 = allocator.allocate()
    assert alloc1.to_db_ref() == {"model_name": "gpt-4", "model_index": 0}
    assert alloc2.to_db_ref() == {"model_name": "claude", "model_index": 0}
    assert alloc3.to_db_ref() == {"model_name": "gpt-4", "model_index": 1}


def test_by_model_name_allocator_rotates_within_named_group():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("gpt-4", "a3"),
        _make_named_entry("claude", "c1"),
    ]
    allocator = ByModelNameAllocator(pool)

    bases = [
        allocator.allocate(model_name="gpt-4").to_team_model_config().model_client_config.api_base
        for _ in range(3)
    ]
    assert bases == ["http://a1", "http://a2", "http://a3"]

    # Wrap-around in the gpt-4 group.
    wrap = allocator.allocate(model_name="gpt-4").to_team_model_config()
    assert wrap.model_client_config.api_base == "http://a1"

    # claude has only one endpoint — repeated calls always return c1.
    for _ in range(2):
        alloc = allocator.allocate(model_name="claude")
        assert alloc.to_team_model_config().model_client_config.api_base == "http://c1"


def test_by_model_name_allocator_independent_counters_per_name():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("claude", "c1"),
        _make_named_entry("claude", "c2"),
    ]
    allocator = ByModelNameAllocator(pool)
    [allocator.allocate(model_name="gpt-4") for _ in range(5)]
    first_claude = allocator.allocate(model_name="claude").to_team_model_config()
    assert first_claude.model_client_config.api_base == "http://c1"


def test_by_model_name_allocator_returns_none_for_unknown_or_missing_name():
    pool = [_make_named_entry("gpt-4", "a1")]
    allocator = ByModelNameAllocator(pool)
    assert allocator.allocate(model_name=None) is None
    assert allocator.allocate() is None
    assert allocator.allocate(model_name="") is None
    assert allocator.allocate(model_name="gemini") is None
    # Unknown-name calls must NOT advance any counter.
    assert (
        allocator.allocate(model_name="gpt-4").to_team_model_config().model_client_config.api_base
        == "http://a1"
    )


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
    bases = [
        allocator.allocate(model_name="claude").to_team_model_config().model_client_config.api_base,
        allocator.allocate(model_name="gpt-4").to_team_model_config().model_client_config.api_base,
        allocator.allocate(model_name="claude").to_team_model_config().model_client_config.api_base,
    ]
    assert bases == ["http://a1", "http://c1", "http://a1"]


def test_build_model_allocator_dispatches_by_strategy():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("claude", "c1"),
    ]
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()})

    rr = TeamSpec(
        team_name="t", display_name="t",
        model_pool=pool, model_pool_strategy="round_robin",
    )
    assert isinstance(build_model_allocator(spec, rr), RoundRobinModelAllocator)

    bn = TeamSpec(
        team_name="t", display_name="t",
        model_pool=pool, model_pool_strategy="by_model_name",
    )
    assert isinstance(build_model_allocator(spec, bn), ByModelNameAllocator)


def test_build_model_allocator_rejects_unknown_strategy():
    pool = [_make_named_entry("gpt-4", "a1")]
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()})
    team_spec = TeamSpec.model_construct(
        team_name="t", display_name="t",
        model_pool=pool, model_pool_strategy="weighted",
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
# state_dict / load_state_dict + pool-digest driven reset
# ---------------------------------------------------------------------------


def test_round_robin_state_dict_round_trip_resumes_rotation():
    pool = _make_pool(3)
    a = RoundRobinModelAllocator(pool)
    [a.allocate() for _ in range(2)]
    snapshot = a.state_dict()

    b = RoundRobinModelAllocator(pool)
    b.load_state_dict(snapshot)
    resume_name = b.allocate().to_team_model_config().model_request_config.model_name
    fresh_name = (
        RoundRobinModelAllocator(pool).allocate().to_team_model_config().model_request_config.model_name
    )
    assert resume_name == "m2"
    assert fresh_name == "m0"


def test_round_robin_state_dict_round_trips_through_json():
    import json

    pool = _make_pool(2)
    a = RoundRobinModelAllocator(pool)
    a.allocate()
    encoded = json.dumps(a.state_dict())
    decoded = json.loads(encoded)

    b = RoundRobinModelAllocator(pool)
    b.load_state_dict(decoded)
    assert b.allocate().to_team_model_config().model_request_config.model_name == "m1"


def test_round_robin_load_state_dict_resets_on_pool_digest_change():
    original_pool = _make_pool(3)
    a = RoundRobinModelAllocator(original_pool)
    [a.allocate() for _ in range(2)]  # counter = 2
    snapshot = a.state_dict()

    # New pool with different composition -> fresh allocator gets a
    # different digest, load_state_dict must reset index to 0.
    new_pool = _make_pool(2)  # entries differ
    b = RoundRobinModelAllocator(new_pool)
    b.load_state_dict(snapshot)
    assert b.allocate().to_team_model_config().model_request_config.model_name == "m0"


def test_round_robin_load_state_dict_tolerates_missing_or_bad_input():
    pool = _make_pool(2)
    a = RoundRobinModelAllocator(pool)
    a.load_state_dict({})
    assert a.allocate().to_team_model_config().model_request_config.model_name == "m0"

    # Matching digest but bogus counter -> counter reset to 0.
    a.load_state_dict({"index": "not-an-int", "pool_digest": a.state_dict()["pool_digest"]})
    assert a.allocate().to_team_model_config().model_request_config.model_name == "m0"


def test_by_model_name_state_dict_resumes_per_group_rotation():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("gpt-4", "a3"),
        _make_named_entry("claude", "c1"),
        _make_named_entry("claude", "c2"),
    ]
    a = ByModelNameAllocator(pool)
    a.allocate(model_name="gpt-4")
    a.allocate(model_name="gpt-4")
    a.allocate(model_name="claude")
    snapshot = a.state_dict()

    b = ByModelNameAllocator(pool)
    b.load_state_dict(snapshot)
    assert (
        b.allocate(model_name="gpt-4").to_team_model_config().model_client_config.api_base
        == "http://a3"
    )
    assert (
        b.allocate(model_name="claude").to_team_model_config().model_client_config.api_base
        == "http://c2"
    )


def test_by_model_name_load_state_dict_resets_on_pool_digest_change():
    """Composition change -> all counters reset, not selectively dropped."""
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
        _make_named_entry("gpt-4", "a2"),  # new endpoint
        _make_named_entry("gemini", "g1"),
    ]
    b = ByModelNameAllocator(new_pool)
    b.load_state_dict(snapshot)
    # Digest mismatch -> counters zeroed. gpt-4 group starts at a1, not a2.
    assert (
        b.allocate(model_name="gpt-4").to_team_model_config().model_client_config.api_base
        == "http://a1"
    )
    assert (
        b.allocate(model_name="gemini").to_team_model_config().model_client_config.api_base
        == "http://g1"
    )


def test_pool_digest_stable_under_credential_refresh():
    """Rotating api_key / metadata doesn't bump digest -> counters preserved."""
    original = ModelPoolEntry(
        model_name="gpt-4",
        api_key="OLD",
        api_base_url="http://x",
        api_provider="OpenAI",
    )
    a = ByModelNameAllocator([original])
    a.allocate(model_name="gpt-4")
    snapshot = a.state_dict()

    # Same name + same base_url + new credential -> digest unchanged.
    refreshed = ModelPoolEntry(
        model_name="gpt-4",
        api_key="NEW",
        api_base_url="http://x",
        api_provider="OpenAI",
    )
    b = ByModelNameAllocator([refreshed])
    b.load_state_dict(snapshot)
    # Counter preserved -> next gpt-4 call wraps to a1 (only one entry).
    cfg = b.allocate(model_name="gpt-4").to_team_model_config()
    assert cfg.model_client_config.api_key == "NEW"


def test_by_model_name_load_state_dict_tolerates_malformed_input():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("claude", "c1"),
    ]
    a = ByModelNameAllocator(pool)
    digest = a.state_dict()["pool_digest"]
    a.load_state_dict({"inner_indexes": "not-a-dict", "pool_digest": digest})
    assert (
        a.allocate(model_name="gpt-4").to_team_model_config().model_client_config.api_base
        == "http://a1"
    )

    a.load_state_dict({"inner_indexes": {"gpt-4": "bogus"}, "pool_digest": digest})
    assert (
        a.allocate(model_name="gpt-4").to_team_model_config().model_client_config.api_base
        == "http://a1"
    )


# ---------------------------------------------------------------------------
# TeamAgent integration: persist + recover allocator state through session
# ---------------------------------------------------------------------------


class _StubSession:
    def __init__(self) -> None:
        self.state: dict = {}

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)


def _bare_team_agent(allocator):
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
        team_name="t", display_name="t",
        leader_member_name="leader",
        model_pool=pool, model_pool_strategy="by_model_name",
    )
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="t",
        model_pool=pool, model_pool_strategy="by_model_name",
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.LEADER, member_name="leader", team_spec=team_spec,
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
    assert snapshot["inner_indexes"] == {"gpt-4": 1, "claude": 1}
    assert "pool_digest" in snapshot


def test_persist_allocator_state_writes_only_allocator_payload():
    pool = _make_pool(3)
    allocator = RoundRobinModelAllocator(pool)
    [allocator.allocate() for _ in range(2)]

    agent = _bare_team_agent(allocator)
    session = _StubSession()
    agent._team_session = session
    agent._persist_allocator_state()

    assert set(session.state) == {"model_allocator_state"}
    snapshot = session.state["model_allocator_state"]
    assert snapshot["index"] == 2
    assert "pool_digest" in snapshot


def test_persist_allocator_state_no_op_without_session_or_allocator():
    agent_no_session = _bare_team_agent(RoundRobinModelAllocator(_make_pool(2)))
    agent_no_session._team_session = None
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
    agent._model_allocator = None

    session = _StubSession()
    agent._persist_leader_config(session)

    assert "model_allocator_state" not in session.state
    assert "spec" in session.state and "context" in session.state


def test_leader_spec_carries_model_name_for_pool_allocation():
    from openjiuwen.agent_teams.schema.blueprint import LeaderSpec

    leader = LeaderSpec(model_name="gpt-4")
    assert leader.model_name == "gpt-4"
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


# ---------------------------------------------------------------------------
# resolve_member_model: positional DB resolution, no allocator mutation
# ---------------------------------------------------------------------------


def test_resolve_member_model_returns_entry_at_group_index():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("claude", "c1"),
    ]
    team_spec = TeamSpec(team_name="t", display_name="t", model_pool=pool)
    cfg = resolve_member_model(team_spec, model_name="gpt-4", model_index=1)
    assert cfg is not None
    assert cfg.model_client_config.api_base == "http://a2"


def test_resolve_member_model_picks_up_refreshed_credentials_from_pool():
    """Pool reflects credential rotation; DB ref still resolves positionally."""
    refreshed_pool = [
        ModelPoolEntry(
            model_name="gpt-4",
            api_key="NEW-KEY",
            api_base_url="http://new",
            api_provider="OpenAI",
        ),
    ]
    team_spec = TeamSpec(team_name="t", display_name="t", model_pool=refreshed_pool)
    cfg = resolve_member_model(team_spec, model_name="gpt-4", model_index=0)
    assert cfg.model_client_config.api_key == "NEW-KEY"
    assert cfg.model_client_config.api_base == "http://new"


def test_resolve_member_model_clamps_out_of_range_index_to_zero():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
    ]
    team_spec = TeamSpec(team_name="t", display_name="t", model_pool=pool)
    # Group now has 2 entries. Stored index=5 -> clamp to 0 deterministically.
    cfg = resolve_member_model(team_spec, model_name="gpt-4", model_index=5)
    assert cfg.model_client_config.api_base == "http://a1"


def test_resolve_member_model_returns_none_when_name_absent_from_pool():
    pool = [_make_named_entry("gpt-4", "a1")]
    team_spec = TeamSpec(team_name="t", display_name="t", model_pool=pool)
    cfg = resolve_member_model(team_spec, model_name="gemini", model_index=0)
    assert cfg is None


def test_resolve_member_model_returns_none_without_pool():
    team_spec = TeamSpec(team_name="t", display_name="t")
    cfg = resolve_member_model(team_spec, model_name="gpt-4", model_index=0)
    assert cfg is None


def test_resolve_member_model_tolerates_missing_index():
    pool = [_make_named_entry("gpt-4", "a1")]
    team_spec = TeamSpec(team_name="t", display_name="t", model_pool=pool)
    # model_index=None -> clamp to 0.
    cfg = resolve_member_model(team_spec, model_name="gpt-4", model_index=None)
    assert cfg.model_client_config.api_base == "http://a1"


# ---------------------------------------------------------------------------
# update_model_pool: runtime refresh + counter reset
# ---------------------------------------------------------------------------


def test_update_model_pool_replaces_pool_and_resets_allocator():
    initial = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
    ]
    allocator = ByModelNameAllocator(initial)
    allocator.allocate(model_name="gpt-4")
    allocator.allocate(model_name="gpt-4")

    agent = _bare_team_agent(allocator)
    agent._team_session = None

    replacement = [
        _make_named_entry("gpt-4", "b1"),  # different endpoint -> no inherit
        _make_named_entry("claude", "c1"),
    ]
    agent.update_model_pool(replacement)

    # Pool replaced (entries inherited or kept their own model_id).
    assert len(agent._ctx.team_spec.model_pool) == 2
    assert {e.api_base_url for e in agent._ctx.team_spec.model_pool} == {"http://b1", "http://c1"}
    # Allocator rebuilt -> counters zeroed against new layout.
    first_after = agent._model_allocator.allocate(model_name="gpt-4").to_team_model_config()
    assert first_after.model_client_config.api_base == "http://b1"


# ---------------------------------------------------------------------------
# inherit_pool_ids: bit-exact signature match preserves model_id
# ---------------------------------------------------------------------------


def test_inherit_pool_ids_preserves_id_for_bit_exact_entry():
    """No-change refresh inherits model_id (cache stays stable when safe)."""
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    old = ModelPoolEntry(
        model_name="gpt-4", api_key="K",
        api_base_url="http://x", api_provider="OpenAI",
    )
    new = ModelPoolEntry(
        model_name="gpt-4", api_key="K",
        api_base_url="http://x", api_provider="OpenAI",
    )
    assert old.model_id != new.model_id  # different uuids before merge

    [merged] = inherit_pool_ids([old], [new])
    assert merged.model_id == old.model_id


def test_inherit_pool_ids_breaks_inheritance_on_credential_rotation():
    """api_key change yields a fresh model_id so a future cache cannot
    serve a stale client built against the old credential."""
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    old = ModelPoolEntry(
        model_name="gpt-4", api_key="OLD",
        api_base_url="http://x", api_provider="OpenAI",
    )
    new = ModelPoolEntry(
        model_name="gpt-4", api_key="ROTATED",
        api_base_url="http://x", api_provider="OpenAI",
    )

    [merged] = inherit_pool_ids([old], [new])
    assert merged.model_id == new.model_id  # NOT inherited
    assert merged.api_key == "ROTATED"


def test_inherit_pool_ids_breaks_inheritance_on_base_url_migration():
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    old = ModelPoolEntry(
        model_name="gpt-4", api_key="K",
        api_base_url="http://old", api_provider="OpenAI",
    )
    new = ModelPoolEntry(
        model_name="gpt-4", api_key="K",
        api_base_url="http://new", api_provider="OpenAI",
    )
    [merged] = inherit_pool_ids([old], [new])
    assert merged.model_id == new.model_id


def test_inherit_pool_ids_breaks_inheritance_on_metadata_change():
    """Even small config tweaks (timeout) prevent inheritance — they could
    mean a different client object is needed under a future cache."""
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    old = ModelPoolEntry(
        model_name="gpt-4", api_key="K",
        api_base_url="http://x", api_provider="OpenAI",
        metadata={"client": {"timeout": 30.0}},
    )
    new = ModelPoolEntry(
        model_name="gpt-4", api_key="K",
        api_base_url="http://x", api_provider="OpenAI",
        metadata={"client": {"timeout": 60.0}},  # tuned
    )
    [merged] = inherit_pool_ids([old], [new])
    assert merged.model_id == new.model_id


def test_inherit_pool_ids_keeps_own_id_for_truly_new_endpoint():
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    old = ModelPoolEntry(
        model_name="gpt-4", api_key="k",
        api_base_url="http://a", api_provider="OpenAI",
    )
    new = ModelPoolEntry(
        model_name="claude", api_key="k",
        api_base_url="http://b", api_provider="OpenAI",
    )
    [merged] = inherit_pool_ids([old], [new])
    assert merged.model_id == new.model_id


def test_inherit_pool_ids_signature_match_is_order_independent():
    """Bit-exact pairs match by signature regardless of pool order."""
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    old = [
        ModelPoolEntry(model_name="gpt-4", api_key="K1", api_base_url="http://x", api_provider="OpenAI"),
        ModelPoolEntry(model_name="gpt-4", api_key="K2", api_base_url="http://x", api_provider="OpenAI"),
    ]
    # User reorders without changing values.
    new = [
        ModelPoolEntry(model_name="gpt-4", api_key="K2", api_base_url="http://x", api_provider="OpenAI"),
        ModelPoolEntry(model_name="gpt-4", api_key="K1", api_base_url="http://x", api_provider="OpenAI"),
    ]
    merged = inherit_pool_ids(old, new)
    # Each new entry inherits from the old entry with matching signature.
    assert merged[0].model_id == old[1].model_id  # K2 ↔ K2
    assert merged[1].model_id == old[0].model_id  # K1 ↔ K1


def test_inherit_pool_ids_pairs_one_to_one_when_signatures_collide():
    """If two old entries are byte-identical, two new identical entries
    consume them in pool order — no double-mapping."""
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    # Two genuine duplicates (same signature) on each side.
    old = [
        ModelPoolEntry(model_name="gpt-4", api_key="K", api_base_url="http://x", api_provider="OpenAI"),
        ModelPoolEntry(model_name="gpt-4", api_key="K", api_base_url="http://x", api_provider="OpenAI"),
    ]
    new = [
        ModelPoolEntry(model_name="gpt-4", api_key="K", api_base_url="http://x", api_provider="OpenAI"),
        ModelPoolEntry(model_name="gpt-4", api_key="K", api_base_url="http://x", api_provider="OpenAI"),
    ]
    merged = inherit_pool_ids(old, new)
    inherited = {m.model_id for m in merged}
    # Both new entries inherited; one mapped to each old entry; no
    # duplicates (each old consumed once).
    assert inherited == {old[0].model_id, old[1].model_id}


def test_inherit_pool_ids_drops_removed_endpoints():
    """Removed entries' ids are not transferred anywhere."""
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    old = [
        ModelPoolEntry(model_name="gpt-4", api_key="K", api_base_url="http://a", api_provider="OpenAI"),
        ModelPoolEntry(model_name="claude", api_key="K", api_base_url="http://b", api_provider="OpenAI"),
    ]
    new = [
        ModelPoolEntry(model_name="gpt-4", api_key="K", api_base_url="http://a", api_provider="OpenAI"),
    ]
    merged = inherit_pool_ids(old, new)
    assert len(merged) == 1
    assert merged[0].model_id == old[0].model_id


def test_inherit_pool_ids_does_not_mutate_input_lists():
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    old_entry = ModelPoolEntry(
        model_name="gpt-4", api_key="K", api_base_url="http://x", api_provider="OpenAI",
    )
    new_entry = ModelPoolEntry(
        model_name="gpt-4", api_key="K", api_base_url="http://x", api_provider="OpenAI",
    )
    new_entry_id_before = new_entry.model_id
    inherit_pool_ids([old_entry], [new_entry])
    assert new_entry.model_id == new_entry_id_before


def test_inherit_pool_ids_handles_empty_inputs():
    from openjiuwen.agent_teams.schema.team import inherit_pool_ids

    assert inherit_pool_ids([], []) == []
    assert inherit_pool_ids([], [_make_named_entry("gpt-4", "a1")]) != []


def test_build_rejects_by_model_name_pool_without_leader_model_name():
    """by_model_name + leader.model_name unset + no per-agent leader model → fail fast."""
    from openjiuwen.agent_teams.schema.blueprint import LeaderSpec
    from openjiuwen.core.common.exception.errors import ValidationError

    pool = [_make_named_entry("gpt-4", "a1")]
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},  # model unset
        team_name="t",
        model_pool=pool,
        model_pool_strategy="by_model_name",
        leader=LeaderSpec(member_name="leader"),  # model_name unset
    )
    with pytest.raises(ValidationError, match="agent team config invalid"):
        spec.build()


def test_build_rejects_unknown_leader_model_name():
    """leader.model_name typo / not in pool → fail fast with available names listed."""
    from openjiuwen.agent_teams.schema.blueprint import LeaderSpec
    from openjiuwen.core.common.exception.errors import ValidationError

    pool = [_make_named_entry("gpt-4", "a1")]
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="t",
        model_pool=pool,
        model_pool_strategy="by_model_name",
        leader=LeaderSpec(member_name="leader", model_name="claude"),  # not in pool
    )
    with pytest.raises(ValidationError, match="not present in the pool"):
        spec.build()


def test_build_accepts_pool_when_per_agent_leader_model_supplied():
    """No leader.model_name but agents['leader'].model is set → falls back, no error."""
    from openjiuwen.agent_teams.schema.blueprint import LeaderSpec
    from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
    from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig

    pool = [_make_named_entry("gpt-4", "a1")]
    explicit_model = TeamModelConfig(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="explicit",
            api_base="http://explicit",
            verify_ssl=False,
        ),
        model_request_config=ModelRequestConfig(model="explicit-model"),
    )
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(model=explicit_model)},
        team_name="t",
        spawn_mode="inprocess",
        model_pool=pool,
        model_pool_strategy="by_model_name",
        leader=LeaderSpec(member_name="leader"),  # model_name unset, pool can't resolve
    )
    # Should NOT raise — per-agent fallback covers the leader.
    spec.build()


def test_build_round_robin_strategy_does_not_require_leader_model_name():
    """round_robin always allocates regardless of leader.model_name."""
    from openjiuwen.agent_teams.schema.blueprint import LeaderSpec

    pool = [
        ModelPoolEntry(
            model_name="gpt-4", api_key="k", api_base_url="http://x",
            api_provider="OpenAI", metadata={"client": {"verify_ssl": False}},
        ),
    ]
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="t",
        spawn_mode="inprocess",
        model_pool=pool,
        model_pool_strategy="round_robin",
        leader=LeaderSpec(member_name="leader"),
    )
    # round_robin allocates the first entry without needing model_name.
    spec.build()


def test_update_model_pool_preserves_id_only_when_entry_is_unchanged():
    """End-to-end: pure refresh keeps id; rotation forces new id."""
    initial = [
        ModelPoolEntry(model_name="gpt-4", api_key="K", api_base_url="http://x", api_provider="OpenAI"),
    ]
    allocator = ByModelNameAllocator(initial)
    agent = _bare_team_agent(allocator)
    agent._ctx.team_spec.model_pool = initial
    agent._team_session = None

    old_id = initial[0].model_id

    # Pure refresh (rebuild the same entry) -> id preserved.
    same_again = [
        ModelPoolEntry(model_name="gpt-4", api_key="K", api_base_url="http://x", api_provider="OpenAI"),
    ]
    agent.update_model_pool(same_again)
    assert agent._ctx.team_spec.model_pool[0].model_id == old_id

    # Now rotate the credential -> id MUST change so a future cache
    # can't serve a client built against the previous api_key.
    rotated = [
        ModelPoolEntry(model_name="gpt-4", api_key="ROTATED", api_base_url="http://x", api_provider="OpenAI"),
    ]
    agent.update_model_pool(rotated)
    stored = agent._ctx.team_spec.model_pool[0]
    assert stored.model_id != old_id
    assert stored.api_key == "ROTATED"
