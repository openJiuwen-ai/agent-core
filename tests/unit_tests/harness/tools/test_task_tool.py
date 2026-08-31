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
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig
from openjiuwen.harness.tools import TaskTool, create_task_tool
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.subagent.task_tool import _build_success_tool_content
from openjiuwen.core.single_agent.ability_manager import AbilityManager


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
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

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
        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()):
            result = await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=session,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data["output"], "done")
        self.assertEqual(result.data["agent_id"], "test_id")
        self.assertIn("content", result.data)
        self.assertIn("已完成任务", result.data["content"])
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

    async def test_task_tool_reuses_sticky_browser_subsession_id(self) -> None:
        called_inputs: dict[str, str] = {}

        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                called_inputs.update(inputs)
                return {"output": "done"}

        browser_spec = SubAgentConfig(
            agent_card=AgentCard(name="browser_agent", description="browser subagent"),
            system_prompt="sub",
        )
        parent_agent = DeepAgent(AgentCard(name="parent", description="test"))
        parent_agent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[browser_spec],
                tools=[],
                mcps=[],
                model=None,
                skills=[],
            )
        )

        card = ToolCard(id="task_tool_test", name="task_tool", description="test")
        tool = TaskTool(card=card, parent_agent=parent_agent)

        session = Session(session_id="parent_session")
        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()) as mock_create_subagent:
            result = await tool.invoke(
                {
                    "subagent_type": "browser_agent",
                    "task_description": "continue browser task",
                    "browser_capabilities": ["pdf", "vision"],
                },
                session=session,
            )

        self.assertTrue(result.success)
        self.assertEqual(called_inputs["conversation_id"], "parent_session_sub_browser_agent")
        mock_create_subagent.assert_called_once_with(
            "browser_agent",
            "parent_session_sub_browser_agent",
            browser_capabilities=["pdf", "vision"],
        )

    async def test_task_tool_passes_resolved_model_to_create_subagent(self) -> None:
        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")
                self.deep_config = SimpleNamespace(model=None)

            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                return {"output": "done"}

        override = _create_dummy_model()
        parent_agent = DeepAgent(AgentCard(name="parent", description="test"))
        parent_agent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[
                    SubAgentConfig(
                        agent_card=AgentCard(name="code", description="code subagent"),
                        system_prompt="sub",
                    )
                ],
                tools=[],
                mcps=[],
                model=None,
                skills=[],
            )
        )
        parent_agent.resolve_subagent_model = (  # type: ignore[attr-defined]
            lambda *, model_name="", model_tier="": (override, None)
        )

        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )
        session = Session(session_id="parent_session")
        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()) as mock_create:
            result = await tool.invoke(
                {
                    "subagent_type": "code",
                    "task_description": "run task",
                    "model_tier": "lite",
                    "model_name": "",
                },
                session=session,
            )

        self.assertTrue(result.success)
        mock_create.assert_called_once()
        self.assertIs(mock_create.call_args.kwargs.get("model"), override)

    async def test_task_tool_returns_interrupt_dict(self) -> None:
        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, str]) -> dict:
                return {
                    "result_type": "interrupt",
                    "interrupt_ids": ["inner_perm_1"],
                    "state": [],
                }

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
        tool = TaskTool(card=ToolCard(id="task_tool_test", name="task_tool", description="test"), parent_agent=parent_agent)
        session = Session(session_id="parent_session")

        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()) as mock_create:
            result = await tool.invoke(
                {"subagent_type": "code", "task_description": "read external file"},
                session=session,
                tool_call_id="call_interrupt_001",
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "interrupt")
        self.assertIn("interrupt_ids", result)
        mock_create.assert_called_once()

    async def test_task_tool_resume_reuses_pending_subagent(self) -> None:
        create_count = 0

        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict) -> dict:
                if isinstance(inputs.get("query"), dict):
                    return {"output": "file is empty"}
                return {
                    "result_type": "interrupt",
                    "interrupt_ids": ["inner_perm_1"],
                    "state": [],
                }

        def _create_subagent(*_args, **_kwargs):
            nonlocal create_count
            create_count += 1
            return FakeSubAgent()

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
        tool = TaskTool(card=ToolCard(id="task_tool_test", name="task_tool", description="test"), parent_agent=parent_agent)
        session = Session(session_id="parent_session")
        tool_call_id = "call_resume_001"

        with patch.object(parent_agent, "create_subagent", side_effect=_create_subagent):
            interrupt_result = await tool.invoke(
                {"subagent_type": "code", "task_description": "read external file"},
                session=session,
                tool_call_id=tool_call_id,
            )
            self.assertIsInstance(interrupt_result, dict)
            self.assertEqual(interrupt_result.get("result_type"), "interrupt")
            self.assertEqual(create_count, 1)

            resume_result = await tool.invoke(
                {
                    "subagent_type": "code",
                    "task_description": "read external file",
                    "query": {"action": "allow_once"},
                },
                session=session,
                tool_call_id=tool_call_id,
            )

        self.assertIsInstance(resume_result, ToolOutput)
        self.assertTrue(resume_result.success)
        self.assertEqual(resume_result.data["output"], "file is empty")
        self.assertIn("已完成任务", resume_result.data["content"])
        self.assertEqual(create_count, 1)

    async def test_task_tool_sub_session_id_uses_tool_call_id(self) -> None:
        called_inputs: dict[str, str] = {}

        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                called_inputs.update(inputs)
                return {"output": "done"}

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
        tool = TaskTool(card=ToolCard(id="task_tool_test", name="task_tool", description="test"), parent_agent=parent_agent)
        session = Session(session_id="parent_session")
        tool_call_id = "call_stable_session"

        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()):
            await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=session,
                tool_call_id=tool_call_id,
            )

        self.assertEqual(
            called_inputs["conversation_id"],
            f"parent_session_sub_code_{tool_call_id}",
        )

    async def test_task_tool_english_success_content(self) -> None:
        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                return {"output": "hello"}

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
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
            language="en",
        )
        session = Session(session_id="parent_session")

        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()):
            result = await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=session,
            )

        self.assertTrue(result.success)
        self.assertIn("completed successfully", result.data["content"])
        self.assertIn("hello", result.data["content"])

    async def test_task_tool_empty_output_success_content(self) -> None:
        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                return {"output": ""}

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
        tool = TaskTool(card=ToolCard(id="task_tool_test", name="task_tool", description="test"), parent_agent=parent_agent)
        session = Session(session_id="parent_session")

        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()):
            result = await tool.invoke(
                {"subagent_type": "code", "task_description": "read empty file"},
                session=session,
            )

        self.assertTrue(result.success)
        self.assertIn("已完成任务", result.data["content"])
        self.assertNotIn("\n\n", result.data["content"])


class TestTaskToolSuccessContent(unittest.TestCase):
    def test_build_success_tool_content_cn(self) -> None:
        content = _build_success_tool_content(
            "file is empty",
            subagent_type="general-purpose",
            language="cn",
        )
        self.assertIn("已完成任务", content)
        self.assertIn("file is empty", content)
        self.assertIn("task_tool", content)

    def test_build_success_tool_content_en(self) -> None:
        content = _build_success_tool_content(
            "",
            subagent_type="code",
            language="en",
        )
        self.assertIn("completed successfully", content)
        self.assertIn("Do NOT call task_tool", content)

    def test_build_success_tool_content_whitespace_only_output(self) -> None:
        content = _build_success_tool_content(
            "   ",
            subagent_type="general-purpose",
            language="cn",
        )
        self.assertIn("已完成任务", content)
        self.assertNotIn("\n\n", content)

    def test_ability_manager_uses_task_tool_content_field(self) -> None:
        tool_output = ToolOutput(
            success=True,
            data={
                "content": _build_success_tool_content("payload", subagent_type="code", language="cn"),
                "output": "payload",
                "agent_id": "agent-1",
            },
            error=None,
        )
        rendered = AbilityManager._build_tool_message_content(tool_output)
        self.assertIn("已完成任务", rendered)
        self.assertIn("payload", rendered)
        self.assertNotIn("ToolOutput", rendered)


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

    def test_create_subagent_model_override_wins_over_spec(self) -> None:
        parent_model = _create_dummy_model()
        override = Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="override-key",
                api_base="http://override-base",
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(model="override-model"),
        )
        parent_agent = create_deep_agent(
            model=parent_model,
            card=AgentCard(name="parent", description="test"),
            system_prompt="parent prompt",
            tools=[],
            mcps=[],
            skills=[],
            subagents=[],
            add_general_purpose_agent=True,
        )

        sub = parent_agent.create_subagent(
            "general-purpose",
            "sub_session_id",
            model=override,
        )
        self.assertIs(sub.deep_config.model, override)


if __name__ == "__main__":
    unittest.main()

