# coding: utf-8
"""Unit tests for model allocator behavior.

Covers ``RoundRobinModelAllocator`` rotation, the ``build_model_allocator``
factory's pool-vs-no-pool branching, and ``ModelPoolEntry`` materialization
into ``TeamModelConfig``.
"""

from __future__ import annotations

from openjiuwen.agent_teams.agent.model_allocator import (
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
