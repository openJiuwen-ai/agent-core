# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for ReActAgent workflow interrupt/resume redesign.

Covers three scenarios:
- Scenario 1: 1 workflow, 1 interrupt component (baseline)
- Scenario 2: 1 workflow, 2 parallel interrupt components in the same superstep
- Scenario 3: 2 workflows, each with 1 interrupt component

Mock strategy:
- All questioner components use question_content (no LLM call needed)
- Only ReActAgent LLM calls are mocked via MockLLMModel
- Use run_agent_streaming; collect __interaction__ OutputSchema from stream
- component_id is read from chunk.payload.id
"""
import os
import unittest
from typing import Tuple
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage, ModelClientConfig, ModelRequestConfig, ToolCall, UsageMetadata,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig, ReActAgent
from openjiuwen.core.workflow import (
    End, QuestionerComponent, QuestionerConfig, Start, Workflow, WorkflowCard,
)

from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

CONVERSATION_ID = "test_conv_interrupt"

_MODEL_CLIENT = ModelClientConfig(
    client_provider="OpenAI",
    api_key="sk-fake",
    api_base="https://mock.openai.com/v1",
    verify_ssl=False,
)
_MODEL_REQUEST = ModelRequestConfig(model="gpt-4o-mock", temperature=0.0)


def _make_questioner(question: str) -> QuestionerComponent:
    """Build a questioner with a preset question (no LLM call needed)."""
    cfg = QuestionerConfig(
        model_client_config=_MODEL_CLIENT,
        model_config=_MODEL_REQUEST,
        question_content=question,
        extract_fields_from_response=False,
        with_chat_history=False,
    )
    return QuestionerComponent(questioner_comp_config=cfg)


def _make_agent(*workflow_cards: WorkflowCard) -> ReActAgent:
    """Build a ReActAgent with given workflow abilities."""
    agent_card = AgentCard(id="react_agent_interrupt_test", description="test agent")
    config = ReActAgentConfig(
        model_client_config=_MODEL_CLIENT,
        model_config_obj=_MODEL_REQUEST,
        prompt_template=[{"role": "system", "content": "You are a helpful assistant."}],
    )
    agent = ReActAgent(card=agent_card).configure(config)
    for card in workflow_cards:
        agent.ability_manager.add(card)
    return agent


async def _run_streaming(agent, query, conversation_id) -> Tuple[list, list]:
    """Run agent streaming, return list of __interaction__ OutputSchema chunks."""
    chunks, all_chunks = [], []
    async for chunk in Runner.run_agent_streaming(
        agent=agent,
        inputs={"conversation_id": conversation_id, "query": query},
    ):
        if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
            chunks.append(chunk)
        all_chunks.append(chunk)
    return chunks, all_chunks


class TestScenario1SingleWorkflowSingleInterrupt(unittest.IsolatedAsyncioTestCase):
    """Scenario 1: 1 workflow, 1 interrupt component.

    Topology: s -> questioner -> e
    Flow:
      invoke 1 -> 1 interaction chunk (questioner)
      invoke 2 (feedback for questioner) -> 0 interaction chunks (answer)
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _build_workflow() -> Workflow:
        card = WorkflowCard(id="wf_single", name="wf_single", version="1.0")
        flow = Workflow(card=card)
        flow.set_start_comp("s", Start(), inputs_schema={"query": "${query}"})
        flow.set_end_comp(
            "e",
            End({"responseTemplate": "done: {{user_response}}"}),
            inputs_schema={"user_response": "${questioner.user_response}"},
        )
        flow.add_workflow_comp(
            "questioner", _make_questioner("请问你的姓名是什么？"),
            inputs_schema={"query": "${s.query}"},
        )
        flow.add_connection("s", "questioner")
        flow.add_connection("questioner", "e")
        return flow

    @pytest.mark.asyncio
    async def test_single_interrupt_then_resume(self):
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            # invoke 1: agent decides to call wf_single
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call_001", type="function", name="wf_single",
                                     arguments='{"query": "收集姓名"}')],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            # invoke 2: workflow done, agent summarizes
            create_text_response("任务完成，已收集到用户姓名。"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke), \
             patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()):

            flow = self._build_workflow()
            Runner.resource_mgr.add_workflow(
                WorkflowCard(id="wf_single", name="wf_single", version="1.0"),
                lambda: flow,
            )
            agent = _make_agent(flow.card)

            # --- invoke 1: expect 1 interaction chunk for questioner ---
            conversation_id = "TestScenario1SingleWorkflowSingleInterrupt"
            chunks1, _ = await _run_streaming(agent, "请帮我收集用户姓名", conversation_id)
            self.assertEqual(len(chunks1), 1, f"Expected 1 interaction chunk, got: {chunks1}")
            self.assertEqual(chunks1[0].payload.id, "questioner")

            # --- invoke 2: provide feedback, expect no more interaction chunks, last chunk is answer ---
            user_input = InteractiveInput()
            user_input.update(chunks1[0].payload.id, "张三")
            chunks2, all_chunks2 = await _run_streaming(agent, user_input, conversation_id)
            self.assertEqual(len(chunks2), 0, f"Expected no interaction chunks, got: {chunks2}")
            self.assertGreater(len(all_chunks2), 0, "Expected at least one chunk in final round")
            last_chunk = all_chunks2[-1]
            self.assertIsInstance(last_chunk, OutputSchema)
            self.assertIn("任务完成，已收集到用户姓名。", str(last_chunk.payload), "Last chunk payload is not correct")

            # check get chat history from context
            chat_history = agent.context_engine.get_context(session_id=conversation_id).get_messages()

            # --- checkpoint 1: 7 messages in order ---
            expected_roles = ["user", "assistant", "tool", "user", "assistant", "tool", "assistant"]
            self.assertEqual(len(chat_history), 7,
                             f"Expected 7 messages in chat history, got {len(chat_history)}: {chat_history}")
            for i, (msg, expected_role) in enumerate(zip(chat_history, expected_roles)):
                self.assertEqual(msg.role, expected_role,
                                 f"chat_history[{i}].role expected '{expected_role}', got '{msg.role}'")

            # --- checkpoint 2: chat_history[2] is the placeholder ToolMessage ---
            placeholder = chat_history[2]
            self.assertEqual(placeholder.role, "tool",
                             f"chat_history[2] should be a ToolMessage, got role='{placeholder.role}'")
            self.assertEqual(placeholder.content, "[INTERRUPTED - Waiting for user input]",
                             f"chat_history[2] content mismatch: '{placeholder.content}'")


class TestScenario2SingleWorkflowParallelInterrupt(unittest.IsolatedAsyncioTestCase):
    """Scenario 2: 1 workflow, 2 parallel interrupt components in the same superstep.

    Topology: s -> questioner_1 + questioner_2 -> e
    Flow:
      invoke 1 -> 1 interaction chunk (questioner_1, first pending)
      invoke 2 (feedback for questioner_1) -> 1 interaction chunk (questioner_2)
      invoke 3 (feedback for questioner_2) -> 0 interaction chunks (answer)
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _build_workflow() -> Workflow:
        card = WorkflowCard(id="wf_parallel", name="wf_parallel", version="1.0")
        flow = Workflow(card=card)
        flow.set_start_comp("s", Start(), inputs_schema={"query": "${query}"})
        flow.set_end_comp(
            "e",
            End({"responseTemplate": "姓名:{{name_resp}} 地址:{{addr_resp}}"}),
            inputs_schema={
                "name_resp": "${questioner_1.user_response}",
                "addr_resp": "${questioner_2.user_response}",
            },
        )
        flow.add_workflow_comp(
            "questioner_1", _make_questioner("请问你的姓名是什么？"),
            inputs_schema={"query": "${s.query}"},
        )
        flow.add_workflow_comp(
            "questioner_2", _make_questioner("请问你的地址是什么？"),
            inputs_schema={"query": "${s.query}"},
        )
        flow.add_connection("s", "questioner_1")
        flow.add_connection("s", "questioner_2")
        flow.add_connection("questioner_1", "e")
        flow.add_connection("questioner_2", "e")
        return flow

    @pytest.mark.asyncio
    async def test_parallel_interrupt_sequential_resume(self):
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            # invoke 1: agent calls wf_parallel
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call_p_001", type="function", name="wf_parallel",
                                     arguments='{"query": "收集信息"}')],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            # invoke 3: both questioners done, agent summarizes
            create_text_response("任务完成，已收集到姓名和地址。"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke), \
             patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()):

            flow = self._build_workflow()
            Runner.resource_mgr.add_workflow(
                WorkflowCard(id="wf_parallel", name="wf_parallel", version="1.0"),
                lambda: flow,
            )
            agent = _make_agent(flow.card)

            # --- invoke 1: expect interaction for questioner_1 ---
            conversation_id = "TestScenario2SingleWorkflowParallelInterrupt"
            chunks1, _ = await _run_streaming(agent, "请帮我收集用户信息", conversation_id)
            self.assertEqual(len(chunks1), 1, f"invoke 1: expected 1 interaction chunk, got {chunks1}")
            self.assertEqual(chunks1[0].payload.id, "questioner_1")

            # --- invoke 2: feedback for questioner_1, expect interaction for questioner_2 ---
            user_input_1 = InteractiveInput()
            user_input_1.update(chunks1[0].payload.id, "李四")
            chunks2, _ = await _run_streaming(agent, user_input_1, conversation_id)
            self.assertEqual(len(chunks2), 1, f"invoke 2: expected 1 interaction chunk, got {chunks2}")
            self.assertEqual(chunks2[0].payload.id, "questioner_2")

            # --- invoke 3: feedback for questioner_2, expect no more interactions, last chunk is answer ---
            user_input_2 = InteractiveInput()
            user_input_2.update(chunks2[0].payload.id, "北京市朝阳区")
            chunks3, all_chunks3 = await _run_streaming(agent, user_input_2, conversation_id)
            self.assertEqual(len(chunks3), 0, f"invoke 3: expected no interaction chunks, got {chunks3}")
            self.assertGreater(len(all_chunks3), 0, "Expected at least one chunk in final round")
            last_chunk = all_chunks3[-1]
            self.assertIsInstance(last_chunk, OutputSchema)
            self.assertIsNotNone(last_chunk.payload, "Last chunk payload should not be None")

            chat_history = agent.context_engine.get_context(session_id=conversation_id).get_messages()

            # --- checkpoint 1: 10 messages in order ---
            expected_roles = [
                "user", "assistant", "tool",   # invoke 1: query -> tool_call -> placeholder
                "user", "assistant", "tool",   # invoke 2: feedback q1 -> tool_call -> placeholder
                "user", "assistant", "tool",   # invoke 3: feedback q2 -> tool_call -> result
                "assistant",                   # final answer
            ]
            self.assertEqual(len(chat_history), 10,
                             f"Expected 10 messages in chat history, got {len(chat_history)}: {chat_history}")
            for i, (msg, expected_role) in enumerate(zip(chat_history, expected_roles)):
                self.assertEqual(msg.role, expected_role,
                                 f"chat_history[{i}].role expected '{expected_role}', got '{msg.role}'")

            # --- checkpoint 2: index 2 and 5 are both placeholder ToolMessages ---
            for idx in (2, 5):
                placeholder = chat_history[idx]
                self.assertEqual(placeholder.role, "tool",
                                 f"chat_history[{idx}] should be a ToolMessage, got role='{placeholder.role}'")
                self.assertEqual(placeholder.content, "[INTERRUPTED - Waiting for user input]",
                                 f"chat_history[{idx}] content mismatch: '{placeholder.content}'")


class TestScenario3TwoWorkflowsEachInterrupt(unittest.IsolatedAsyncioTestCase):
    """Scenario 3: 2 workflows, each with 1 interrupt component.

    LLM returns two tool_calls in one turn (wf_a and wf_b).
    Flow:
      invoke 1 -> 1 interaction chunk (wf_a.questioner_a, first pending)
      invoke 2 (feedback for questioner_a) -> 1 interaction chunk (wf_b.questioner_b)
      invoke 3 (feedback for questioner_b) -> both workflows resume concurrently -> 0 interaction chunks
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _build_workflow_a() -> Workflow:
        card = WorkflowCard(id="wf_a", name="wf_a", version="1.0")
        flow = Workflow(card=card)
        flow.set_start_comp("s", Start(), inputs_schema={"query": "${query}"})
        flow.set_end_comp(
            "e", End({"responseTemplate": "姓名:{{name_resp}}"}),
            inputs_schema={"name_resp": "${questioner_a.user_response}"},
        )
        flow.add_workflow_comp(
            "questioner_a", _make_questioner("请问你的姓名是什么？"),
            inputs_schema={"query": "${s.query}"},
        )
        flow.add_connection("s", "questioner_a")
        flow.add_connection("questioner_a", "e")
        return flow

    @staticmethod
    def _build_workflow_b() -> Workflow:
        card = WorkflowCard(id="wf_b", name="wf_b", version="1.0")
        flow = Workflow(card=card)
        flow.set_start_comp("s", Start(), inputs_schema={"query": "${query}"})
        flow.set_end_comp(
            "e", End({"responseTemplate": "地址:{{addr_resp}}"}),
            inputs_schema={"addr_resp": "${questioner_b.user_response}"},
        )
        flow.add_workflow_comp(
            "questioner_b", _make_questioner("请问你的地址是什么？"),
            inputs_schema={"query": "${s.query}"},
        )
        flow.add_connection("s", "questioner_b")
        flow.add_connection("questioner_b", "e")
        return flow

    @pytest.mark.asyncio
    async def test_two_workflows_sequential_interrupt_then_concurrent_resume(self):
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            # invoke 1: agent calls both wf_a and wf_b in one turn
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="call_a_001", type="function", name="wf_a",
                             arguments='{"query": "收集姓名"}'),
                    ToolCall(id="call_b_001", type="function", name="wf_b",
                             arguments='{"query": "收集地址"}'),
                ],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            # invoke 3: both workflows done, agent summarizes
            create_text_response("任务完成，已收集到姓名和地址。"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke), \
             patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()):

            flow_a = self._build_workflow_a()
            flow_b = self._build_workflow_b()
            Runner.resource_mgr.add_workflow(
                WorkflowCard(id="wf_a", name="wf_a", version="1.0"), lambda: flow_a)
            Runner.resource_mgr.add_workflow(
                WorkflowCard(id="wf_b", name="wf_b", version="1.0"), lambda: flow_b)
            agent = _make_agent(flow_a.card, flow_b.card)

            # --- invoke 1: wf_a and wf_b both interrupt; only wf_a's chunk returned first ---
            conversation_id = "TestScenario3TwoWorkflowsEachInterrupt"
            chunks1, _ = await _run_streaming(agent, "请帮我同时收集姓名和地址", conversation_id)
            self.assertEqual(len(chunks1), 1, f"invoke 1: expected 1 interaction chunk, got {chunks1}")
            self.assertEqual(chunks1[0].payload.id, "questioner_a")

            # --- invoke 2: feedback for wf_a; wf_b still pending -> questioner_b chunk ---
            user_input_1 = InteractiveInput()
            user_input_1.update(chunks1[0].payload.id, "李四")
            chunks2, _ = await _run_streaming(agent, user_input_1, conversation_id)
            self.assertEqual(len(chunks2), 1, f"invoke 2: expected 1 interaction chunk, got {chunks2}")
            self.assertEqual(chunks2[0].payload.id, "questioner_b")

            # --- invoke 3: feedback for wf_b; both workflows resume concurrently -> no more interactions ---
            user_input_2 = InteractiveInput()
            user_input_2.update(chunks2[0].payload.id, "北京市朝阳区")
            chunks3, all_chunks3 = await _run_streaming(agent, user_input_2, conversation_id)
            self.assertEqual(len(chunks3), 0, f"invoke 3: expected no interaction chunks, got {chunks3}")
            self.assertGreater(len(all_chunks3), 0, "Expected at least one chunk in final round")
            last_chunk = all_chunks3[-1]
            self.assertIsInstance(last_chunk, OutputSchema)
            self.assertIsNotNone(last_chunk.payload, "Last chunk payload should not be None")

            chat_history = agent.context_engine.get_context(session_id=conversation_id).get_messages()

            # --- checkpoint 1: 10 messages in order ---
            expected_roles = [
                "user", "assistant", "tool",  # invoke 1: query -> tool_calls(wf_a+wf_b) -> placeholder(wf_a)
                "user", "tool",               # invoke 2: feedback wf_a -> placeholder(wf_b), no new ai_message
                "user", "assistant",          # invoke 3: feedback wf_b -> resume ai_message
                "tool", "tool",               # wf_a result + wf_b result
                "assistant",                  # final answer
            ]
            self.assertEqual(len(chat_history), 10,
                             f"Expected 10 messages in chat history, got {len(chat_history)}: {chat_history}")
            for i, (msg, expected_role) in enumerate(zip(chat_history, expected_roles)):
                self.assertEqual(msg.role, expected_role,
                                 f"chat_history[{i}].role expected '{expected_role}', got '{msg.role}'")

            # --- checkpoint 2: index 2 and 4 are both placeholder ToolMessages ---
            for idx in (2, 4):
                placeholder = chat_history[idx]
                self.assertEqual(placeholder.role, "tool",
                                 f"chat_history[{idx}] should be a ToolMessage, got role='{placeholder.role}'")
                self.assertEqual(placeholder.content, "[INTERRUPTED - Waiting for user input]",
                                 f"chat_history[{idx}] content mismatch: '{placeholder.content}'")
