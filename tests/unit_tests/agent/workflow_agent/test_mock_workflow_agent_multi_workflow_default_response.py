# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WorkflowAgent multi-workflow default response UT

Covers ST:
  - test_default_response_when_no_task_detected
  - test_fallback_to_first_workflow_when_no_default_response
  - test_default_response_stream_returns_workflow_final

Workflow topology (all cases):
  weather_flow: start -> end  (prefix="weather:")
  stock_flow:   start -> end  (prefix="stock:")

Case #15: invoke + agent direct + DefaultResponse configured
          LLM intent detection returns None
          -> returns default_response.text
Case #16: invoke + Runner + no DefaultResponse
          LLM intent detection returns None
          -> falls back to workflows[0]
Case #17: stream + Runner + DefaultResponse configured
          LLM intent detection returns None
          -> stream yields workflow_final with default text

Mock strategy:
  - patch WorkflowController._detect_workflow_via_llm
    as AsyncMock returning None (simulates no intent match)
  - mock_llm_context() as safety net to prevent real LLM calls
"""
import os
import uuid
import unittest
from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen.core.single_agent.legacy import (
    WorkflowAgentConfig,
    DefaultResponse,
)
from openjiuwen.core.workflow import (
    WorkflowCard,
    Workflow,
    Start,
    End,
)
from openjiuwen.core.foundation.llm import (
    ModelConfig,
    BaseModelInfo,
)
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.runner import Runner

from tests.unit_tests.fixtures.mock_llm import (
    mock_llm_context, create_text_response,
)
from .mock_workflow_agent import MockWorkflowAgent as WorkflowAgent

os.environ.setdefault("LLM_SSL_VERIFY", "false")

# Model config for intent detection (all LLM calls are mocked)
_MODEL_CONFIG = ModelConfig(
    model_provider="OpenAI",
    model_info=BaseModelInfo(
        model="gpt-4o-mock",
        api_base="https://mock.openai.com/v1",
        api_key="sk-fake",
        temperature=0.7,
        top_p=0.9,
        timeout=120,
    ),
)

# Patch target for WorkflowController._detect_workflow_via_llm
_DETECT_PATCH_TARGET = (
    "openjiuwen.core.application.workflow_agent"
    ".workflow_controller"
    ".WorkflowController._detect_workflow_via_llm"
)


class TestMultiWorkflowDefaultResponse(
    unittest.IsolatedAsyncioTestCase,
):
    """Multi-workflow default response tests.

    Corresponds to ST:
      - test_default_response_when_no_task_detected
      - test_fallback_to_first_workflow_when_no_default_response
      - test_default_response_stream_returns_workflow_final
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    # ---- helpers ----

    @staticmethod
    def _build_prefixed_workflow(
        workflow_id: str,
        workflow_name: str,
        prefix: str,
    ) -> Workflow:
        """Build simple workflow: start -> end

        End uses responseTemplate with prefix so we can
        verify which workflow was selected.
        """
        card = WorkflowCard(
            name=workflow_name,
            id=workflow_id,
            version="1.0",
        )
        flow = Workflow(card=card)

        flow.set_start_comp(
            "start",
            Start(),
            inputs_schema={"query": "${query}"},
        )
        flow.set_end_comp(
            "end",
            End({
                "responseTemplate": (
                    f"{prefix}{{{{output}}}}"
                ),
            }),
            inputs_schema={
                "output": "${start.query}",
            },
        )
        flow.add_connection("start", "end")
        return flow

    @staticmethod
    def _build_two_workflows():
        """Build weather + stock workflows with descriptions.

        Returns:
            Tuple of (weather_wf, stock_wf).
        """
        weather_wf = (
            TestMultiWorkflowDefaultResponse
            ._build_prefixed_workflow(
                workflow_id="weather_flow",
                workflow_name="weather_query",
                prefix="weather:",
            )
        )
        stock_wf = (
            TestMultiWorkflowDefaultResponse
            ._build_prefixed_workflow(
                workflow_id="stock_flow",
                workflow_name="stock_query",
                prefix="stock:",
            )
        )

        weather_wf.card.description = (
            "Query weather, temperature, forecast"
        )
        stock_wf.card.description = (
            "Query stock price, market trends"
        )
        return weather_wf, stock_wf

    # ---- Case #15 ----

    @pytest.mark.asyncio
    @patch(
        _DETECT_PATCH_TARGET,
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_default_response_with_config(
        self,
        mock_detect: AsyncMock,
    ):
        """Invoke + agent direct + multi-workflow +
        DefaultResponse configured.

        When LLM intent detection returns None and
        default_response is configured, agent returns
        the configured default_response.text instead of
        falling back to the first workflow.

        ST alignment:
          test_default_response_when_no_task_detected

        Checkpoints:
          - result is dict
          - result["status"] == "default_response"
          - result["result_type"] == "answer"
          - result["output"]["answer"] == default_text
          - _detect_workflow_via_llm was called exactly once
        """
        with mock_llm_context():
            weather_wf, stock_wf = (
                self._build_two_workflows()
            )

            default_text = (
                "Sorry, I cannot understand your question"
            )
            config = WorkflowAgentConfig(
                id="test_default_resp_agent",
                version="1.0",
                description="default response test",
                workflows=[],
                model=_MODEL_CONFIG,
                default_response=DefaultResponse(
                    type="text",
                    text=default_text,
                ),
            )
            agent = WorkflowAgent(config)
            agent.add_workflows(
                [weather_wf, stock_wf],
            )

            conv_id = str(uuid.uuid4())
            result = await agent.invoke({
                "query": "blahblah random xyz",
                "conversation_id": conv_id,
            })

            # result is dict
            self.assertIsInstance(result, dict)

            # status indicates default_response path
            self.assertEqual(
                result["status"],
                "default_response",
            )

            # result_type is answer
            self.assertEqual(
                result["result_type"],
                "answer",
            )

            # output contains the configured default text
            self.assertEqual(
                result["output"]["answer"],
                default_text,
            )

            # check get chat history from context
            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 2)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")

    # ---- Case #16 ----

    @pytest.mark.asyncio
    @patch(
        _DETECT_PATCH_TARGET,
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_fallback_to_first_workflow(
        self,
        mock_detect: AsyncMock,
    ):
        """Invoke + Runner.run_agent + multi-workflow +
        no DefaultResponse configured.

        When LLM intent detection returns None and
        default_response is NOT configured (text is None),
        agent falls back to executing workflows[0]
        for backward compatibility.

        ST alignment:
          test_fallback_to_first_workflow_when_no_default_response

        Checkpoints:
          - result is dict
          - result["result_type"] == "answer"
          - result["output"].result["response"] contains
            "weather:" prefix (proving workflows[0] ran)
        """
        with mock_llm_context():
            weather_wf, stock_wf = (
                self._build_two_workflows()
            )

            # No default_response configured
            # (DefaultResponse default has text=None)
            config = WorkflowAgentConfig(
                id="test_no_default_resp_agent",
                version="1.0",
                description=(
                    "fallback to first workflow test"
                ),
                workflows=[],
                model=_MODEL_CONFIG,
                # default_response uses factory default
            )
            agent = WorkflowAgent(config)
            agent.add_workflows(
                [weather_wf, stock_wf],
            )

            conv_id = str(uuid.uuid4())
            result = await Runner.run_agent(
                agent,
                {
                    "query": "blahblah random xyz",
                    "conversation_id": conv_id,
                },
            )

            # result is dict
            self.assertIsInstance(result, dict)

            # result_type is answer
            self.assertEqual(
                result["result_type"],
                "answer",
            )

            # first workflow (weather) was executed
            response_content = (
                result["output"].result["response"]
            )
            self.assertIn(
                "weather:", response_content,
            )

    # ---- Case #17 ----

    @pytest.mark.asyncio
    async def test_default_response_stream(self):
        """Stream + Runner.run_agent_streaming +
        multi-workflow + DefaultResponse configured.

        Mock LLM to return {"result": 0} (invalid
        1-indexed category), triggering the default
        response path. Stream should yield a
        workflow_final chunk with the configured
        default_response text.

        ST alignment:
          test_default_response_stream_returns_workflow_final

        Checkpoints:
          - stream yields at least one chunk
          - exactly one workflow_final OutputSchema chunk
          - workflow_final.payload is dict
          - workflow_final.payload contains "response"
          - workflow_final.payload["response"] == default_text
        """
        with mock_llm_context() as mock_llm:
            # {"result": 0} is invalid (1-indexed),
            # so intent detection finds no match
            mock_llm.set_responses([
                create_text_response(
                    '{"result": 0}',
                ),
            ])

            weather_wf, stock_wf = (
                self._build_two_workflows()
            )

            default_text = (
                "Sorry, I cannot understand your question"
            )
            config = WorkflowAgentConfig(
                id="test_default_resp_stream_agent",
                version="1.0",
                description=(
                    "default response stream test"
                ),
                workflows=[],
                model=_MODEL_CONFIG,
                default_response=DefaultResponse(
                    type="text",
                    text=default_text,
                ),
            )
            agent = WorkflowAgent(config)
            agent.add_workflows(
                [weather_wf, stock_wf],
            )

            chunks = []
            conv_id = str(uuid.uuid4())
            async for chunk in (
                Runner.run_agent_streaming(
                    agent,
                    {
                        "query": "blahblah random xyz",
                        "conversation_id": conv_id,
                    },
                )
            ):
                chunks.append(chunk)

            # stream yields at least one chunk
            self.assertGreater(len(chunks), 0)

            # filter workflow_final chunks
            wf_final_chunks = []
            for c in chunks:
                if isinstance(c, OutputSchema) and c.type == "workflow_final":
                    wf_final_chunks.append(c)

            # exactly one workflow_final chunk
            self.assertEqual(
                len(wf_final_chunks), 1,
            )

            payload = wf_final_chunks[0].payload

            # payload is dict
            self.assertIsInstance(payload, dict)

            # payload contains "response" key
            self.assertIn("response", payload)

            # response equals configured default text
            self.assertEqual(
                payload["response"],
                default_text,
            )

            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 2)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
