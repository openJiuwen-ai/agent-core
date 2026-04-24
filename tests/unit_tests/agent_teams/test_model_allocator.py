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


def test_by_model_name_allocator_alternates_groups_in_insertion_order():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("gpt-4", "a3"),
        _make_named_entry("claude", "c1"),
        _make_named_entry("claude", "c2"),
    ]
    allocator = ByModelNameAllocator(pool)
    sequence = [allocator.allocate() for _ in range(8)]

    names = [cfg.model_request_config.model_name for cfg in sequence]
    bases = [cfg.model_client_config.api_base for cfg in sequence]

    # Outer rotation alternates names in insertion order.
    assert names == [
        "gpt-4", "claude",
        "gpt-4", "claude",
        "gpt-4", "claude",
        "gpt-4", "claude",
    ]
    # Inner rotation walks each group's endpoints independently.
    assert bases == [
        "http://a1", "http://c1",
        "http://a2", "http://c2",
        "http://a3", "http://c1",  # claude wraps before gpt-4 does
        "http://a1", "http://c2",
    ]


def test_by_model_name_allocator_handles_empty_pool():
    allocator = ByModelNameAllocator([])
    assert allocator.allocate() is None


def test_by_model_name_allocator_distributes_evenly_when_groups_uneven():
    pool = [
        _make_named_entry("gpt-4", "a1"),
        _make_named_entry("gpt-4", "a2"),
        _make_named_entry("gpt-4", "a3"),
        _make_named_entry("gpt-4", "a4"),
        _make_named_entry("claude", "c1"),
    ]
    allocator = ByModelNameAllocator(pool)
    counts: dict[str, int] = {"gpt-4": 0, "claude": 0}
    for _ in range(20):
        cfg = allocator.allocate()
        counts[cfg.model_request_config.model_name] += 1
    # Even split per name, regardless of pool composition.
    assert counts == {"gpt-4": 10, "claude": 10}


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
