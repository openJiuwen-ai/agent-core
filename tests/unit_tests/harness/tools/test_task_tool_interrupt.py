# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TaskTool surfacing sub-agent interrupts to its caller."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.subagent.task_tool import TaskTool


def _interrupt_envelope() -> dict:
    """The dict shape an agent returns from ``commit_interrupt``."""
    return {
        "result_type": "interrupt",
        "state": [],
        "interrupt_ids": ["inner_call_1"],
    }


class _FakeSubAgent:
    """Subagent returning a scripted result and recording its inputs."""

    def __init__(self, result: dict) -> None:
        self.card = AgentCard(id="sub", name="sub", description="sub")
        self._result = result
        self.inputs: list[dict] = []

    async def invoke(self, inputs: dict) -> dict:
        self.inputs.append(dict(inputs))
        return self._result


def _make_tool(subagent: _FakeSubAgent) -> TaskTool:
    """A TaskTool whose parent has KV-cache affinity switched off."""
    parent = SimpleNamespace(
        deep_config=SimpleNamespace(model=None, kv_cache_affinity_config=None),
        create_subagent=lambda *_args, **_kwargs: subagent,
    )
    return TaskTool(ToolCard(id="task_tool", name="task_tool", description="task"), parent)


async def _invoke(subagent: _FakeSubAgent):
    return await _make_tool(subagent).invoke(
        {"subagent_type": "code", "task_description": "run task"},
        session=Session(session_id="parent_session"),
    )


@pytest.mark.asyncio
async def test_interrupt_result_is_passed_through_unflattened() -> None:
    """The envelope reaches the caller intact, not as an empty success."""
    result = await _invoke(_FakeSubAgent(_interrupt_envelope()))

    assert result == _interrupt_envelope()
    assert result["result_type"] == "interrupt"
    assert result["interrupt_ids"] == ["inner_call_1"]


@pytest.mark.asyncio
async def test_interrupt_result_is_recognised_by_the_interrupt_handler() -> None:
    """The producer emits exactly the shape the consuming handler tests for."""
    result = await _invoke(_FakeSubAgent(_interrupt_envelope()))

    assert ToolInterruptHandler._is_sub_agent_interrupt(result) is True


@pytest.mark.asyncio
async def test_normal_result_is_unaffected() -> None:
    """A completed subagent still returns a flattened successful ToolOutput."""
    result = await _invoke(_FakeSubAgent({"output": "done"}))

    assert result.success is True
    assert result.data == {"output": "done", "agent_id": "sub"}
    assert ToolInterruptHandler._is_sub_agent_interrupt(result) is False


@pytest.mark.asyncio
async def test_partial_interrupt_shape_is_not_treated_as_an_interrupt() -> None:
    """A result without ``interrupt_ids`` is a normal result, not an interrupt."""
    result = await _invoke(_FakeSubAgent({"result_type": "interrupt", "output": "done"}))

    assert result.success is True
    assert result.data["output"] == "done"
