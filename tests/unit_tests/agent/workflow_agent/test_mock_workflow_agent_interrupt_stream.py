# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WorkflowAgent interrupt & resume UT (stream mode)

Covers ST:
  - test_workflow_agent_stream_with_interrupt_recovery
  - test_workflow_agent_runner_stream_with_interrupt_recovery
  - test_workflow_agent_runner_stream_with_dict_interrupt_recovery

Two test cases:
  #7 agent.stream() direct - str interrupt then resume
  #8 Runner.run_agent_streaming() - dict interrupt, re-interrupt,
     then resume

Mock strategy:
  - Questioner uses extract_fields_from_response=False with a
    preset question_content, so NO LLM call is needed to trigger
    the interrupt. Only patch Model.invoke/stream as safety net.
  - patch LongTermMemory.set_scope_config to avoid storage deps

Validates (aligned with ST checkpoints):
  - 1st stream: chunks contain __interaction__
  - 2nd stream (resume): chunks contain workflow_final,
    payload['response'] echoes user answer
"""
import os
import unittest
from unittest.mock import patch, AsyncMock

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
    FieldInfo,
)
from openjiuwen.core.foundation.llm import (
    ModelRequestConfig,
    ModelClientConfig,
)
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.runner import Runner

from tests.unit_tests.fixtures.mock_llm import (
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


class TestWorkflowAgentInterruptStream(
    unittest.IsolatedAsyncioTestCase
):
    """WorkflowAgent stream-mode interrupt & resume tests.

    Corresponds to ST:
      - test_workflow_agent_stream_with_interrupt_recovery
      - test_workflow_agent_runner_stream_with_interrupt_recovery
      - test_workflow_agent_runner_stream_with_dict_interrupt_recovery
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    # ---- helpers ----

    @staticmethod
    def _build_questioner_workflow(
        workflow_id: str = "interrupt_stream_wf",
        workflow_name: str = "interrupt_stream_test",
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
        agent_id: str = "interrupt_stream_agent",
    ) -> WorkflowAgent:
        """Create WorkflowAgent with add_workflows()."""
        config = WorkflowAgentConfig(
            id=agent_id,
            version="1.0",
            description="interrupt stream test agent",
            workflows=[],
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([workflow])
        return agent

    @staticmethod
    async def _collect_stream(stream) -> list:
        """Drain an async generator into a list."""
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        return chunks

    @staticmethod
    def _find_chunks(chunks, chunk_type: str) -> list:
        """Filter OutputSchema chunks by type."""
        result = []
        for c in chunks:
            if isinstance(c, OutputSchema) and c.type == chunk_type:
                result.append(c)
        return result

    # ---- Case #7: agent.stream() interrupt & resume (str) ----

    @pytest.mark.asyncio
    async def test_stream_interrupt_and_resume(self):
        """agent.stream() triggers interrupt, then resumes
        with InteractiveInput.

        ST checkpoint alignment:
          1st stream -> chunks contain __interaction__
          2nd stream -> chunks contain workflow_final,
                        payload['response'] == 'shanghai'
        """
        with mock_llm_context():
            workflow = self._build_questioner_workflow()
            agent = self._create_agent(workflow)

            conv_id = "test_interrupt_stream"

            # 1st stream - should trigger interrupt
            chunks1 = await self._collect_stream(
                agent.stream({
                    "conversation_id": conv_id,
                    "query": "check weather",
                })
            )

            interaction_chunks = self._find_chunks(
                chunks1, "__interaction__",
            )
            self.assertGreater(
                len(interaction_chunks), 0,
                "1st stream should contain __interaction__",
            )

            # 2nd stream
            interactive_input = InteractiveInput()
            interactive_input.update(
                "questioner", "shanghai",
            )

            chunks2 = await self._collect_stream(
                agent.stream({
                    "conversation_id": conv_id,
                    "query": interactive_input,
                })
            )

            final_chunks = self._find_chunks(
                chunks2, "workflow_final",
            )
            self.assertEqual(
                len(final_chunks), 1,
                "2nd stream should have workflow_final",
            )

            payload = final_chunks[0].payload
            self.assertIsInstance(payload, dict)
            self.assertIn("response", payload)
            self.assertEqual(
                payload["response"], "shanghai",
            )

            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 4)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")
            self.assertEqual(chat_history[2].role, "user")
            self.assertEqual(chat_history[3].role, "assistant")

    # ---- Case #8: Runner stream + dict interrupt ----

    @staticmethod
    def _build_field_extract_workflow(
        workflow_id: str = "interrupt_dict_stream_wf",
        workflow_name: str = "interrupt_dict_stream_test",
        version: str = "1.0",
    ) -> Workflow:
        """Build workflow: start -> questioner -> end

        Questioner uses extract_fields_from_response=True
        with FieldInfo(location), requiring LLM to extract
        fields. First call triggers interrupt to collect
        missing fields from user.
        """
        card = WorkflowCard(
            id=workflow_id,
            version=version,
            name=workflow_name,
            description=(
                "Questioner field-extract workflow"
            ),
        )
        flow = Workflow(card=card)

        flow.set_start_comp(
            "start", Start(),
            inputs_schema={"query": "${query}"},
        )

        key_fields = [
            FieldInfo(
                field_name="location",
                description="location",
                required=True,
            ),
        ]
        cfg = QuestionerConfig(
            model_config=_MODEL_REQUEST,
            model_client_config=_MODEL_CLIENT,
            question_content="",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        questioner = QuestionerComponent(cfg)

        flow.add_workflow_comp(
            "questioner",
            questioner,
            inputs_schema={
                "query": "${start.query}",
            },
        )
        flow.set_end_comp(
            "end",
            End({
                "responseTemplate": (
                    "{{user_response}}"
                ),
            }),
            inputs_schema={
                "user_response": (
                    "${questioner.user_response}"
                ),
            },
        )

        flow.add_connection("start", "questioner")
        flow.add_connection("questioner", "end")
        return flow

    @pytest.mark.asyncio
    async def test_stream_dict_interrupt_and_resume(self):
        """Runner.run_agent_streaming() with dict interrupt.

        Uses field-extraction questioner (FieldInfo location,
        extract_fields_from_response=True). Patches
        _invoke_llm_for_extraction to return dict(location=
        "shanghai") simulating LLM extraction output.

        Two-phase flow:
          1st stream -> __interaction__ (questioner asks)
          2nd stream -> InteractiveInput with dict value,
                        should complete with workflow_final

        ST checkpoint alignment:
          - test_workflow_agent_runner_stream_with_dict_interrupt_recovery
          - 1st call: interaction_outputs[0].type == '__interaction__'
          - 2nd call: workflow_final is not None
          - 2nd call: payload is dict
          - 2nd call: 'response' in payload
        """
        _extraction_patch = (
            "openjiuwen.core.workflow.components.llm"
            ".questioner_comp"
            ".QuestionerDirectReplyHandler"
            "._invoke_llm_for_extraction"
        )

        mock_extract = AsyncMock(
            side_effect=[
                dict(),
                dict(location="shanghai"),
            ],
        )

        with mock_llm_context(), patch(
            _extraction_patch,
            mock_extract,
        ):
            @workflow_provider(
                workflow_id="interrupt_dict_stream_wf",
                workflow_name=(
                    "interrupt_dict_stream_test"
                ),
                workflow_version="1.0",
                workflow_description=(
                    "Questioner field-extract workflow"
                ),
            )
            def create_workflow():
                return self._build_field_extract_workflow()

            config = WorkflowAgentConfig(
                id="interrupt_dict_stream_agent",
                version="1.0",
                description=(
                    "interrupt dict stream test agent"
                ),
                workflows=[],
            )
            agent = WorkflowAgent(config)
            agent.add_workflows([create_workflow])

            conv_id = "test_interrupt_dict_stream"

            # Phase 1: trigger interrupt
            chunks1 = await self._collect_stream(
                Runner.run_agent_streaming(
                    agent,
                    {
                        "query": "check weather",
                        "conversation_id": conv_id,
                    },
                )
            )

            interaction_chunks = self._find_chunks(
                chunks1, "__interaction__",
            )
            self.assertGreater(
                len(interaction_chunks), 0,
                "1st stream should contain __interaction__",
            )
            self.assertEqual(
                interaction_chunks[0].type,
                "__interaction__",
            )

            # Phase 2: InteractiveInput with dict value
            # Build from interaction chunk payload.id
            # (same as ST _create_interactive_input_dict)
            interactive_input = InteractiveInput()
            for item in interaction_chunks:
                component_id = item.payload.id
                interactive_input.update(
                    component_id,
                    {"location": "shanghai"},
                )

            chunks2 = await self._collect_stream(
                Runner.run_agent_streaming(
                    agent,
                    {
                        "query": interactive_input,
                        "conversation_id": conv_id,
                    },
                )
            )

            final_chunks = self._find_chunks(
                chunks2, "workflow_final",
            )
            self.assertEqual(
                len(final_chunks), 1,
                "2nd stream should have workflow_final",
            )

            payload = final_chunks[0].payload
            self.assertIsInstance(payload, dict)
            self.assertIn("response", payload)

            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 4)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")
            self.assertEqual(chat_history[2].role, "user")
            self.assertEqual(chat_history[3].role, "assistant")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
