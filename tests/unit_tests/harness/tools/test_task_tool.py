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
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.execution_subject import current_execution_subject
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

    async def test_task_tool_prefers_stream_and_returns_only_terminal_answer(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        class FakeSubAgent:
            card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, _inputs):
                raise AssertionError("invoke must not be used when the public stream is available")

            async def stream(self, inputs):
                calls.append(("stream", inputs))
                yield SimpleNamespace(
                    type="llm_output",
                    payload={"content": "intermediate"},
                )
                yield SimpleNamespace(
                    type="answer",
                    payload={"output": "final", "result_type": "answer"},
                )

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FakeSubAgent(),
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )

        result = await tool.invoke(
            {"subagent_type": "code", "task_description": "run task"},
            session=Session(session_id="parent_session"),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"output": "final", "agent_id": "test_id"})
        self.assertEqual(calls[0][0], "stream")

    async def test_task_tool_stream_error_preserves_failure_semantics(self) -> None:
        cleanup_calls = 0

        class FakeSubAgent:
            card = AgentCard(name="test_agent", description="test", id="test_id")

            async def stream(self, _inputs):
                yield {
                    "type": "answer",
                    "payload": {"output": "stream failed", "result_type": "error"},
                }

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

        with self.assertRaisesRegex(Exception, "stream failed"):
            await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=Session(session_id="parent_session"),
            )
        self.assertEqual(cleanup_calls, 1)

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

    async def test_task_tool_creates_fresh_browser_model_session(self) -> None:
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
        browser_session_id = called_inputs["conversation_id"]
        self.assertRegex(browser_session_id, r"^parent_session_sub_browser_agent_[0-9a-f]{8}$")
        self.assertEqual(result.data["resume_task_id"], browser_session_id)
        mock_create_subagent.assert_called_once_with(
            "browser_agent",
            browser_session_id,
            browser_capabilities=["pdf", "vision"],
        )

    def test_browser_session_can_resume_only_with_returned_parent_scoped_id(self) -> None:
        resume_id = "parent_session_sub_browser_agent_1234abcd"
        self.assertEqual(
            TaskTool._build_sub_session_id(
                "parent_session",
                "browser_agent",
                resume_id,
            ),
            resume_id,
        )
        with self.assertRaisesRegex(ValueError, "not valid"):
            TaskTool._build_sub_session_id(
                "another_parent",
                "browser_agent",
                resume_id,
            )

    async def test_browser_resume_passes_structured_context_and_result_metadata(self) -> None:
        called_inputs: dict[str, object] = {}
        browser_result = {
            "status": "partial",
            "retryable": True,
            "missing_fields": ["product_rating"],
            "missing_slots": [
                {"entity": "product", "variant": "default", "field": "product_rating"}
            ],
            "blockers": [],
            "evidence": [{"field": "title", "value": "Keyboard"}],
            "current_page": {"url": "https://example.test/item/1"},
            "recommended_recovery": "collect_missing_evidence_from_current_page",
            "resume_count": 0,
        }

        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, object]) -> dict[str, object]:
                called_inputs.update(inputs)
                return {
                    "output": '{"browser_result":{"status":"partial"}}',
                    "authoritative_browser_result": browser_result,
                }

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
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )
        resume_id = "parent_session_sub_browser_agent_1234abcd"

        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()):
            result = await tool.invoke(
                {
                    "subagent_type": "browser_agent",
                    "task_description": "Only collect the missing product rating",
                    "browser_capabilities": [],
                    "resume_task_id": resume_id,
                },
                session=Session(session_id="parent_session"),
            )

        self.assertTrue(result.success)
        self.assertEqual(called_inputs["conversation_id"], resume_id)
        self.assertEqual(
            called_inputs["run_context"],
            {"browser_resume": True, "resume_task_id": resume_id},
        )
        self.assertTrue(result.data["retryable"])
        self.assertEqual(result.data["browser_result"], browser_result)
        self.assertEqual(result.data["resume_context"]["missing_fields"], ["product_rating"])


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
