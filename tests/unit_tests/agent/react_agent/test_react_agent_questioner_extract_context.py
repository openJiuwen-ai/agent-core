# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit test: ReActAgent + workflow containing questioner (extract mode) + LLMComponent.

Topology: start -> questioner -> llm_comp -> end

LLM call sequence (all mocked via Model.invoke / Model.stream):
  call 1 - Agent:        tool_call to invoke the workflow
  call 2 - Questioner:   first extraction attempt, name not found -> continue-ask (interrupt)
  call 3 - Questioner:   second extraction after user feedback, {"name": "张三"} -> END_EVENT
                         (this is the path that writes assistant message to context, the fix
                         we are verifying)
  call 4 - LLMComponent: normal text output
  call 5 - Agent:        final summary

Flow:
  invoke 1 -> 1 __interaction__ chunk (questioner asks for name)
  invoke 2 (user provides name) -> 0 __interaction__ chunks, workflow completes, agent summarizes
"""
import os
import unittest
from typing import Tuple
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ModelClientConfig,
    ModelRequestConfig,
    ToolCall,
    UsageMetadata,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.workflow import (
    End,
    FieldInfo,
    LLMCompConfig,
    LLMComponent,
    QuestionerComponent,
    QuestionerConfig,
    Start,
    Workflow,
    WorkflowCard,
)

from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_json_response, create_text_response

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

_MODEL_CLIENT = ModelClientConfig(
    client_provider="OpenAI",
    api_key="sk-fake",
    api_base="https://mock.openai.com/v1",
    verify_ssl=False,
)
_MODEL_REQUEST = ModelRequestConfig(model="gpt-4o-mock", temperature=0.0)

WF_ID = "wf_questioner_extract"


def _build_workflow() -> Workflow:
    """Build: start -> questioner(extract name) -> llm_comp -> end."""
    card = WorkflowCard(id=WF_ID, name=WF_ID, version="1.0")
    flow = Workflow(card=card)

    flow.set_start_comp("start", Start(), inputs_schema={"query": "${query}"})

    questioner_cfg = QuestionerConfig(
        model_client_config=_MODEL_CLIENT,
        model_config=_MODEL_REQUEST,
        extract_fields_from_response=True,
        field_names=[FieldInfo(field_name="name", description="姓名", required=True)],
        with_chat_history=True,
    )
    questioner = QuestionerComponent(questioner_comp_config=questioner_cfg)
    flow.add_workflow_comp(
        "questioner", questioner,
        inputs_schema={"query": "${start.query}"},
    )

    llm_cfg = LLMCompConfig(
        model_client_config=_MODEL_CLIENT,
        model_config=_MODEL_REQUEST,
        template_content=[{"role": "user", "content": "Hello {{name}}"}],
        response_format={"type": "text"},
        output_config={"result": {"type": "string", "required": True}},
        enable_history=True
    )
    llm_comp = LLMComponent(llm_cfg)
    flow.add_workflow_comp(
        "llm", llm_comp,
        inputs_schema={"name": "${questioner.name}"},
    )

    flow.set_end_comp(
        "end", End({"responseTemplate": "result: {{result}}"}),
        inputs_schema={"result": "${llm.result}"},
    )

    flow.add_connection("start", "questioner")
    flow.add_connection("questioner", "llm")
    flow.add_connection("llm", "end")
    return flow


def _make_agent(card: WorkflowCard) -> ReActAgent:
    agent_card = AgentCard(id="react_agent_questioner_extract_test", description="test agent")
    config = ReActAgentConfig(
        model_client_config=_MODEL_CLIENT,
        model_config_obj=_MODEL_REQUEST,
        prompt_template=[{"role": "system", "content": "You are a helpful assistant."}],
    )
    agent = ReActAgent(card=agent_card).configure(config)
    agent.ability_manager.add(card)
    return agent


async def _run_streaming(agent, query, conversation_id) -> Tuple[list, list]:
    """Run agent streaming; return (interaction_chunks, all_chunks)."""
    chunks, all_chunks = [], []
    async for chunk in Runner.run_agent_streaming(
        agent=agent,
        inputs={"conversation_id": conversation_id, "query": query},
    ):
        if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
            chunks.append(chunk)
        all_chunks.append(chunk)
    return chunks, all_chunks


class TestQuestionerExtractWithInterruptWritesAssistantMessage(unittest.IsolatedAsyncioTestCase):
    """Scenario: questioner first asks for name (interrupt), user provides it,
    questioner extracts successfully on second attempt (END_EVENT), then
    LLMComponent runs and agent summarizes.

    The key behavior under test: when questioner reaches END_EVENT after extracting
    all required fields, it must write an assistant message (with extracted params JSON)
    to context. This is the fix added in questioner_comp.py.
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_questioner_extract_interrupt_then_resume(self):
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            # call 1 - Agent: invoke the workflow
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(
                    id="call_wf_001", type="function", name=WF_ID,
                    arguments='{"query": "帮我处理一下"}',
                )],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            # call 2 - Questioner (first attempt): name not found, triggers continue-ask
            create_json_response({}),
            # call 3 - Questioner (second attempt after user feedback): name extracted
            create_json_response({"name": "张三"}),
            # call 4 - LLMComponent: greet the user
            create_text_response("你好，张三！"),
            # call 5 - Agent: final summary
            create_text_response("工作流已完成，用户姓名为张三。"),
        ])

        with patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke), \
             patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()):

            flow = _build_workflow()
            Runner.resource_mgr.add_workflow(
                WorkflowCard(id=WF_ID, name=WF_ID, version="1.0"),
                lambda: flow,
            )
            agent = _make_agent(flow.card)
            conversation_id = "TestQuestionerExtractWithInterruptWritesAssistantMessage"

            # --- invoke 1: questioner can't extract name, asks user ---
            chunks1, _ = await _run_streaming(agent, "帮我处理一下", conversation_id)
            self.assertEqual(len(chunks1), 1, f"Expected 1 interaction chunk, got: {chunks1}")
            self.assertEqual(chunks1[0].payload.id, "questioner")

            # --- invoke 2: user provides name, questioner extracts, workflow completes ---
            user_input = InteractiveInput()
            user_input.update(chunks1[0].payload.id, "我叫张三")
            chunks2, all_chunks2 = await _run_streaming(agent, user_input, conversation_id)
            self.assertEqual(len(chunks2), 0, f"Expected no interaction chunks, got: {chunks2}")
            self.assertGreater(len(all_chunks2), 0, "Expected at least one output chunk")
            last_chunk = all_chunks2[-1]
            self.assertIsInstance(last_chunk, OutputSchema)
            self.assertIn("张三", str(last_chunk.payload), "Final answer should mention 张三")

            chat_history = agent.context_engine.get_context(context_id=WF_ID, session_id=conversation_id).get_messages()

            # --- checkpoint 1: 6 messages in order ---
            expected_roles = ["user", "assistant", "user", "assistant", "user", "assistant"]
            self.assertEqual(
                len(chat_history), 6,
                f"Expected 6 messages, got {len(chat_history)}: {[(m.role, m.content) for m in chat_history]}"
            )
            for i, (msg, expected_role) in enumerate(zip(chat_history, expected_roles)):
                self.assertEqual(
                    msg.role, expected_role,
                    f"chat_history[{i}].role expected '{expected_role}', got '{msg.role}'"
                )

            # --- checkpoint 2: content of index 3, 4, 5 ---
            # index 3: assistant message written by questioner on END_EVENT (extracted params JSON)
            self.assertEqual(
                chat_history[3].content, '{"name": "张三"}',
                f"chat_history[3] content mismatch: '{chat_history[3].content}'"
            )
            # index 4: user message written by LLMComponent (rendered prompt)
            self.assertEqual(
                chat_history[4].content, "Hello 张三",
                f"chat_history[4] content mismatch: '{chat_history[4].content}'"
            )
            # index 5: assistant message written by LLMComponent (LLM output)
            self.assertEqual(
                chat_history[5].content, "你好，张三！",
                f"chat_history[5] content mismatch: '{chat_history[5].content}'"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
