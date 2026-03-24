# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.tool import ToolCard, McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.deepagents.tools.task_tool import TaskTool, create_task_tool


class InspectableTaskTool(TaskTool):
    def find_subagent_spec(self, subagent_type: str):
        return self._find_subagent_spec(subagent_type)


@pytest_asyncio.fixture(name="runner_started")
async def runner_started_fixture():
    await Runner.start()
    yield
    await Runner.stop()


@pytest.mark.asyncio
async def test_task_tool_invoke_success(runner_started):
    parent_agent = SimpleNamespace(deep_config=None)
    card = ToolCard(id="task_tool_test", name="task_tool", description="test")
    
    called_inputs: dict[str, str] = {}

    class FakeSubAgent:
        async def invoke(self, inputs):
            called_inputs.update(inputs)
            return {"output": "done"}

    class TestTaskTool(TaskTool):
        def _create_subagent(self, subagent_type: str):
            return FakeSubAgent()
    
    tool = TestTaskTool(card=card, parent_agent=parent_agent)

    session = Session(session_id="parent_session")
    result = await tool.invoke(
        {"subagent_type": "code", "task_description": "run task"},
        session=session,
    )

    assert result.success is True
    assert result.data == {"output": "done"}
    assert result.error is None
    assert called_inputs["query"] == "run task"
    assert called_inputs["conversation_id"].startswith("parent_session_sub_code_")


@pytest.mark.asyncio
async def test_task_tool_invoke_invalid_session(runner_started):
    parent_agent = SimpleNamespace(deep_config=None)
    card = ToolCard(id="task_tool_test", name="task_tool", description="test")
    tool = TaskTool(card=card, parent_agent=parent_agent)

    with pytest.raises(Exception, match="valid session"):
        await tool.invoke(
            {"subagent_type": "code", "task_description": "run task"},
            session="not-session",
        )


@pytest.mark.asyncio
async def test_task_tool_invoke_missing_required_fields(runner_started):
    parent_agent = SimpleNamespace(deep_config=None)
    card = ToolCard(id="task_tool_test", name="task_tool", description="test")
    tool = TaskTool(card=card, parent_agent=parent_agent)

    session = Session(session_id="parent_session")
    with pytest.raises(Exception, match="required"):
        await tool.invoke({"subagent_type": "code"}, session=session)


def test_create_task_tool():
    parent_agent = SimpleNamespace(deep_config=None)
    tools = create_task_tool(
        parent_agent=parent_agent,
        available_agents="code,search",
        language="cn",
    )

    assert len(tools) == 1
    assert isinstance(tools[0], TaskTool)


def test_general_purpose_subagent_inherits_parent_mcps():
    parent_agent = SimpleNamespace(
        deep_config=SimpleNamespace(
            subagents=[],
            system_prompt="parent prompt",
            tools=[ToolCard(id="parent_tool", name="read_file", description="read file")],
            mcps=[
                McpServerConfig(
                    server_name="parent_mcp",
                    server_id="mcp_parent_001",
                    server_path="http://127.0.0.1:8930/mcp",
                )
            ],
            model=None,
            skills=["skill_a"],
        )
    )
    tool = InspectableTaskTool(
        card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
        parent_agent=parent_agent,
    )

    spec = tool.find_subagent_spec("general-purpose")

    assert spec is not None
    assert spec.tools == parent_agent.deep_config.tools
    assert spec.mcps == parent_agent.deep_config.mcps


def test_explicit_general_purpose_subagent_overrides_default():
    explicit_spec = SubAgentConfig(
        agent_card=AgentCard(name="general-purpose", description="custom general subagent"),
        system_prompt="custom prompt",
        tools=[ToolCard(id="custom_tool", name="custom_tool", description="custom tool")],
        mcps=[
            McpServerConfig(
                server_name="custom_mcp",
                server_id="custom_mcp_001",
                server_path="http://127.0.0.1:8931/mcp",
            )
        ],
        skills=["skill_b"],
    )
    parent_agent = SimpleNamespace(
        deep_config=SimpleNamespace(
            subagents=[explicit_spec],
            system_prompt="parent prompt",
            tools=[ToolCard(id="parent_tool", name="read_file", description="read file")],
            mcps=[],
            model=None,
            skills=["skill_a"],
        )
    )
    tool = InspectableTaskTool(
        card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
        parent_agent=parent_agent,
    )

    spec = tool.find_subagent_spec("general-purpose")

    assert spec is explicit_spec
