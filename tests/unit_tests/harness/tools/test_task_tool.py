# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import unittest
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import re

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import ToolCard, McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.execution_subject import current_execution_subject
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig
from openjiuwen.harness.tools import TaskTool, create_task_tool
from openjiuwen.harness.kv_cache import kv_cache_hooks


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
        self.assertEqual(result.data, {"output": "done", 'agent_id': 'test_id'})
        self.assertIsNone(result.error)
        self.assertEqual(called_inputs["query"], "run task")
        # task_tool: f"{parent_session_id}_sub_{subagent_type}_{uuid.uuid4().hex[:8]}"
        self.assertIsNotNone(
            re.fullmatch(
                r"parent_session_sub_code_[0-9a-f]{8}",
                called_inputs["conversation_id"],
            ),
        )

    async def test_repeated_concurrent_calls_get_isolated_execution_subjects(self) -> None:
        observed_subjects = []

        class FakeSubAgent:
            card = AgentCard(name="Explore Agent", description="test", id="explore")

            async def invoke(self, _inputs):
                observed_subjects.append(current_execution_subject())
                await asyncio.sleep(0)
                return {"output": "done"}

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FakeSubAgent(),
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )
        session = Session(session_id="parent_session")

        with patch.object(
            tool,
            "_build_sub_session_id",
            return_value="parent_session_sub_sticky",
        ):
            await asyncio.gather(
                tool.invoke(
                    {"subagent_type": "explore", "task_description": "first"},
                    session=session,
                ),
                tool.invoke(
                    {"subagent_type": "explore", "task_description": "second"},
                    session=session,
                ),
            )

        self.assertEqual(len(observed_subjects), 2)
        self.assertTrue(all(subject is not None for subject in observed_subjects))
        self.assertEqual(
            len({subject.subject_id for subject in observed_subjects}),
            2,
        )
        self.assertEqual(
            {subject.display_name for subject in observed_subjects},
            {"Explore Agent"},
        )
        self.assertEqual(
            {subject.parent_subject_id for subject in observed_subjects},
            {"main"},
        )
        self.assertEqual(
            {subject.session_id for subject in observed_subjects},
            {"parent_session_sub_sticky"},
        )
        self.assertIsNone(current_execution_subject())

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

    async def test_invoke_timeout_returns_failure_and_cancels_subagent(self) -> None:
        invoke_cancelled = asyncio.Event()

        class HangingSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")
                self.deep_config = SimpleNamespace(completion_timeout=0.05)

            async def invoke(self, _inputs):
                try:
                    await asyncio.sleep(60)
                finally:
                    invoke_cancelled.set()

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: HangingSubAgent(),
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )
        session = Session(session_id="parent_session")

        with patch.object(kv_cache_hooks, "affinity_enabled", return_value=True), patch.object(
            kv_cache_hooks, "prefetch_sticky_subagent", Mock()
        ), patch.object(kv_cache_hooks, "finish_subagent", new=AsyncMock()) as mock_finish:
            started = time.monotonic()
            result = await tool.invoke(
                {"subagent_type": "explore", "task_description": "run task"},
                session=session,
            )
            elapsed = time.monotonic() - started

        self.assertFalse(result.success)
        self.assertIn("invoke timeout", result.error)
        self.assertIn("parent_session", result.error)
        self.assertEqual(result.data.get("agent_id"), "test_id")
        self.assertLess(elapsed, 5.0)
        self.assertTrue(invoke_cancelled.is_set())
        mock_finish.assert_awaited_once()
        self.assertFalse(mock_finish.await_args.kwargs.get("succeeded", True))

    async def test_invoke_timeout_falls_back_to_1800s_without_deep_config(self) -> None:
        captured_timeouts: list[float] = []

        async def fake_wait_for(_awaitable, timeout=None):
            captured_timeouts.append(timeout)
            close = getattr(_awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError()

        class NoConfigSubAgent:
            card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, _inputs):  # pragma: no cover - cancelled before use
                await asyncio.sleep(60)

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: NoConfigSubAgent(),
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )
        session = Session(session_id="parent_session")

        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            result = await tool.invoke(
                {"subagent_type": "explore", "task_description": "run task"},
                session=session,
            )

        self.assertEqual(captured_timeouts, [1800.0])
        self.assertFalse(result.success)
        self.assertIn("invoke timeout", result.error)

    async def test_invoke_success_with_completion_timeout_configured(self) -> None:
        class FastSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")
                self.deep_config = SimpleNamespace(completion_timeout=5.0)

            async def invoke(self, _inputs):
                await asyncio.sleep(0)
                return {"output": "done"}

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FastSubAgent(),
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )
        session = Session(session_id="parent_session")

        result = await tool.invoke(
            {"subagent_type": "explore", "task_description": "run task"},
            session=session,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data.get("output"), "done")
        self.assertIsNone(result.error)


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
