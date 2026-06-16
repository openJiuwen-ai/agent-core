# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the stable Skill evolution review subagent config."""

import pytest
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.evolution.review.subagent import (
    EVOLUTION_REVIEW_AGENT_NAME,
    build_evolution_review_agent_config,
    build_evolution_review_agent_prompt,
    ensure_evolution_review_agent_config,
    remove_evolution_review_agent_config,
)


class DummyStore:
    pass


class DummyQueryService:
    pass


def test_build_evolution_review_agent_config_is_stable_and_restricted():
    query_service = DummyQueryService()
    config = build_evolution_review_agent_config(
        runtime=EvolutionReviewRuntime(),
        query_service=query_service,
        model=None,
    )

    assert config.agent_card.name == "evolution_reviewer"
    assert "ear_" not in config.agent_card.description
    assert config.mcps == []
    assert config.skills is None
    assert config.rails == []
    assert config.max_iterations == 10
    tool_names = [tool.card.name for tool in config.tools]
    assert tool_names == [
        "list_skill_experiences",
        "read_skill_experiences",
        "list_trajectory_steps",
        "read_trajectory_steps",
        "submit_evolution_review",
    ]
    assert "evolve_skill_experiences" not in tool_names
    assert "simplify_skill_experiences" not in tool_names
    assert "task_tool" not in tool_names
    assert config.system_prompt


def test_build_evolution_review_agent_config_supports_english_prompt():
    query_service = DummyQueryService()
    config = build_evolution_review_agent_config(
        runtime=EvolutionReviewRuntime(),
        query_service=query_service,
        model=None,
        language="en",
    )

    assert config.system_prompt
    assert config.agent_card.description


def test_build_evolution_review_agent_config_accepts_custom_max_iterations():
    config = build_evolution_review_agent_config(
        runtime=EvolutionReviewRuntime(),
        query_service=DummyQueryService(),
        model=None,
        max_iterations=18,
    )

    assert config.max_iterations == 18


def test_build_evolution_review_agent_prompt_supports_cn_and_en():
    cn_prompt = build_evolution_review_agent_prompt("cn")
    en_prompt = build_evolution_review_agent_prompt("en")

    assert cn_prompt
    assert en_prompt
    assert cn_prompt != en_prompt


def test_build_evolution_review_agent_config_does_not_accept_non_model_string():
    config = build_evolution_review_agent_config(
        runtime=EvolutionReviewRuntime(),
        store=DummyStore(),
        model="test-model-name",
    )

    assert config.model is None


def test_build_evolution_review_agent_config_passes_query_service_to_tools():
    runtime = EvolutionReviewRuntime()
    query_service = DummyQueryService()

    config = build_evolution_review_agent_config(
        runtime=runtime,
        query_service=query_service,
        model=None,
    )

    tools = {tool.card.name: tool for tool in config.tools}
    assert tools["list_skill_experiences"]._query_service is query_service
    assert tools["read_skill_experiences"]._query_service is query_service


def test_build_evolution_review_agent_config_uses_general_subject_schema_for_tools():
    config = build_evolution_review_agent_config(
        runtime=EvolutionReviewRuntime(),
        query_service=DummyQueryService(),
        model=None,
    )

    tools = {tool.card.name: tool for tool in config.tools}
    subject_schema = tools["submit_evolution_review"].card.input_params["properties"]["subject"]

    assert subject_schema["properties"]["kind"]["enum"] == ["skill", "swarm-skill"]


def test_evolution_review_agent_tool_ids_are_scoped_per_runtime():
    config_a = build_evolution_review_agent_config(
        runtime=EvolutionReviewRuntime(),
        store=DummyStore(),
        model=None,
        agent_id="parent-agent",
    )
    config_b = build_evolution_review_agent_config(
        runtime=EvolutionReviewRuntime(),
        store=DummyStore(),
        model=None,
        agent_id="parent-agent",
    )

    names_a = [tool.card.name for tool in config_a.tools]
    names_b = [tool.card.name for tool in config_b.tools]
    ids_a = {tool.card.id for tool in config_a.tools}
    ids_b = {tool.card.id for tool in config_b.tools}

    assert names_a == names_b
    assert ids_a.isdisjoint(ids_b)
    assert all("parent_agent_evolution_review" in tool_id for tool_id in ids_a | ids_b)


def test_evolution_review_agent_config_helpers_are_deterministic():
    config = build_evolution_review_agent_config(
        runtime=EvolutionReviewRuntime(),
        store=DummyStore(),
        model=None,
    )

    subagents = ensure_evolution_review_agent_config([], config)
    same = ensure_evolution_review_agent_config(subagents, config)

    assert [item.agent_card.name for item in subagents] == ["evolution_reviewer"]
    assert same is subagents
    assert [item.agent_card.name for item in same] == ["evolution_reviewer"]
    assert remove_evolution_review_agent_config(subagents) == []


def test_evolution_review_agent_config_ensure_keeps_same_binding():
    runtime = EvolutionReviewRuntime()
    query_service = DummyQueryService()
    store = DummyStore()
    config_a = build_evolution_review_agent_config(
        runtime=runtime,
        query_service=query_service,
        store=store,
        model=None,
    )
    config_b = build_evolution_review_agent_config(
        runtime=runtime,
        query_service=query_service,
        store=store,
        model=None,
    )

    subagents = ensure_evolution_review_agent_config([], config_a)
    subagents = ensure_evolution_review_agent_config(subagents, config_b)

    assert len(subagents) == 1
    assert [item.agent_card.name for item in subagents] == [EVOLUTION_REVIEW_AGENT_NAME]


def test_evolution_review_agent_config_ensure_fails_with_runtime_mismatch():
    runtime_a = EvolutionReviewRuntime()
    runtime_b = EvolutionReviewRuntime()
    query_service = DummyQueryService()
    store = DummyStore()
    config_a = build_evolution_review_agent_config(
        runtime=runtime_a,
        query_service=query_service,
        store=store,
        model=None,
    )
    config_b = build_evolution_review_agent_config(
        runtime=runtime_b,
        query_service=query_service,
        store=store,
        model=None,
    )

    subagents = ensure_evolution_review_agent_config([], config_a)

    with pytest.raises(RuntimeError, match="binding mismatch"):
        ensure_evolution_review_agent_config(subagents, config_b)


def test_evolution_review_agent_config_ensure_fails_with_query_service_mismatch():
    runtime = EvolutionReviewRuntime()
    store = DummyStore()
    config_a = build_evolution_review_agent_config(
        runtime=runtime,
        query_service=DummyQueryService(),
        store=store,
        model=None,
    )
    config_b = build_evolution_review_agent_config(
        runtime=runtime,
        query_service=DummyQueryService(),
        store=store,
        model=None,
    )

    subagents = ensure_evolution_review_agent_config([], config_a)

    with pytest.raises(RuntimeError, match="binding mismatch"):
        ensure_evolution_review_agent_config(subagents, config_b)


def test_evolution_review_agent_config_ensure_fails_with_store_mismatch():
    runtime = EvolutionReviewRuntime()
    query_service = DummyQueryService()
    config_a = build_evolution_review_agent_config(
        runtime=runtime,
        query_service=query_service,
        store=DummyStore(),
        model=None,
    )
    config_b = build_evolution_review_agent_config(
        runtime=runtime,
        query_service=query_service,
        store=DummyStore(),
        model=None,
    )

    subagents = ensure_evolution_review_agent_config([], config_a)

    with pytest.raises(RuntimeError, match="binding mismatch"):
        ensure_evolution_review_agent_config(subagents, config_b)


def test_evolution_package_exports_stable_review_runtime_and_agent_helpers():
    from openjiuwen.harness.rails.evolution import (
        EVOLUTION_REVIEW_AGENT_NAME,
        EvolutionReviewRuntime,
        build_evolution_review_agent_config,
        ensure_evolution_review_agent_config,
        remove_evolution_review_agent_config,
    )

    assert EVOLUTION_REVIEW_AGENT_NAME == "evolution_reviewer"
    assert EvolutionReviewRuntime.__name__ == "EvolutionReviewRuntime"
    assert callable(build_evolution_review_agent_config)
    assert callable(ensure_evolution_review_agent_config)
    assert callable(remove_evolution_review_agent_config)


def test_binding_metadata_is_attached_to_config():
    runtime = EvolutionReviewRuntime()
    store = DummyStore()
    config = build_evolution_review_agent_config(
        runtime=runtime,
        store=store,
        model=None,
    )
    from openjiuwen.harness.rails.evolution.review.subagent import _get_review_agent_binding

    binding = _get_review_agent_binding(config)
    assert binding is not None
    assert binding.runtime is runtime
    assert binding.store is store


def test_same_binding_not_same_instance_still_matches():
    runtime = EvolutionReviewRuntime()
    store = DummyStore()
    config_a = build_evolution_review_agent_config(runtime=runtime, store=store, model=None)
    config_b = build_evolution_review_agent_config(runtime=runtime, store=store, model=None)

    subagents = ensure_evolution_review_agent_config([], config_a)
    result = ensure_evolution_review_agent_config(subagents, config_b)
    assert result is subagents


def test_hand_written_config_without_metadata_fails():
    from openjiuwen.harness.schema.config import SubAgentConfig
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    hand_written = SubAgentConfig(
        agent_card=AgentCard(name="evolution_reviewer", description="hand-written"),
        system_prompt="test",
    )
    subagents = [hand_written]
    config = build_evolution_review_agent_config(
        runtime=EvolutionReviewRuntime(),
        store=DummyStore(),
        model=None,
    )
    with pytest.raises(RuntimeError, match="lacks binding metadata"):
        ensure_evolution_review_agent_config(subagents, config)


def test_set_and_get_binding_helpers_roundtrip():
    from openjiuwen.harness.rails.evolution.review.subagent import (
        _get_review_agent_binding,
        _set_review_agent_binding,
    )
    from openjiuwen.harness.schema.config import SubAgentConfig
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    config = SubAgentConfig(
        agent_card=AgentCard(name="test", description="test"),
        system_prompt="test",
    )
    runtime = EvolutionReviewRuntime()
    _set_review_agent_binding(config, runtime=runtime, query_service=None, store=None)
    binding = _get_review_agent_binding(config)
    assert binding is not None
    assert binding.runtime is runtime
