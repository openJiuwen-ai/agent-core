# coding: utf-8

"""Tests for drop_unregistered_elements (recovery-time spec hygiene)."""

from __future__ import annotations

import pytest

from openjiuwen.harness.schema import deep_agent_spec as das
from openjiuwen.harness.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    RailSpec,
    SubAgentSpec,
    drop_unregistered_elements,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@pytest.fixture
def sentinel_providers():
    """注册哨兵 provider，测试后摘除，保持注册表干净。"""
    das.register_tool_provider("test.kept_tool", lambda params, context: [])
    das.register_rail_provider("test.kept_rail", lambda params, context: None)
    yield
    das._TOOL_PROVIDER_REGISTRY.pop("test.kept_tool", None)
    das._RAIL_PROVIDER_REGISTRY.pop("test.kept_rail", None)


@pytest.mark.level0
def test_drops_unknown_tool_and_rail_types(sentinel_providers):
    """已退役类型被剥掉、仍注册的类型保留；返回被剥清单供记日志。"""
    spec = DeepAgentSpec(
        tools=[
            BuiltinToolSpec(type="test.kept_tool"),
            BuiltinToolSpec(type="swarm.user_todos"),
        ],
        rails=[
            RailSpec(type="test.kept_rail"),
            RailSpec(type="swarm.retired_rail"),
        ],
    )

    dropped = drop_unregistered_elements(spec)

    assert dropped == {"tools": ["swarm.user_todos"], "rails": ["swarm.retired_rail"]}
    assert [t.type for t in spec.tools] == ["test.kept_tool"]
    assert [r.type for r in spec.rails] == ["test.kept_rail"]


@pytest.mark.level0
def test_subagent_specs_sanitized_recursively(sentinel_providers):
    """subagent 的 tools/rails 同规则递归净化。"""
    spec = DeepAgentSpec(
        subagents=[
            SubAgentSpec(
                agent_card=AgentCard(name="sub", description="d", version="1.0.0"),
                system_prompt="p",
                tools=[BuiltinToolSpec(type="swarm.user_todos")],
                rails=[RailSpec(type="swarm.retired_rail")],
            )
        ],
    )

    dropped = drop_unregistered_elements(spec)

    assert dropped == {"tools": ["swarm.user_todos"], "rails": ["swarm.retired_rail"]}
    assert spec.subagents[0].tools == []
    assert spec.subagents[0].rails == []


@pytest.mark.level0
def test_clean_spec_untouched(sentinel_providers):
    """无退役类型时不改 spec、返回空清单。"""
    spec = DeepAgentSpec(
        tools=[BuiltinToolSpec(type="test.kept_tool")],
        rails=[RailSpec(type="test.kept_rail")],
    )

    dropped = drop_unregistered_elements(spec)

    assert dropped == {"tools": [], "rails": []}
    assert len(spec.tools) == 1
    assert len(spec.rails) == 1
