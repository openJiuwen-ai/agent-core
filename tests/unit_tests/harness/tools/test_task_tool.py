"""Validation and failure propagation for the unified TaskTool protocol."""
from types import SimpleNamespace

import pytest

from openjiuwen.core.runner import Runner  # noqa: F401
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.subagent.task_tool import TaskTool, create_task_tool


@pytest.mark.parametrize("language", ["cn", "en"])
def test_factory_exposes_one_task_tool(language):
    parent = SimpleNamespace(card=SimpleNamespace(id="parent"), deep_config=SimpleNamespace(subagents=[]))
    tools = create_task_tool(parent, "general-purpose: independent work", language)
    assert len(tools) == 1 and tools[0].card.name == "task_tool"
    assert "independent work" in tools[0].card.description
    assert tools[0].card.input_params["required"] == ["action"]


@pytest.mark.asyncio
@pytest.mark.parametrize("inputs,error", [
    ({}, "invalid_action"),
    ({"subagent_type": "general-purpose", "task_description": "old"}, "invalid_action"),
    ({"action": "dispatch", "tasks": []}, "nonempty bounded"),
    ({"action": "dispatch", "tasks": [{"subagent_type": "general-purpose"}]}, "type and description"),
    ({"action": "wait", "task_ids": []}, "requires task_ids"),
    ({"action": "cancel", "task_ids": ["A"], "tasks": []}, "requires task_ids"),
    ({"action": "dispatch", "subagent_type": "general-purpose", "tasks": []}, "do not mix"),
    ([], "inputs must be an object"),
])
async def test_invalid_inputs_return_tool_error(inputs, error):
    tool = TaskTool(ToolCard(id="task", name="task_tool", description="test"), SimpleNamespace())
    result = await tool.invoke(inputs, session=Session(session_id="parent"), tool_call_id="invalid")
    assert not result.success and error in result.error


@pytest.mark.asyncio
async def test_unknown_type_lists_registered_names():
    parent = SimpleNamespace(resolve_subagent_type=lambda _: None,
                             available_subagent_types=lambda: ["general-purpose"])
    tool = TaskTool(ToolCard(id="task", name="task_tool", description="test"), parent, language="en")
    result = await tool.invoke(
        {"action": "dispatch", "tasks": [{"subagent_type": "unknown", "task_description": "work"}]},
        session=Session(session_id="parent"), tool_call_id="unknown",
    )
    assert not result.success
    assert "unknown" in result.error and "general-purpose" in result.error


@pytest.mark.asyncio
async def test_child_failure_preserves_error_and_cleans_resources():
    cleaned = []
    async def invoke(inputs):
        raise RuntimeError("child boom")
    async def cleanup():
        cleaned.append(True)
    child = SimpleNamespace(invoke=invoke, cleanup_task_resources=cleanup)
    parent = SimpleNamespace(create_subagent=lambda *args, **kwargs: child)
    tool = TaskTool(ToolCard(id="task", name="task_tool", description="test"), parent)
    try:
        result = await tool.invoke(
            {"action": "dispatch", "tasks": [{"subagent_type": "general-purpose", "task_description": "work"}]},
            session=Session(session_id="parent"), tool_call_id="failure",
        )
        assert result.data["ready"][0]["status"] == "failed"
        assert "child boom" in str(result.data["ready"][0]["error"])
        assert cleaned == [True]
    finally:
        await tool.close_task_groups("parent")
