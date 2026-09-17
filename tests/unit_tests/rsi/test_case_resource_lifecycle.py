# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A finished evaluation must not destroy a concurrent case or Judge's tools."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator import case_backend


class _ProbeTool(Tool):
    def __init__(self):
        super().__init__(ToolCard(name="lifecycle_probe", description="Test resource ownership"))

    async def invoke(self, inputs, **kwargs):
        return "still available"

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["success", "error", "cancel"])
async def test_case_cleanup_preserves_concurrent_case_and_judge(tmp_path, monkeypatch, ending):
    agents = []
    tools = []
    both_started = asyncio.Event()
    second_can_finish = asyncio.Event()
    stop = AsyncMock()
    monkeypatch.setattr(Runner, "start", AsyncMock())
    monkeypatch.setattr(Runner, "stop", stop)
    monkeypatch.setattr(case_backend, "_resolve_single_harness_ref", lambda refs: ("solver", "unused"))
    monkeypatch.setattr(case_backend, "load_member_optimizer_model", Mock())
    monkeypatch.setattr(case_backend, "_single_harness_rails", lambda *args, **kwargs: [])
    monkeypatch.setattr(case_backend, "_attach_single_harness_trajectory_rail", Mock())

    def factory(**kwargs):
        card = kwargs["card"]
        manager = AbilityManager(owner_id=card.id)
        tool = _ProbeTool()
        manager.add_ability(tool.card, tool)
        agent = SimpleNamespace(
            card=card, ability_manager=manager,
            cleanup_task_resources=AsyncMock(), load_plugin=AsyncMock(),
        )
        agents.append(agent)
        tools.append(tool)
        return agent

    async def run(agent, inputs, *, session):
        if session == "first":
            await both_started.wait()
            if ending == "error":
                raise RuntimeError("execution failed")
            if ending == "cancel":
                raise asyncio.CancelledError
        else:
            both_started.set()
            await second_can_finish.wait()
        return "done"

    monkeypatch.setattr(case_backend, "create_deep_agent", factory)
    monkeypatch.setattr(case_backend, "run_agent_with_empty_response_recovery", run)
    judge_manager = AbilityManager(owner_id="judge_lifecycle_test")
    judge_tool = _ProbeTool()
    judge_manager.add_ability(judge_tool.card, judge_tool)
    backend = case_backend.SingleHarnessExecutionBackend(EvaluatorConfig())
    tasks = [asyncio.create_task(backend.execute(
        case={"case_id": name, "input": "test"}, output_dir=str(tmp_path / name), session_id=name,
    )) for name in ("first", "second")]
    try:
        if ending == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(tasks[0], timeout=10)
        else:
            result = await asyncio.wait_for(tasks[0], timeout=10)
            assert result.execution_status == ("passed" if ending == "success" else "failed")
        stop.assert_not_awaited()
        agents[0].cleanup_task_resources.assert_awaited_once()
        assert Runner.resource_mgr.get_tool(tools[0].card.id) is None
        assert Runner.resource_mgr.get_tool(tools[1].card.id) is tools[1]
        assert await tools[1].invoke({}) == "still available"
        assert Runner.resource_mgr.get_tool(judge_tool.card.id) is judge_tool
        second_can_finish.set()
        await asyncio.wait_for(tasks[1], timeout=10)
        assert Runner.resource_mgr.get_tool(tools[1].card.id) is None
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for agent in agents:
            agent.ability_manager.teardown_tools()
        judge_manager.teardown_tools()
