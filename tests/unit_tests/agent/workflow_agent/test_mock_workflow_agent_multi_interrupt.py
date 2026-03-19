# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WorkflowAgent multi-node interrupt UT

Covers ST:
  - test_workflow_agent_with_multiple_interrupt_nodes_stream
  - test_workflow_agent_with_multiple_interrupt_nodes_resume_all_at_once

Workflow topology (parallel branches):
    start -> questioner  \
          -> interactive  -> end

Two interrupt components run in parallel:
  - QuestionerComponent (extract_fields_from_response=False,
    preset question_content) triggers str-type interrupt
  - InteractiveConfirmComponent (session.interact()) triggers
    dict-type interrupt

Case #9: stream mode, sequential resume (3 steps)
  step1: 1st stream -> returns first interrupt
  step2: resume first -> returns second interrupt
  step3: resume second -> workflow_final

Case #10: invoke mode, resume all at once (2 steps)
  step1: 1st invoke -> returns first interrupt (list)
  step2: InteractiveInput with both nodes -> completed

Mock strategy:
  - mock_llm_context() patches Model.invoke/stream +
    LongTermMemory as safety net
  - Neither component needs real LLM calls
"""
import os
import unittest

import pytest

from openjiuwen.core.single_agent.legacy import (
    WorkflowAgentConfig,
)
from openjiuwen.core.workflow import (
    WorkflowCard,
    Workflow,
    WorkflowComponent,
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


def _make_questioner(
    question: str,
) -> QuestionerComponent:
    """Build a questioner with preset question (no LLM)."""
    cfg = QuestionerConfig(
        model_client_config=_MODEL_CLIENT,
        model_config=_MODEL_REQUEST,
        question_content=question,
        extract_fields_from_response=False,
        with_chat_history=False,
    )
    return QuestionerComponent(questioner_comp_config=cfg)


class InteractiveConfirmComponent(WorkflowComponent):
    """Custom interrupt component using session.interact().

    Triggers a dict-type interrupt. On resume, returns
    the user's confirmation string as confirm_result.
    """

    def __init__(self, comp_id: str):
        super().__init__()
        self.comp_id = comp_id

    async def invoke(self, inputs, session, context):
        confirm = await session.interact(
            "Please confirm the operation"
        )
        return {"confirm_result": confirm}


class TestWorkflowAgentMultiInterrupt(
    unittest.IsolatedAsyncioTestCase
):
    """WorkflowAgent multi-node parallel interrupt tests.

    Corresponds to ST:
      - test_workflow_agent_with_multiple_interrupt_nodes_stream
      - test_workflow_agent_with_multiple_interrupt_nodes_resume_all_at_once
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    # ---- helpers ----

    @staticmethod
    def _build_multi_interrupt_workflow(
        workflow_id: str = "multi_interrupt_wf",
        workflow_name: str = "multi_interrupt_test",
        version: str = "1.0",
    ) -> Workflow:
        """Build workflow: start -> [questioner, interactive] -> end

        Two parallel interrupt branches:
          - questioner: preset question, str-type interrupt
          - interactive: session.interact(), dict-type interrupt
        End collects outputs from both branches.
        """
        card = WorkflowCard(
            id=workflow_id,
            version=version,
            name=workflow_name,
            description="Multi-node interrupt workflow",
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
        flow.add_workflow_comp(
            "interactive",
            InteractiveConfirmComponent("interactive"),
            inputs_schema={"query": "${start.query}"},
        )
        flow.set_end_comp(
            "end",
            End({
                "responseTemplate": (
                    "{{user_response}}"
                    " | confirm={{confirm_result}}"
                ),
            }),
            inputs_schema={
                "user_response": (
                    "${questioner.user_response}"
                ),
                "confirm_result": (
                    "${interactive.confirm_result}"
                ),
            },
        )

        # Parallel: start fans out to both
        flow.add_connection("start", "questioner")
        flow.add_connection("start", "interactive")
        # Both must complete before end
        flow.add_connection(
            ["questioner", "interactive"], "end",
        )

        return flow

    @staticmethod
    def _create_agent(
        workflow: Workflow,
        agent_id: str = "multi_interrupt_agent",
    ) -> WorkflowAgent:
        """Create WorkflowAgent with add_workflows()."""
        config = WorkflowAgentConfig(
            id=agent_id,
            version="1.0",
            description="multi interrupt test agent",
            workflows=[],
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([workflow])
        return agent

    # ---- Case #9: stream sequential resume ----

    @pytest.mark.asyncio
    async def test_multi_interrupt_sequential_resume(
        self,
    ):
        """Stream mode: two parallel interrupts, resume
        one at a time (3 steps).

        ST checkpoint alignment:
          step1: stream returns 1 interaction chunk
                 (first interrupt only, by design)
          step2: resume first -> returns second interrupt
          step3: resume second -> workflow_final chunk,
                 payload contains both nodes' outputs
        """
        with mock_llm_context():
            workflow = self._build_multi_interrupt_workflow()
            agent = self._create_agent(workflow)

            conv_id = "test_multi_interrupt_seq"

            # == Step 1: trigger parallel interrupts ==
            interaction_chunks = []
            async for chunk in agent.stream({
                "query": "check weather",
                "conversation_id": conv_id,
            }):
                if (
                    hasattr(chunk, "type")
                    and chunk.type == "__interaction__"
                ):
                    interaction_chunks.append(chunk)

            # Only first interrupt returned (by design)
            self.assertEqual(len(interaction_chunks), 1)
            first_id = interaction_chunks[0].payload.id

            # == Step 2: resume first interrupt ==
            interactive_input = InteractiveInput()
            if first_id == "interactive":
                interactive_input.update(
                    "interactive", "confirmed",
                )
                expected_second = "questioner"
            else:
                interactive_input.update(
                    "questioner", "shanghai",
                )
                expected_second = "interactive"

            interaction_chunks = []
            async for chunk in agent.stream({
                "query": interactive_input,
                "conversation_id": conv_id,
            }):
                if (
                    hasattr(chunk, "type")
                    and chunk.type == "__interaction__"
                ):
                    interaction_chunks.append(chunk)

            # Second interrupt returned
            self.assertEqual(len(interaction_chunks), 1)
            self.assertEqual(
                interaction_chunks[0].payload.id,
                expected_second,
            )

            # == Step 3: resume second interrupt ==
            interactive_input = InteractiveInput()
            if expected_second == "interactive":
                interactive_input.update(
                    "interactive", "confirmed",
                )
            else:
                interactive_input.update(
                    "questioner", "shanghai",
                )

            final_chunk = None
            interaction_chunks = []
            async for chunk in agent.stream({
                "query": interactive_input,
                "conversation_id": conv_id,
            }):
                if (
                    hasattr(chunk, "type")
                    and chunk.type == "__interaction__"
                ):
                    interaction_chunks.append(chunk)
                elif (
                    hasattr(chunk, "type")
                    and chunk.type == "workflow_final"
                ):
                    final_chunk = chunk

            # No more interrupts
            self.assertEqual(len(interaction_chunks), 0)
            # workflow_final received
            self.assertIsNotNone(final_chunk)
            self.assertIsInstance(
                final_chunk.payload, dict,
            )
            self.assertIn(
                "response", final_chunk.payload,
            )

            # check get chat history from context
            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 6)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")
            self.assertEqual(chat_history[2].role, "user")
            self.assertEqual(chat_history[3].role, "assistant")
            self.assertEqual(chat_history[4].role, "user")
            self.assertEqual(chat_history[5].role, "assistant")

    # ---- Case #10: invoke resume all at once ----

    @pytest.mark.asyncio
    async def test_multi_interrupt_resume_all_at_once(
        self,
    ):
        """Invoke mode: two parallel interrupts, resume
        all at once in a single InteractiveInput (2 steps).

        ST checkpoint alignment:
          step1: invoke returns list,
                 [0].type == '__interaction__'
          step2: InteractiveInput with both node inputs,
                 result_type='answer', state=COMPLETED,
                 output.result contains both outputs
        """
        with mock_llm_context():
            workflow = self._build_multi_interrupt_workflow()
            agent = self._create_agent(workflow)

            conv_id = "test_multi_interrupt_all"

            # == Step 1: trigger parallel interrupts ==
            result1 = await agent.invoke({
                "query": "check weather",
                "conversation_id": conv_id,
            })

            self.assertIsInstance(result1, list)
            self.assertEqual(
                result1[0].type, "__interaction__",
            )

            # == Step 2: resume both at once ==
            interactive_input = InteractiveInput()
            interactive_input.update(
                "interactive", "confirmed",
            )
            interactive_input.update(
                "questioner", "shanghai",
            )

            result2 = await agent.invoke({
                "query": interactive_input,
                "conversation_id": conv_id,
            })

            self.assertIsInstance(result2, dict)
            self.assertEqual(
                result2["result_type"], "answer",
            )
            self.assertEqual(
                result2["output"].state.value,
                "COMPLETED",
            )
            # Final result contains both outputs
            response = result2["output"].result
            self.assertIn("response", response)

            # check get chat history from context
            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 4)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")
            self.assertEqual(chat_history[2].role, "user")
            self.assertEqual(chat_history[3].role, "assistant")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
