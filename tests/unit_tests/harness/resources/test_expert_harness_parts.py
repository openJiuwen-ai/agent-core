# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExpertHarness canonicalize + resolve contracts (no live agent)."""

from __future__ import annotations

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.resources.expert_harness_parts import (
    canonicalize_expert_harness_spec,
    resolve_expert_harness_parts,
)
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec, RailSpec, SubAgentSpec
from openjiuwen.harness.schema.expert_harness_spec import ExpertHarnessConfigSpec, ExpertHarnessSpec

pytestmark = pytest.mark.level0


def _fake_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test-base",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )


def test_canonicalize_maps_sysop_tool_groups_and_ask_user() -> None:
    """Canonicalize maps fs/shell/todo/ask_user tool decls into rails."""
    spec = ExpertHarnessSpec(
        id="canon",
        tools=[
            BuiltinToolSpec(type="core.filesystem"),
            BuiltinToolSpec(type="core.shell"),
            BuiltinToolSpec(type="core.ask_user"),
            BuiltinToolSpec(type="core.todo"),
        ],
        rails=[RailSpec(type="core.security")],
    )
    out = canonicalize_expert_harness_spec(spec)
    assert out.tools == []
    rail_types = {r.type for r in out.rails}
    assert "core.sys_operation" in rail_types
    assert "core.ask_user" in rail_types
    assert "core.task_planning" in rail_types
    assert "core.security" in rail_types


def test_canonicalize_maps_subagent_short_names() -> None:
    """Canonicalize maps core.explore_agent short name to core.subagent.explore_agent."""
    spec = ExpertHarnessSpec(
        id="canon_sub",
        config=ExpertHarnessConfigSpec(enable_subagent=True),
        subagents=[
            SubAgentSpec(
                agent_card=AgentCard(name="explore", description=""),
                system_prompt="",
                factory_name="core.explore_agent",
            )
        ],
    )
    out = canonicalize_expert_harness_spec(spec)
    assert out.subagents[0].factory_name == "core.subagent.explore_agent"
    subagent_rails = [r for r in out.rails if r.type == "core.subagent"]
    assert len(subagent_rails) == 1
    assert subagent_rails[0].params == {"enable_async_subagent": False}


def test_canonicalize_async_subagent_rail_params() -> None:
    """Canonicalize maps subagent_delegate_type=async to enable_async_subagent=True."""
    spec = ExpertHarnessSpec(
        id="canon_async_sub",
        config=ExpertHarnessConfigSpec(enable_subagent=True, subagent_delegate_type="async"),
        subagents=[
            SubAgentSpec(
                agent_card=AgentCard(name="custom", description=""),
                system_prompt="You are custom.",
            )
        ],
    )
    out = canonicalize_expert_harness_spec(spec)
    subagent_rails = [r for r in out.rails if r.type == "core.subagent"]
    assert len(subagent_rails) == 1
    assert subagent_rails[0].params == {"enable_async_subagent": True}


def test_resolve_expert_harness_parts_builds_ask_user_with_source_root() -> None:
    """resolve_expert_harness_parts materializes Parts (incl. source_root) without a live agent."""
    from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRail

    model = _fake_model()
    ctx = BuildContext(language="en", extras={"_parent_model": model, "source_root": "."})
    spec = ExpertHarnessSpec(
        id="resolve_ask",
        tools=[BuiltinToolSpec(type="core.ask_user")],
        rails=[RailSpec(type="core.task_completion")],
    )
    parts = resolve_expert_harness_parts(spec, ctx)
    assert parts.tools == []
    assert any(isinstance(r, AskUserRail) for r in parts.rails)
    assert parts.rails
    assert ctx.extras.get("source_root") == "."
