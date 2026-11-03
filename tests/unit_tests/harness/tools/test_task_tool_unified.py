"""Independent task dispatch uses one protocol for every built-in subagent."""
import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.core.runner import Runner  # noqa: F401
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.subagent.task_tool import TaskTool, create_task_tool


@pytest.mark.parametrize("language", ["cn", "en"])
def test_default_tool_exposes_only_group_arguments(language):
    specs = [SimpleNamespace(agent_card=SimpleNamespace(name=name))
             for name in ("general-purpose", "browser_agent", "verification_agent")]
    parent = SimpleNamespace(card=SimpleNamespace(id="parent"), deep_config=SimpleNamespace(subagents=specs))
    schema = create_task_tool(parent, "available", language)[0].card.input_params
    assert set(schema["properties"]) == {"action", "tasks", "task_ids", "timeout_s"}
    assert schema["required"] == ["action"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["tasks"]["items"]["properties"]["subagent_type"]["enum"] == [
        "general-purpose", "browser_agent", "verification_agent",
    ]


@pytest.mark.asyncio
async def test_old_single_task_arguments_are_rejected():
    tool = TaskTool(ToolCard(id="tool", name="task_tool", description="test"), SimpleNamespace())
    result = await tool.invoke(
        {"subagent_type": "general-purpose", "task_description": "old"},
        session=Session(session_id="parent"), tool_call_id="old",
    )
    assert not result.success and result.error == "invalid_action"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["general-purpose", "browser_agent", "verification_agent"])
async def test_two_new_tasks_of_same_type_are_independent_and_parallel(kind):
    started = asyncio.Queue()
    gate = asyncio.Event()
    created = []

    def create(subagent_type, session_id, **kwargs):
        assert subagent_type == kind
        assert kwargs.pop("isolated") is True
        assert kwargs == ({"browser_capabilities": []} if kind == "browser_agent" else {})
        child = SimpleNamespace(card=SimpleNamespace(id=session_id), cleaned=False)

        async def invoke(inputs):
            await started.put(inputs["conversation_id"])
            await gate.wait()
            return {"output": inputs["query"]}

        async def cleanup():
            child.cleaned = True

        child.invoke, child.cleanup_task_resources = invoke, cleanup
        created.append(child)
        return child

    tool = TaskTool(ToolCard(id="tool", name="task_tool", description="test"),
                    SimpleNamespace(card=SimpleNamespace(id="parent"), create_subagent=create))
    tasks = [{"subagent_type": kind, "task_description": name} for name in ("A", "B")]
    if kind == "browser_agent":
        for task in tasks:
            task["browser_capabilities"] = []
    session = Session(session_id="parent")
    request = asyncio.create_task(tool.invoke(
        {"action": "dispatch", "tasks": tasks}, session=session, tool_call_id="batch",
    ))
    try:
        first = await asyncio.wait_for(started.get(), 2)
        second = await asyncio.wait_for(started.get(), 2)
        assert first != second and not request.done()
        gate.set()
        result = await asyncio.wait_for(request, 2)
        outputs = {item["output"] for item in result.data["ready"]}
        while result.data["pending_ids"]:
            result = await tool.invoke({"action": "wait", "task_ids": result.data["pending_ids"]},
                                       session=session, tool_call_id="collect")
            outputs.update(item["output"] for item in result.data["ready"])
        assert outputs == {"A", "B"}
        assert len(created) == 2 and all(child.cleaned for child in created)
    finally:
        gate.set()
        await tool.close_task_groups("parent")
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("unregister", [False, True])
async def test_parent_stop_reaches_tasks_owned_by_review_tool(tmp_path, unregister):
    from openjiuwen.agent_evolving.tools.skill import EvolveReviewTaskTool
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
    from openjiuwen.harness import create_deep_agent
    started, gate = asyncio.Event(), asyncio.Event()
    async def invoke(inputs):
        started.set()
        await gate.wait()
        return {"output": "reviewed"}
    async def cleanup():
        pass
    parent = create_deep_agent(
        Model(model_client_config=ModelClientConfig(client_provider="OpenAI", api_key="test", api_base="http://test.invalid/v1"),
              model_config=ModelRequestConfig(model="test")),
        workspace=str(tmp_path), auto_create_workspace=False, enable_read_image_multimodal=False,
    )
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.schema.config import SubAgentConfig
    from openjiuwen.harness.rails.evolution.review.subagent import EVOLUTION_REVIEW_AGENT_NAME
    parent.deep_config.subagents = [SubAgentConfig(agent_card=AgentCard(name=EVOLUTION_REVIEW_AGENT_NAME), system_prompt="Review independently")]
    parent.create_subagent = lambda *args, **kwargs: SimpleNamespace(invoke=invoke, cleanup_task_resources=cleanup)
    tool = EvolveReviewTaskTool(parent_agent=parent, language="en", agent_id="parent")
    parent.ability_manager.add_ability(tool.card, tool)
    operation = asyncio.create_task(tool.invoke(
        {"evolution_review_ref": "review-ref"}, session=Session(session_id="parent"), tool_call_id="review",
    ))
    try:
        await asyncio.wait_for(started.wait(), 2)
        if unregister:
            parent.ability_manager.remove_ability(tool.card.name)
        report = await parent.cancel_background_subtasks("parent")
        assert report and report["tasks"][0]["status"] == "cancelled"
        result = await asyncio.wait_for(operation, 2)
        assert not result.success
        assert not tool._task_tool.task_manager._runs
    finally:
        gate.set()
        await tool._task_tool.close_task_groups("parent")
        await asyncio.gather(operation, return_exceptions=True)
        parent.ability_manager.teardown_tools()
