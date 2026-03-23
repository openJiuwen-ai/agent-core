# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WorkflowAgent interrupt & resume UT (invoke mode)

Covers ST:
  - test_workflow_agent_invoke_with_interrupt_recovery
  - test_workflow_agent_runner_invoke_with_interrupt_recovery

Two invocation modes with Questioner-based interrupt:
  1. agent.invoke() direct call - interrupt then resume
  2. Runner.run_agent() - interrupt then resume (string input)

Mock strategy:
  - Questioner uses extract_fields_from_response=False with a
    preset question_content, so NO LLM call is needed to trigger
    the interrupt. Only patch Model.invoke/stream as safety net.
  - patch LongTermMemory.set_scope_config to avoid storage deps

Validates (aligned with ST checkpoints):
  - 1st call: returns list, result[0].type == '__interaction__'
  - 2nd call: result_type == 'answer', state == COMPLETED,
              output.result contains user_response
"""
import os
import unittest
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen.core.single_agent.legacy import (
    WorkflowAgentConfig,
    workflow_provider,
)
from openjiuwen.core.workflow import (
    WorkflowCard,
    Workflow,
    Start,
    End,
    QuestionerComponent,
    QuestionerConfig,
)
from openjiuwen.core.foundation.llm import (
    ModelRequestConfig,
    ModelClientConfig,
)
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.runner import Runner

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    mock_llm_context,
)
from .mock_workflow_agent import MockWorkflowAgent as WorkflowAgent

os.environ.setdefault("LLM_SSL_VERIFY", "false")

# Shared model configs (no real calls needed)
_MODEL_CLIENT = ModelClientConfig(
    client_provider="OpenAI",
    api_key="sk-fake",
    api_base="https://mock.openai.com/v1",
    verify_ssl=False,
)
_MODEL_REQUEST = ModelRequestConfig(
    model="gpt-4o-mock", temperature=0.0,
)


def _make_questioner(question: str) -> QuestionerComponent:
    """Build a questioner with a preset question (no LLM call)."""
    cfg = QuestionerConfig(
        model_client_config=_MODEL_CLIENT,
        model_config=_MODEL_REQUEST,
        question_content=question,
        extract_fields_from_response=False,
        with_chat_history=False,
    )
    return QuestionerComponent(questioner_comp_config=cfg)


class TestWorkflowAgentInterruptInvoke(
    unittest.IsolatedAsyncioTestCase
):
    """WorkflowAgent invoke-mode interrupt & resume tests.

    Corresponds to ST:
      - test_workflow_agent_invoke_with_interrupt_recovery
      - test_workflow_agent_runner_invoke_with_interrupt_recovery
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    # ---- helpers ----

    @staticmethod
    def _build_questioner_workflow(
        workflow_id: str = "interrupt_invoke_wf",
        workflow_name: str = "interrupt_invoke_test",
        version: str = "1.0",
    ) -> Workflow:
        """Build workflow: start -> questioner -> end

        Questioner uses preset question_content with
        extract_fields_from_response=False, so it triggers
        interrupt immediately without any LLM call.
        End collects ${questioner.user_response}.
        """
        card = WorkflowCard(
            id=workflow_id,
            version=version,
            name=workflow_name,
            description="Questioner interrupt workflow",
        )
        flow = Workflow(card=card)

        flow.set_start_comp(
            "start", Start(),
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "questioner",
            _make_questioner("What is your location?"),
            inputs_schema={"query": "${start.query}"},
        )
        flow.set_end_comp(
            "end",
            End({"responseTemplate": "{{user_response}}"}),
            inputs_schema={
                "user_response": (
                    "${questioner.user_response}"
                ),
            },
        )

        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")

        return flow

    @staticmethod
    def _create_agent(
        workflow: Workflow,
        agent_id: str = "interrupt_invoke_agent",
    ) -> WorkflowAgent:
        """Create WorkflowAgent with add_workflows()."""
        config = WorkflowAgentConfig(
            id=agent_id,
            version="1.0",
            description="interrupt invoke test agent",
            workflows=[],
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([workflow])
        return agent

    # ---- Case #5: agent.invoke() interrupt & resume ----

    @pytest.mark.asyncio
    async def test_invoke_interrupt_and_resume(self):
        """agent.invoke() triggers interrupt, then resumes
        with InteractiveInput.

        ST checkpoint alignment:
          1st call -> list, [0].type == '__interaction__'
          2nd call -> dict, result_type='answer',
                      state=COMPLETED
        """
        with mock_llm_context():
            workflow = self._build_questioner_workflow()
            agent = self._create_agent(workflow)

            # 1st invoke - should trigger interrupt
            result1 = await agent.invoke({
                "conversation_id": "test_interrupt_invoke",
                "query": "check weather",
            })

            self.assertIsInstance(result1, list)
            self.assertEqual(
                result1[0].type, "__interaction__",
            )

            # 2nd invoke - resume with InteractiveInput
            interactive_input = InteractiveInput()
            interactive_input.update(
                "questioner", "shanghai",
            )

            result2 = await agent.invoke({
                "conversation_id": "test_interrupt_invoke",
                "query": interactive_input,
            })

            self.assertIsInstance(result2, dict)
            self.assertEqual(
                result2["result_type"], "answer",
            )
            self.assertEqual(
                result2["output"].state.name, "COMPLETED",
            )

            chat_history = agent.context_engine.get_context(session_id="test_interrupt_invoke").get_messages()
            self.assertEqual(len(chat_history), 4)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")
            self.assertEqual(chat_history[2].role, "user")
            self.assertEqual(chat_history[3].role, "assistant")

    # ---- Case #6: Runner.run_agent() interrupt & resume ----

    @pytest.mark.asyncio
    async def test_runner_invoke_interrupt_and_resume(self):
        """Runner.run_agent() triggers interrupt, then resumes
        with string input (ST-aligned: external caller passes
        plain string, WorkflowMessageHandler auto-wraps).

        ST checkpoint alignment:
          1st call -> list, [0].type == '__interaction__'
          2nd call -> dict, result_type='answer',
                      state.value='COMPLETED',
                      result['response'] == 'shanghai'
        """
        with mock_llm_context():
            @workflow_provider(
                workflow_id="interrupt_runner_wf",
                workflow_name="interrupt_runner_test",
                workflow_version="1.0",
                workflow_description=(
                    "Questioner interrupt workflow (runner)"
                ),
            )
            def create_workflow():
                return self._build_questioner_workflow(
                    workflow_id="interrupt_runner_wf",
                    workflow_name="interrupt_runner_test",
                )

            config = WorkflowAgentConfig(
                id="interrupt_runner_agent",
                version="1.0",
                description="interrupt runner test agent",
                workflows=[],
            )
            agent = WorkflowAgent(config)
            agent.add_workflows([create_workflow])

            conv_id = "test_interrupt_runner"

            # 1st call - trigger interrupt
            result1 = await Runner.run_agent(
                agent,
                {
                    "query": "check weather",
                    "conversation_id": conv_id,
                },
            )

            self.assertIsInstance(result1, list)
            self.assertEqual(
                result1[0].type, "__interaction__",
            )

            # 2nd call - resume with plain string
            # (ST pattern: external caller passes string,
            #  not InteractiveInput)
            result2 = await Runner.run_agent(
                agent,
                {
                    "query": "shanghai",
                    "conversation_id": conv_id,
                },
            )

            self.assertIsInstance(result2, dict)
            self.assertEqual(
                result2["result_type"], "answer",
            )
            self.assertEqual(
                result2["output"].state.value,
                "COMPLETED",
            )
            # ST validates: result['response']
            self.assertEqual(
                result2["output"].result["response"],
                "shanghai",
            )

            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 4)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")
            self.assertEqual(chat_history[2].role, "user")
            self.assertEqual(chat_history[3].role, "assistant")



if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
