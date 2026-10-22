# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import re
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import McpServerConfig, ToolCard
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
    SUBAGENT_TASK_TIMEOUT_ENV,
    resolve_subagent_task_timeout_s,
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
        self.assertEqual(result.data, {"output": "done", "agent_id": "test_id"})
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

    async def test_dispatch_hook_forwards_explicit_session(self) -> None:
        calls = []
        child_session = Session(session_id="child_session")
        inputs = {"query": "run task"}

        class InvokeOnlySubAgent:
            async def invoke(self, received_inputs, *, session):
                calls.append(("invoke", received_inputs, session))
                return {"output": "done"}

        class StreamingSubAgent:
            async def stream(self, received_inputs, *, session):
                calls.append(("stream", received_inputs, session))
                yield {
                    "type": "answer",
                    "payload": {"output": "done", "result_type": "answer"},
                }

        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=SimpleNamespace(),
        )
        for subagent in (InvokeOnlySubAgent(), StreamingSubAgent()):
            result = await tool._invoke_subagent(
                subagent,
                inputs,
                parent_session_id="parent_session",
                session=child_session,
            )
            self.assertEqual(result["output"], "done")

        self.assertEqual(
            calls,
            [("invoke", inputs, child_session), ("stream", inputs, child_session)],
        )

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

    async def test_cua_delegation_timeout_returns_a_resumable_result(self) -> None:
        """A cua run that outlives its budget must not lose its session.

        The desktop agent checkpoints every snapshot/action under the sub
        session; a bare timeout error made the coordinator restart from
        scratch (live: 12 minutes of Discord navigation discarded). The tool
        must instead hand back resume_task_id with status=timeout.
        """
        cleanup_calls = 0

        class FakeSubAgent:
            card = AgentCard(name="cua_agent", description="cua", id="cua_id")

            async def invoke(self, _inputs):
                await asyncio.sleep(60)

            async def cleanup_task_resources(self) -> None:
                nonlocal cleanup_calls
                cleanup_calls += 1

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FakeSubAgent(),
        )
        card = ToolCard(id="task_tool_test", name="task_tool", description="test")
        # Ceiling of 0.4s -> budget max(0.4-20, 0.2) = 0.2s: the inner timeout
        # must fire well before a 60s subagent finishes.
        card.properties = {"resilience": {"timeout_s": 0.4}}
        tool = TaskTool(card=card, parent_agent=parent_agent)

        result = await asyncio.wait_for(
            tool.invoke(
                {"subagent_type": "cua_agent", "task_description": "open the settings window"},
                session=Session(session_id="parent_session"),
            ),
            timeout=5,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "timeout")
        self.assertTrue(result.data["retryable"])
        self.assertRegex(result.data["resume_task_id"], r"^parent_session_sub_cua_agent_[0-9a-f]{8}$")
        self.assertIn("resume_task_id", result.data["output"])
        self.assertEqual(cleanup_calls, 1)

    async def test_non_cua_delegations_keep_raising_on_the_hard_timeout(self) -> None:
        class FakeSubAgent:
            card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, _inputs):
                await asyncio.sleep(60)

            async def cleanup_task_resources(self) -> None:
                return None

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FakeSubAgent(),
        )
        card = ToolCard(id="task_tool_test", name="task_tool", description="test")
        card.properties = {"resilience": {"timeout_s": 0.4}}
        tool = TaskTool(card=card, parent_agent=parent_agent)

        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(
                tool.invoke(
                    {"subagent_type": "code", "task_description": "run task"},
                    session=Session(session_id="parent_session"),
                ),
                timeout=0.5,
            )

    def test_subagent_task_timeout_env_override(self) -> None:
        with patch.dict("os.environ", {SUBAGENT_TASK_TIMEOUT_ENV: "1800"}):
            self.assertEqual(resolve_subagent_task_timeout_s(), 1800.0)
        for bad in ("", "abc", "0", "-5"):
            with patch.dict("os.environ", {SUBAGENT_TASK_TIMEOUT_ENV: bad}):
                self.assertEqual(resolve_subagent_task_timeout_s(), DEFAULT_SUBAGENT_TASK_TIMEOUT_S)

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

    async def test_task_tool_rejects_type_reserved_for_runtime(self) -> None:
        parent_agent = SimpleNamespace(create_subagent=lambda *_args, **_kwargs: None)
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
            allowed_subagent_types={"browser_agent"},
        )

        with self.assertRaisesRegex(Exception, "not available through task_tool"):
            await tool.invoke(
                {"subagent_type": "code", "task_description": "run task"},
                session=Session(session_id="parent_session"),
            )

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
            "missing_slots": [{"entity": "product", "variant": "default", "field": "product_rating"}],
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
        parent_session = Session(session_id="parent_session")
        started_at = time.time()
        parent_session.update_state(
            {
                "__browser_query_delegation_state__": {
                    "original-query": {
                        "query_id": "original-query",
                        "sub_session_id": resume_id,
                        "status": "partial",
                        "retryable": True,
                        "resume_count": 0,
                        "started_at": started_at,
                        "deadline_at": started_at + 600,
                        "budget_s": 600.0,
                        "browser_result": browser_result,
                    }
                }
            }
        )

        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()):
            result = await tool.invoke(
                {
                    "subagent_type": "browser_agent",
                    "task_description": "Only collect the missing product rating",
                    "browser_capabilities": [],
                    "resume_task_id": resume_id,
                },
                session=parent_session,
            )

        self.assertTrue(result.success)
        self.assertEqual(called_inputs["conversation_id"], resume_id)
        run_context = parent_agent._normalize_inputs(called_inputs).run_context.extra
        self.assertTrue(run_context["browser_resume"])
        self.assertEqual(run_context["resume_task_id"], resume_id)
        self.assertEqual(run_context["browser_query_id"], "original-query")
        self.assertEqual(run_context["browser_query_started_at"], started_at)
        self.assertEqual(run_context["browser_query_deadline_at"], started_at + 600)
        self.assertTrue(result.data["retryable"])
        self.assertEqual(result.data["browser_result"], browser_result)
        self.assertEqual(result.data["resume_context"]["missing_fields"], ["product_rating"])

    async def test_browser_query_allows_only_one_focused_resume_with_shared_deadline(self) -> None:
        calls: list[dict[str, object]] = []
        partial_result = {
            "status": "partial",
            "retryable": True,
            "missing_fields": ["product_rating"],
            "missing_slots": [{"entity": "product", "variant": "default", "field": "product_rating"}],
            "blockers": [],
            "evidence": [{"field": "title", "value": "Keyboard"}],
            "recommended_recovery": "read_product_rating_on_current_page",
        }
        completed_result = {
            "status": "completed",
            "retryable": False,
            "missing_fields": [],
            "missing_slots": [],
            "blockers": [],
            "evidence": [
                {"field": "title", "value": "Keyboard"},
                {"field": "product_rating", "value": "4.8"},
            ],
        }

        class FakeSubAgent:
            card = AgentCard(name="browser_agent", description="browser", id="browser_id")

            async def invoke(self, invoke_inputs: dict[str, object]) -> dict[str, object]:
                calls.append(dict(invoke_inputs))
                browser_result = partial_result if len(calls) == 1 else completed_result
                return {
                    "output": "browser result",
                    "authoritative_browser_result": browser_result,
                }

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FakeSubAgent(),
        )
        tool = TaskTool(
            card=ToolCard(id="task_tool_test", name="task_tool", description="test"),
            parent_agent=parent_agent,
        )
        session = Session(session_id="parent_session")

        with patch(
            "openjiuwen.harness.tools.subagent.task_tool.current_usage_invocation_id",
            return_value="main-query-1",
        ):
            first = await tool.invoke(
                {
                    "subagent_type": "browser_agent",
                    "task_description": "Search Taobao and return title and product rating",
                    "browser_capabilities": [],
                },
                session=session,
            )
            second = await tool.invoke(
                {
                    "subagent_type": "browser_agent",
                    "task_description": "Try the whole Taobao search again with another selector",
                    "browser_capabilities": [],
                },
                session=session,
            )
            third = await tool.invoke(
                {
                    "subagent_type": "browser_agent",
                    "task_description": "Verify everything one more time",
                    "browser_capabilities": [],
                },
                session=session,
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(first.data["query_id"], "main-query-1")
        self.assertIn("retained source observations", calls[1]["query"])
        self.assertIn("Focused repair instruction", calls[1]["query"])
        self.assertIn("do not repeat satisfied work or expand scope", calls[1]["query"])
        self.assertEqual(calls[0]["conversation_id"], calls[1]["conversation_id"])
        normalizer = DeepAgent(AgentCard(name="normalizer"))
        first_context = normalizer._normalize_inputs(calls[0]).run_context.extra
        second_context = normalizer._normalize_inputs(calls[1]).run_context.extra
        self.assertFalse(first_context["browser_resume"])
        self.assertTrue(second_context["browser_resume"])
        self.assertEqual(
            first_context["browser_query_deadline_at"],
            second_context["browser_query_deadline_at"],
        )
        self.assertEqual(second.data["browser_result"]["status"], "completed")
        self.assertEqual(third.data["code"], "browser_query_resume_not_allowed")
        self.assertFalse(third.data["retryable"])

    async def test_task_tool_creates_fresh_cua_model_session(self) -> None:
        called_inputs: dict[str, str] = {}

        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
                called_inputs.update(inputs)
                return {"output": "done"}

        cua_spec = SubAgentConfig(
            agent_card=AgentCard(name="cua_agent", description="cua subagent"),
            system_prompt="sub",
        )
        parent_agent = DeepAgent(AgentCard(name="parent", description="test"))
        parent_agent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[cua_spec],
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
                    "subagent_type": "cua_agent",
                    "task_description": "open notepad and type hello",
                },
                session=session,
            )

        self.assertTrue(result.success)
        cua_session_id = called_inputs["conversation_id"]
        self.assertRegex(cua_session_id, r"^parent_session_sub_cua_agent_[0-9a-f]{8}$")
        self.assertEqual(result.data["resume_task_id"], cua_session_id)
        mock_create_subagent.assert_called_once_with("cua_agent", cua_session_id)

    def test_cua_session_can_resume_only_with_returned_parent_scoped_id(self) -> None:
        resume_id = "parent_session_sub_cua_agent_1234abcd"
        self.assertEqual(
            TaskTool._build_sub_session_id(
                "parent_session",
                "cua_agent",
                resume_id,
            ),
            resume_id,
        )
        with self.assertRaisesRegex(ValueError, "not valid"):
            TaskTool._build_sub_session_id(
                "another_parent",
                "cua_agent",
                resume_id,
            )

    async def test_cua_resume_passes_structured_context_and_result_metadata(self) -> None:
        called_inputs: dict[str, object] = {}
        cua_result = {
            "status": "blocked",
            "current_window": {"pid": 4242, "window_id": "w1"},
            "blockers": ["type_text: element not found"],
            "revisit_count": 3,
            "recommended_recovery": (
                "Task appears to be cycling through the same desktop state; retry with a "
                "materially different approach or escalate delivery_mode."
            ),
            "resume_count": 1,
        }

        class FakeSubAgent:
            def __init__(self):
                self.card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, object]) -> dict[str, object]:
                called_inputs.update(inputs)
                return {"output": "still working on it", "cua_result": cua_result}

        cua_spec = SubAgentConfig(
            agent_card=AgentCard(name="cua_agent", description="cua subagent"),
            system_prompt="sub",
        )
        parent_agent = DeepAgent(AgentCard(name="parent", description="test"))
        parent_agent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[cua_spec],
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
        resume_id = "parent_session_sub_cua_agent_1234abcd"

        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()):
            result = await tool.invoke(
                {
                    "subagent_type": "cua_agent",
                    "task_description": "keep trying to close the dialog",
                    "resume_task_id": resume_id,
                },
                session=Session(session_id="parent_session"),
            )

        self.assertTrue(result.success)
        self.assertEqual(called_inputs["conversation_id"], resume_id)
        self.assertEqual(
            called_inputs["run"],
            {"context": {"extra": {"cua_resume": True, "resume_task_id": resume_id}}},
        )
        self.assertTrue(result.data["retryable"])
        self.assertEqual(result.data["cua_result"], cua_result)
        self.assertEqual(result.data["resume_context"]["blockers"], cua_result["blockers"])
        self.assertEqual(result.data["resume_context"]["current_window"], cua_result["current_window"])

    async def test_cua_resume_query_carries_the_last_reported_context(self) -> None:
        """A cua resume must reach the subagent as a focused continuation.

        browser_agent rewrites its resume around unresolved slots; cua passed
        the parent's description through untouched, so the resumed run knew
        nothing of the window, blockers or recovery hint its own last run
        reported and tended to start the task over.
        """
        queries: list[object] = []
        cua_result = {
            "status": "blocked",
            "current_window": {"pid": 4242, "window_id": "w1"},
            "blockers": ["type_text: element not found"],
            "revisit_count": 0,
            "recommended_recovery": "escalate delivery_mode",
            "resume_count": 1,
        }

        class FakeSubAgent:
            card = AgentCard(name="test_agent", description="test", id="test_id")

            async def invoke(self, inputs: dict[str, object]) -> dict[str, object]:
                queries.append(inputs["query"])
                return {"output": "still working on it", "cua_result": cua_result}

        cua_spec = SubAgentConfig(
            agent_card=AgentCard(name="cua_agent", description="cua subagent"),
            system_prompt="sub",
        )
        parent_agent = DeepAgent(AgentCard(name="parent", description="test"))
        parent_agent.configure(
            DeepAgentConfig(
                system_prompt="parent",
                subagents=[cua_spec],
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
        session = Session(session_id="parent_session")

        with patch.object(parent_agent, "create_subagent", return_value=FakeSubAgent()):
            first = await tool.invoke(
                {"subagent_type": "cua_agent", "task_description": "close the dialog"},
                session=session,
            )
            resumed = await tool.invoke(
                {
                    "subagent_type": "cua_agent",
                    "task_description": "dismiss the remaining dialog",
                    "resume_task_id": first.data["resume_task_id"],
                },
                session=session,
            )

        self.assertTrue(resumed.success)
        self.assertEqual(queries[0], "close the dialog")
        self.assertIn("Resume the same desktop task", queries[1])
        self.assertIn('"pid": 4242', queries[1])
        self.assertIn("type_text: element not found", queries[1])
        self.assertIn("escalate delivery_mode", queries[1])
        self.assertTrue(queries[1].endswith("Remaining task: dismiss the remaining dialog"))

    async def test_cua_timeout_resume_warns_about_the_unrecorded_last_action(self) -> None:
        """A cancelled cua run must not be resumed as if it stopped cleanly.

        anyio.fail_after cancels the delegation wherever it is, including
        inside a drag or type_text the daemon then completes anyway, and the
        driver keeps no last-action record to ask. The timeout result and the
        resumed query must both say so, or the resumed run repeats the action
        blindly (double-typed text, a second drag).
        """
        queries: list[object] = []

        class FakeSubAgent:
            card = AgentCard(name="cua_agent", description="cua", id="cua_id")

            async def invoke(self, inputs):
                queries.append(inputs["query"])
                if len(queries) == 1:
                    await asyncio.sleep(60)
                return {"output": "done"}

            async def cleanup_task_resources(self) -> None:
                pass

        parent_agent = SimpleNamespace(
            create_subagent=lambda *_args, **_kwargs: FakeSubAgent(),
        )
        card = ToolCard(id="task_tool_test", name="task_tool", description="test")
        card.properties = {"resilience": {"timeout_s": 0.4}}
        tool = TaskTool(card=card, parent_agent=parent_agent)
        session = Session(session_id="parent_session")

        timed_out = await asyncio.wait_for(
            tool.invoke(
                {"subagent_type": "cua_agent", "task_description": "drag the file into the folder"},
                session=session,
            ),
            timeout=5,
        )
        self.assertFalse(timed_out.data["resume_context"]["last_action_confirmed"])
        self.assertIn("may have executed without being recorded", timed_out.data["output"])

        resumed = await asyncio.wait_for(
            tool.invoke(
                {
                    "subagent_type": "cua_agent",
                    "task_description": "finish moving the file",
                    "resume_task_id": timed_out.data["resume_task_id"],
                },
                session=session,
            ),
            timeout=5,
        )
        self.assertTrue(resumed.success)
        self.assertIn("cut off mid-run", queries[1])
        self.assertIn("verify whether that action already took effect", queries[1])
        self.assertTrue(queries[1].endswith("Remaining task: finish moving the file"))


class TestTaskToolSync(unittest.TestCase):
    def test_sub_session_id_deterministic_for_resumable_subagents(self) -> None:
        """Resumable specialists must get the same sub-session on every delegation.

        verification relies on this for FAIL -> fix -> re-verify loops. browser_agent
        and cua_agent are intentionally excluded: their session/driver state is owned
        by a service registry outside the model session, and a sticky model session
        here would leak an earlier, unrelated delegation's context into a fresh
        TaskTool call. Both instead offer an explicit resume_task_id opt-in (see
        _EXPLICIT_RESUME_SUBAGENT_TYPES) for the one case that still wants continuity.
        """
        for resumable in ("verification_agent",):
            self.assertEqual(
                TaskTool._build_sub_session_id("parent", resumable),
                f"parent_sub_{resumable}",
            )
        for non_sticky in ("browser_agent", "cua_agent", "code_agent"):
            first = TaskTool._build_sub_session_id("parent", non_sticky)
            second = TaskTool._build_sub_session_id("parent", non_sticky)
            self.assertIsNotNone(re.fullmatch(rf"parent_sub_{non_sticky}_[0-9a-f]{{8}}", first))
            self.assertNotEqual(first, second)

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

    def test_create_task_tool_propagates_allowed_subagent_types(self) -> None:
        tools = create_task_tool(
            parent_agent=SimpleNamespace(deep_config=None),
            available_agents="browser_agent",
            language="cn",
            allowed_subagent_types={"browser_agent"},
        )

        self.assertEqual(
            tools[0]._allowed_subagent_types,
            frozenset({"browser_agent"}),
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
