# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Fork and nested runtime are built from the live caller."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail
from openjiuwen.harness.subagent_runtime.binding import (
    SpawnFrame,
    ToolBinding,
    pop_creation,
    push_creation,
)
from openjiuwen.harness.subagent_runtime.fork_build import register_inherited_tool_filter
from openjiuwen.harness.subagent_runtime.fork_rail import ForkInstructionRail


def _model(name: str) -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test-base",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model=name),
    )


def _msg(role: str, content: str, tool_calls: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, tool_calls=tool_calls)


@pytest_asyncio.fixture
async def runner():
    await Runner.start()
    yield
    await Runner.stop()


def _parent():
    return create_deep_agent(
        model=_model("caller-model"),
        card=AgentCard(name="parent", description="test"),
        system_prompt="沿用这段系统提示",
        tools=[
            ToolCard(id="read_file", name="read_file", description="read"),
            ToolCard(id="send_file_to_user", name="send_file_to_user", description="send"),
        ],
        add_general_purpose_agent=True,
        enable_subagent_runtime=True,
    )


def _open(parent, *, child_id: str, child_depth: int, child_kind: str, task: str = ""):
    frame = SpawnFrame(
        caller_agent=parent,
        caller_depth=child_depth - 1,
        caller_kind="",
        caller_session_id="caller-session",
        child_id=child_id,
        child_depth=child_depth,
        child_kind=child_kind,
        control=SimpleNamespace(config=SimpleNamespace(max_spawn_depth=2)),
        plant_seed=True,
        task_description=task,
    )
    binding = ToolBinding(
        control=object(),
        caller_id=child_id,
        caller_depth=child_depth,
        caller_kind=child_kind,
    )
    return push_creation(frame, binding)


@pytest.mark.asyncio
async def test_general_purpose_runtime_opens_only_under_the_depth_cap(runner) -> None:
    parent = _parent()
    spec = parent._find_subagent_spec("general-purpose")
    assert spec is not None and spec.can_delegate is True

    plain = parent.create_subagent("general-purpose", "plain")
    assert plain.deep_config.enable_subagent_runtime is False

    tokens = _open(parent, child_id="gp-1", child_depth=1, child_kind="general-purpose")
    try:
        nested = parent.create_subagent("general-purpose", "gp-1")
    finally:
        pop_creation(*tokens)
    await nested._ensure_initialized()
    assert nested.deep_config.enable_subagent_runtime is True
    assert [item.agent_card.name for item in nested.deep_config.subagents] == ["fork"]
    spawn = next(
        tool
        for rail in nested.configured_rails()
        if isinstance(rail, SubagentRail)
        for tool in (rail.tools or [])
        if tool.card.name == "subagent_spawn"
    )
    assert spawn._allowed_subagent_types == frozenset({"fork"})

    tokens = _open(parent, child_id="gp-deep", child_depth=2, child_kind="general-purpose")
    try:
        capped = parent.create_subagent("general-purpose", "gp-deep")
    finally:
        pop_creation(*tokens)
    assert capped.deep_config.enable_subagent_runtime is False


@pytest.mark.asyncio
async def test_fork_copies_caller_and_keeps_task_out_of_the_seed(runner) -> None:
    parent = _parent()
    parent.get_current_context = lambda session_id=None, context_id="default_context_id": [
        _msg("system", "不要进种子"),
        _msg("user", "读方案"),
        _msg("assistant", "要点是三块"),
        _msg("assistant", "", [{"id": "spawn"}]),
    ]
    register_inherited_tool_filter(
        lambda tools: [tool for tool in tools if getattr(tool, "name", "") != "send_file_to_user"]
    )
    tokens = _open(
        parent,
        child_id="fork-1",
        child_depth=2,
        child_kind="fork",
        task="做第 2 页：方案",
    )
    try:
        fork = parent.create_subagent("fork", "fork-1", model=_model("override-model"))
    finally:
        pop_creation(*tokens)
        register_inherited_tool_filter(None)
    await fork._ensure_initialized()

    assert fork.deep_config.model is parent.deep_config.model
    assert fork.deep_config.system_prompt == "沿用这段系统提示"
    assert [tool.name for tool in fork.deep_config.tools] == ["read_file"]
    rail = fork.find_rails_by_type((ForkInstructionRail,))[0]
    assert rail.task_description == "做第 2 页：方案"
    assert [message.content for message in rail.pending_seed] == ["读方案", "要点是三块"]
    spawn = next(
        tool
        for item in fork.configured_rails()
        if isinstance(item, SubagentRail)
        for tool in (item.tools or [])
        if tool.card.name == "subagent_spawn"
    )
    assert spawn._runtime_binding.caller_kind == "fork"
