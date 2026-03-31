# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
import re

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import ToolCard, McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents import create_deep_agent
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.schema.config import DeepAgentConfig, SubAgentConfig
from openjiuwen.deepagents.tools.task_tool import TaskTool, create_task_tool


def _create_dummy_model() -> Model:
    """Minimal Model for unit tests (same pattern as test_deep_agent)."""
    model_client_config = ModelClientConfig(
        client_provider="OpenAI",
        api_key="test-key",
        api_base="http://test-base",
        verify_ssl=False,
    )
    model_config = ModelRequestConfig(model="test-model")
    return Model(model_client_config=model_client_config, model_config=model_config)


class TestTaskTool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await Runner.start()

    async def asyncTearDown(self) -> None:
        await Runner.stop()

    async def test_task_tool_invoke_success(self) -> None:
        called_inputs: dict[str, str] = {}

        class FakeSubAgent:
            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                called_inputs.update(inputs)
                return {"output": "done"}

        # Match production: subagent_type must correspond to a SubAgentConfig.agent_card.name
        code_spec = SubAgentConfig(
            agent_card=AgentCard(name="code", description="code subagent"),
            system_prompt="sub",
        )
        parent_agent = DeepAgent(AgentCard(name="parent", description="test"))
        parent_agent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[code_spec],
                tools=[],
                mcps=[],
                model=None,
                skills=[],
            )
        )

        card = ToolCard(id="task_tool_test", name="task_tool", description="test")
        tool = TaskTool(card=card, parent_agent=parent_agent)

        session = Session(session_id="parent_session")
        with patch.object(tool, "_create_subagent", return_value=FakeSubAgent()):
            result = await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=session,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"output": "done"})
        self.assertIsNone(result.error)
        self.assertEqual(called_inputs["query"], "run task")
        # task_tool: f"{parent_session_id}_sub_{subagent_type}_{uuid.uuid4().hex[:8]}"
        self.assertIsNotNone(
            re.fullmatch(
                r"parent_session_sub_code_[0-9a-f]{8}",
                called_inputs["conversation_id"],
            ),
        )

    async def test_task_tool_invoke_invalid_session(self) -> None:
        parent_agent = SimpleNamespace(deep_config=None)
        card = ToolCard(id="task_tool_test", name="task_tool", description="test")
        tool = TaskTool(card=card, parent_agent=parent_agent)

        with self.assertRaisesRegex(Exception, "valid session"):
            await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session="not-session",
            )

    async def test_task_tool_invoke_missing_required_fields(self) -> None:
        parent_agent = SimpleNamespace(deep_config=None)
        card = ToolCard(id="task_tool_test", name="task_tool", description="test")
        tool = TaskTool(card=card, parent_agent=parent_agent)

        session = Session(session_id="parent_session")
        with self.assertRaisesRegex(Exception, "required"):
            await tool.invoke({"subagent_type": "code"}, session=session)


class TestTaskToolSync(unittest.TestCase):
    def test_create_task_tool(self) -> None:
        parent_agent = SimpleNamespace(deep_config=None)
        tools = create_task_tool(
            parent_agent=parent_agent,
            available_agents="code,search",
            language="cn",
        )

        self.assertEqual(len(tools), 1)
        self.assertIsInstance(tools[0], TaskTool)

    def test_general_purpose_subagent_inherits_parent_mcps(self) -> None:
        tools = [ToolCard(id="parent_tool", name="read_file", description="read file")]
        mcps = [
            McpServerConfig(
                server_name="parent_mcp",
                server_id="mcp_parent_001",
                server_path="http://127.0.0.1:8930/mcp",
            )
        ]
        model = _create_dummy_model()
        parent_agent = create_deep_agent(
            model=model,
            card=AgentCard(name="parent", description="test"),
            system_prompt="parent prompt",
            tools=tools,
            mcps=mcps,
            skills=["skill_a"],
            subagents=[],
            add_general_purpose_agent=True,
        )

        sub = parent_agent.create_subagent("general-purpose", "sub_session_id")

        self.assertEqual(sub.deep_config.tools, tools)
        self.assertEqual(sub.deep_config.mcps, mcps)

    def test_explicit_general_purpose_subagent_overrides_default(self) -> None:
        explicit_spec = SubAgentConfig(
            agent_card=AgentCard(
                name="general-purpose",
                description="custom general subagent",
            ),
            system_prompt="custom prompt",
            tools=[
                ToolCard(id="custom_tool", name="custom_tool", description="custom tool")
            ],
            mcps=[
                McpServerConfig(
                    server_name="custom_mcp",
                    server_id="custom_mcp_001",
                    server_path="http://127.0.0.1:8931/mcp",
                )
            ],
            skills=["skill_b"],
        )
        parent_agent = create_deep_agent(
            model=_create_dummy_model(),
            card=AgentCard(name="parent", description="test"),
            system_prompt="parent prompt",
            tools=[ToolCard(id="parent_tool", name="read_file", description="read file")],
            mcps=[],
            skills=["skill_a"],
            subagents=[explicit_spec],
            add_general_purpose_agent=True,
        )

        sub = parent_agent.create_subagent("general-purpose", "sub_session_id")

        self.assertEqual(sub.deep_config.tools, explicit_spec.tools)
        self.assertEqual(sub.deep_config.mcps, explicit_spec.mcps)
        self.assertEqual(sub.deep_config.skills, explicit_spec.skills)


if __name__ == "__main__":
    unittest.main()
