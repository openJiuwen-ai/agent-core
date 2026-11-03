# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Nesting gates, seed cutoff, and caller scope for fork."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.harness.subagent_runtime.seed import finished_seed
from tests.unit_tests.harness.subagent_runtime.test_control import (
    ControlParentAgent,
    _patched_control,
)
from tests.unit_tests.harness.subagent_runtime.test_instance import MockAgent
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.models import SubagentStatusKind


def _msg(role: str, content: str, tool_calls: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, tool_calls=tool_calls)


def test_finished_seed_drops_the_spawn_turn() -> None:
    messages = [
        _msg("user", "读方案"),
        _msg("assistant", "", [{"id": "1"}]),
        _msg("tool", "正文"),
        _msg("assistant", "要点是三块"),
        _msg("user", "做成三页"),
        _msg("assistant", "", [{"id": "2"}]),
    ]
    seed = finished_seed(messages)
    assert [message.content for message in seed] == ["读方案", "", "正文", "要点是三块"]


def test_finished_seed_is_empty_when_the_only_turn_is_open() -> None:
    messages = [
        _msg("user", "现在就开始"),
        _msg("assistant", "", [{"id": "1"}]),
    ]
    assert finished_seed(messages) == []


@pytest.mark.asyncio
async def test_fork_caller_cannot_spawn() -> None:
    async with _patched_control() as control:
        with pytest.raises(Exception, match="Fork subagents cannot spawn subagents"):
            await control.spawn("explore", "again", caller_kind="fork", caller_depth=2)


@pytest.mark.asyncio
async def test_spawn_depth_limit_rejects_before_create() -> None:
    config = SubagentRuntimeConfig(max_spawn_depth=2)
    async with _patched_control(config=config) as control:
        with pytest.raises(Exception, match="Subagent spawn depth limit reached"):
            await control.spawn("explore", "too deep", caller_depth=2)


@pytest.mark.asyncio
async def test_concurrent_limit_rejects_before_a_slot_is_taken() -> None:
    parent = ControlParentAgent(mock_agent=MockAgent(delay_s=0.3))
    config = SubagentRuntimeConfig(max_concurrent_running=1, max_subagents=10)
    async with _patched_control(parent=parent, config=config) as control:
        first = await control.spawn("explore", "running")
        with pytest.raises(Exception, match="Do not retry"):
            await control.spawn("explore", "second")
        assert control._registry.count == 1
        assert control._manager.find(first.subagent_id) is not None


@pytest.mark.asyncio
async def test_nested_caller_cannot_wait_on_a_sibling() -> None:
    async with _patched_control() as control:
        own = await control.spawn("explore", "mine", caller_id="gp-1", caller_depth=1)
        other = await control.spawn("explore", "theirs", caller_id="gp-2", caller_depth=1)
        waited = await control.wait([other.subagent_id], timeout_ms=200, caller_id="gp-1")
        assert waited.statuses[other.subagent_id].kind is SubagentStatusKind.NOT_FOUND
        assert other.subagent_id not in waited.results
        visible = control.describe_list(caller_id="gp-1")
        ids = [row["subagent_id"] for row in visible["live_subagents"]]
        assert ids == [own.subagent_id]
        assert visible["live_subagents"][0]["parent_id"] == "gp-1"
        assert visible["live_subagents"][0]["depth"] == 2
        whole = control.describe_list()
        assert {row["subagent_id"] for row in whole["live_subagents"]} == {
            own.subagent_id,
            other.subagent_id,
        }


@pytest.mark.asyncio
async def test_plant_fork_seed_awaits_update_and_keeps_seed_when_write_fails() -> None:
    from openjiuwen.harness.subagent_runtime.fork_rail import ForkInstructionRail
    from openjiuwen.harness.subagent_runtime.instance import _plant_fork_seed

    planted = ForkInstructionRail("task", ["seed-msg"])

    class _Agent:
        def find_rails_by_type(self, types):
            _ = types
            return [planted]

    class _Session:
        def __init__(self) -> None:
            self.data = None

        async def update_state(self, data):
            self.data = data

    session = _Session()
    await _plant_fork_seed(_Agent(), session)
    assert session.data == {"background_messages": ["seed-msg"]}
    assert planted.pending_seed == []

    kept = ForkInstructionRail("task", ["seed-msg"])

    class _KeptAgent:
        def find_rails_by_type(self, types):
            _ = types
            return [kept]

    class _BadSession:
        def update_state(self, data):
            _ = data
            raise RuntimeError("state rejected")

    await _plant_fork_seed(_KeptAgent(), _BadSession())
    assert kept.pending_seed == ["seed-msg"]


def test_refresh_keeps_fork_leaf_allowed_type() -> None:
    from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail
    from openjiuwen.harness.subagent_runtime.binding import ToolBinding

    class _Card:
        name = "subagent_spawn"
        description = ""

    class _Tool:
        def __init__(self) -> None:
            self.card = _Card()
            self.allowed = None

        def set_allowed_subagent_types(self, types) -> None:
            self.allowed = types

    class _Agent:
        def __init__(self) -> None:
            self.deep_config = SimpleNamespace(subagents=[])
            self._subagent_tool_binding = ToolBinding(
                control=object(),
                caller_id="fork-1",
                caller_depth=2,
                caller_kind="fork",
            )

    rail = SubagentRail(enable_subagent_runtime=True)
    rail.tools = [_Tool()]
    rail.system_prompt_builder = SimpleNamespace(language="cn")
    rail.refresh_available_agents(_Agent())
    assert rail.tools[0].allowed == frozenset({"fork"})
    assert "不能再派" in rail.tools[0].card.description
