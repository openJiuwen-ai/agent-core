# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import re

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import ToolCard, McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig
from openjiuwen.harness.tools import TaskTool, create_task_tool
from openjiuwen.harness.tools.subagent.task_tool import (
    DEFAULT_SUBAGENT_TASK_TIMEOUT_S,
)


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
        prepare_calls = 0
        cleanup_calls = 0

        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                called_inputs.update(inputs)
                return {"output": "done"}

            async def prepare_task_resources(self) -> None:
                nonlocal prepare_calls
                prepare_calls += 1

            async def cleanup_task_resources(self) -> None:
                nonlocal cleanup_calls
                cleanup_calls += 1

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
        self.assertEqual(result.data, {"output": "done", 'agent_id': 'test_id'})
        self.assertIsNone(result.error)
        self.assertEqual(called_inputs["query"], "run task")
        self.assertEqual(prepare_calls, 1)
        self.assertEqual(cleanup_calls, 1)
        # task_tool: f"{parent_session_id}_sub_{subagent_type}_{uuid.uuid4().hex[:8]}"
        self.assertIsNotNone(
            re.fullmatch(
                r"parent_session_sub_code_[0-9a-f]{8}",
                called_inputs["conversation_id"],
            ),
        )

    async def test_task_tool_preserves_interrupt_and_resumes_subsession(self) -> None:
        invoked_inputs: list[dict] = []
        interrupt_result = {
            "result_type": "interrupt",
            "interrupt_ids": ["inner-call"],
            "state": [],
        }

        class FakeSubAgent:
            card = AgentCard(name="test_agent", description="test", id="test_id")

            def __init__(self) -> None:
                self.invoke_count = 0
                self.cleanup_count = 0

            async def invoke(self, inputs):
                invoked_inputs.append(inputs)
                self.invoke_count += 1
                if self.invoke_count == 1:
                    return interrupt_result
                return {"output": "resumed"}

            async def cleanup_task_resources(self) -> None:
                self.cleanup_count += 1

        created_subagents: list[FakeSubAgent] = []

        def create_subagent(*_args, **_kwargs):
            subagent = FakeSubAgent()
            created_subagents.append(subagent)
            return subagent

        parent_agent = SimpleNamespace(
            create_subagent=create_subagent,
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )
        session = Session(session_id="parent_session")

        first_result = await tool.invoke(
            {"subagent_type": "code", "task_description": "run task"},
            session=session,
            tool_call_id="outer-call",
        )
        self.assertIs(first_result, interrupt_result)
        self.assertEqual(created_subagents[0].cleanup_count, 0)

        approval = InteractiveInput()
        approval.update("inner-call", {"approved": True})
        result = await tool.invoke(
            {
                "subagent_type": "code",
                "task_description": "run task",
                "query": approval,
            },
            session=session,
            tool_call_id="outer-call",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["output"], "resumed")
        self.assertEqual(len(created_subagents), 1)
        self.assertEqual(created_subagents[0].cleanup_count, 1)
        self.assertEqual(
            [item["conversation_id"] for item in invoked_inputs],
            [
                "parent_session_sub_code_outer-call",
                "parent_session_sub_code_outer-call",
            ],
        )
        self.assertIs(invoked_inputs[1]["query"], approval)

    async def test_task_tool_cleans_up_after_subagent_failure(self) -> None:
        cleanup_calls = 0

        class FakeSubAgent:
            card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, _inputs):
                raise RuntimeError("subagent failed")

            async def cleanup_task_resources(self) -> None:
                nonlocal cleanup_calls
                cleanup_calls += 1

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FakeSubAgent(),
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )

        with self.assertRaisesRegex(Exception, "subagent failed"):
            await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=Session(session_id="parent_session"),
            )
        self.assertEqual(cleanup_calls, 1)

    async def test_task_tool_cleans_up_after_cancellation(self) -> None:
        cleanup_calls = 0

        class FakeSubAgent:
            card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, _inputs):
                raise asyncio.CancelledError

            async def cleanup_task_resources(self) -> None:
                nonlocal cleanup_calls
                cleanup_calls += 1

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FakeSubAgent(),
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )

        with self.assertRaises(asyncio.CancelledError):
            await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=Session(session_id="parent_session"),
            )
        self.assertEqual(cleanup_calls, 1)

    async def test_task_tool_cleans_up_when_outer_timeout_cancels_invoke(self) -> None:
        cleanup_calls = 0

        class FakeSubAgent:
            card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, _inputs):
                await asyncio.sleep(60)

            async def cleanup_task_resources(self) -> None:
                nonlocal cleanup_calls
                cleanup_calls += 1

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FakeSubAgent(),
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )

        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(
                tool.invoke(
                    {"subagent_type": "code", "task_description": "run task"},
                    session=Session(session_id="parent_session"),
                ),
                timeout=0.01,
            )
        self.assertEqual(cleanup_calls, 1)

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

    async def test_task_tool_maps_general_agent_alias(self) -> None:
        created: list[str] = []

        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="gp", description="test", id="gp_id")

            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                return {"output": "done"}

            async def prepare_task_resources(self) -> None:
                return None

            async def cleanup_task_resources(self) -> None:
                return None

        spec = SubAgentConfig(
            agent_card=AgentCard(name="general-purpose", description="gp"),
            system_prompt="sub",
        )
        parent_agent = DeepAgent(AgentCard(name="parent", description="test"))
        parent_agent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[spec],
                tools=[],
                mcps=[],
                model=None,
                skills=[],
            )
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )

        def _create(subagent_type, *_args, **_kwargs):
            created.append(subagent_type)
            return FakeSubAgent()

        with patch.object(parent_agent, "create_subagent", side_effect=_create):
            result = await tool.invoke(
                {"subagent_type": "general_agent", "task_description": "research"},
                session=Session(session_id="parent_session"),
            )

        self.assertTrue(result.success)
        self.assertEqual(created, ["general-purpose"])

    async def test_task_tool_unknown_type_returns_available_names(self) -> None:
        spec = SubAgentConfig(
            agent_card=AgentCard(name="research_agent", description="research"),
            system_prompt="sub",
        )
        parent_agent = DeepAgent(AgentCard(name="parent", description="test"))
        parent_agent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[spec],
                tools=[],
                mcps=[],
                model=None,
                skills=[],
            )
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )

        result = await tool.invoke(
            {"subagent_type": "general-purpose", "task_description": "research"},
            session=Session(session_id="parent_session"),
        )

        self.assertFalse(result.success)
        self.assertIn("research_agent", result.error)
        self.assertIn("general-purpose", result.error)


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
        self.assertEqual(
            tools[0].card.properties["resilience"]["timeout_s"],
            DEFAULT_SUBAGENT_TASK_TIMEOUT_S,
        )
        self.assertEqual(
            AbilityManager._resolve_call_timeout(tools[0].card),
            DEFAULT_SUBAGENT_TASK_TIMEOUT_S,
        )

    def test_create_task_tool_writes_subagent_type_enum(self) -> None:
        spec = SubAgentConfig(
            agent_card=AgentCard(name="general-purpose", description="gp"),
            system_prompt="sub",
        )
        parent_agent = DeepAgent(AgentCard(name="parent", description="test"))
        parent_agent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[spec],
                tools=[],
                mcps=[],
                model=None,
                skills=[],
            )
        )
        tools = create_task_tool(
            parent_agent=parent_agent,
            available_agents="- general-purpose: gp",
            language="cn",
        )
        enum_names = tools[0].card.input_params["properties"]["subagent_type"]["enum"]
        self.assertEqual(enum_names, ["general-purpose"])

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

