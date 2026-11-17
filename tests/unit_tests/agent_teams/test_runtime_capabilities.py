# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for hot-updatable TeamAgentSpec capability overlays."""

from __future__ import annotations

from openjiuwen.agent_teams.models.pool import ModelPoolEntry, ModelRouterConfig
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec, TeamModelConfig
from openjiuwen.agent_teams.schema.runtime_capabilities import (
    apply_runtime_capabilities,
    apply_runtime_capabilities_inplace,
    resolve_effective_model_pool,
)
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig


def _model(name: str) -> TeamModelConfig:
    return TeamModelConfig(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="k",
            api_base="http://localhost",
            model_name=name,
            verify_ssl=False,
        )
    )


def _spec(
    *,
    leader_model: str,
    leader_skills: list[str] | None,
    teammate_skills: list[str] | None = None,
    pool: list[str] | None = None,
) -> TeamAgentSpec:
    agents = {
        "leader": DeepAgentSpec(model=_model(leader_model), skills=leader_skills),
        "teammate": DeepAgentSpec(skills=teammate_skills),
    }
    entries = [
        ModelPoolEntry(
            model_name=name,
            api_key="k",
            api_base_url="http://localhost",
            api_provider="OpenAI",
        )
        for name in (pool or [])
    ]
    return TeamAgentSpec(
        team_name="demo",
        agents=agents,
        model_pool=entries,
        model_pool_strategy="by_model_name" if entries else "round_robin",
    )


def test_apply_runtime_capabilities_overlays_model_and_skills():
    base = _spec(leader_model="old-model", leader_skills=["alpha"], teammate_skills=["alpha"])
    runtime = _spec(
        leader_model="new-model",
        leader_skills=["alpha", "beta"],
        teammate_skills=["gamma"],
        pool=["new-model"],
    )

    merged = apply_runtime_capabilities(base, runtime)

    assert merged is not base
    assert base.agents["leader"].model.model_client_config.model_name == "old-model"
    assert merged.agents["leader"].model.model_client_config.model_name == "new-model"
    assert merged.agents["leader"].skills == ["alpha", "beta"]
    assert merged.agents["teammate"].skills == ["gamma"]
    assert [entry.model_name for entry in merged.model_pool] == ["new-model"]
    assert merged.model_pool_strategy == "by_model_name"
    assert merged.team_name == "demo"


def test_apply_runtime_capabilities_preserves_base_only_agent_keys():
    base = _spec(leader_model="old-model", leader_skills=["alpha"])
    base.agents["alice"] = DeepAgentSpec(skills=["kept"])
    runtime = _spec(leader_model="new-model", leader_skills=["beta"])

    merged = apply_runtime_capabilities(base, runtime)

    assert "alice" in merged.agents
    assert merged.agents["alice"].skills == ["kept"]
    assert merged.agents["leader"].skills == ["beta"]


def test_apply_runtime_capabilities_inplace_mutates_target():
    target = _spec(leader_model="old-model", leader_skills=["alpha"])
    runtime = _spec(leader_model="new-model", leader_skills=["beta"], pool=["new-model"])

    result = apply_runtime_capabilities_inplace(target, runtime)

    assert result is target
    assert target.agents["leader"].model.model_client_config.model_name == "new-model"
    assert target.agents["leader"].skills == ["beta"]
    assert [entry.model_name for entry in target.model_pool] == ["new-model"]


def test_resolve_effective_model_pool_expands_router():
    tip = TeamAgentSpec(
        team_name="demo",
        agents={"leader": DeepAgentSpec()},
        model_router=ModelRouterConfig(
            api_base_url="http://router",
            api_key="k",
            api_provider="OpenAI",
            model_names=["a", "b"],
        ),
    )

    pool, strategy = resolve_effective_model_pool(tip)

    assert strategy == "router"
    assert [entry.model_name for entry in pool] == ["a", "b"]
    assert tip.model_pool == []


def test_apply_runtime_capabilities_copies_router_sourcing():
    base = _spec(leader_model="old-model", leader_skills=["alpha"], pool=["old-model"])
    runtime = TeamAgentSpec(
        team_name="demo",
        agents={"leader": DeepAgentSpec(model=_model("routed"), skills=["beta"])},
        model_router=ModelRouterConfig(
            api_base_url="http://router",
            api_key="k",
            api_provider="OpenAI",
            model_names=["routed"],
        ),
    )

    merged = apply_runtime_capabilities(base, runtime)
    pool, strategy = resolve_effective_model_pool(merged)

    assert merged.model_pool == []
    assert merged.model_router is not None
    assert strategy == "router"
    assert [entry.model_name for entry in pool] == ["routed"]
    assert merged.agents["leader"].skills == ["beta"]


def test_refresh_context_member_model_from_pool_picks_up_tip_credentials():
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec

    stale = TeamModelConfig(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="old-key",
            api_base="http://old",
            model_name="shared-model",
            verify_ssl=False,
        )
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader",
        desc="leader",
        team_spec=TeamSpec(
            team_name="demo",
            display_name="demo",
            leader_member_name="leader",
            model_pool=[
                ModelPoolEntry(
                    model_name="shared-model",
                    api_key="new-key",
                    api_base_url="http://new",
                    api_provider="OpenAI",
                )
            ],
            model_pool_strategy="by_model_name",
        ),
        member_model=stale,
    )

    TeamAgent._refresh_context_member_model_from_pool(ctx)

    assert ctx.member_model is not None
    assert ctx.member_model.model_client_config.api_key == "new-key"
    assert ctx.member_model.model_client_config.api_base == "http://new"


def test_refresh_context_member_model_clears_when_pool_empty():
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec

    ctx = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader",
        desc="leader",
        team_spec=TeamSpec(
            team_name="demo",
            display_name="demo",
            leader_member_name="leader",
            model_pool=[],
        ),
        member_model=_model("stale"),
    )

    TeamAgent._refresh_context_member_model_from_pool(ctx)

    assert ctx.member_model is None
