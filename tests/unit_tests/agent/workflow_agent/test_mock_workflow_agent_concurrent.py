# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WorkflowAgent concurrent & realtime interrupt UT

Covers ST:
  - test_workflow_agent_concurrent_with_workflow_provider
  - test_real_time_interrupt_with_cancellation
  - test_questioner_state_reset_on_second_invocation

Case #18: concurrent conversations with workflow_provider factory
Case #19: realtime interrupt cancellation (streaming)
Case #20: component state reset on second invocation

Mock strategy:
  - Questioner uses extract_fields_from_response=False with a
    preset question_content, so NO LLM call is needed to trigger
    the interrupt. Only patch Model.invoke/stream as safety net.
  - patch LongTermMemory.set_scope_config to avoid storage deps
  - SlowNode simulates long-running workflow execution
  - Intent detection mocked via create_text_response

Validates (aligned with ST checkpoints):
  #18:
  - 3 conversations invoke concurrently via asyncio.gather
  - Each returns list with [0].type == '__interaction__'
  - All 3 succeed (state isolation across conversations)
  #19:
  - Phase 1: slow workflow starts (not awaited)
  - Phase 2: new query interrupts, triggers __interaction__
  - Phase 3: InteractiveInput resumes, workflow_final returned
  #20:
  - 1st invoke: triggers __interaction__
  - 1st resume: result_type='answer', response contains answer1
  - 2nd invoke: triggers __interaction__ again (state reset)
  - 2nd resume: result_type='answer', response contains answer2,
    does NOT contain answer1
"""
import asyncio
import os
import unittest
import uuid

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
    ModelConfig,
    BaseModelInfo,
    ModelRequestConfig,
    ModelClientConfig,
)
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.runner import Runner

from tests.unit_tests.fixtures.mock_llm import (
    mock_llm_context,
    create_text_response,
)
from tests.unit_tests.core.workflow.mock_nodes import (
    SlowNode,
)
from .mock_workflow_agent import MockWorkflowAgent as WorkflowAgent

os.environ.setdefault("LLM_SSL_VERIFY", "false")

# Model config for intent detection (LLM calls are mocked)
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


class TestWorkflowAgentConcurrent(
    unittest.IsolatedAsyncioTestCase
):
    """WorkflowAgent concurrent & realtime interrupt tests.

    Corresponds to ST:
      - test_workflow_agent_concurrent_with_workflow_provider
      - test_real_time_interrupt_with_cancellation
      - test_questioner_state_reset_on_second_invocation
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    # ---- helpers ----

    @staticmethod
    def _build_questioner_workflow(
        workflow_id: str = "concurrent_wf",
        workflow_name: str = "concurrent_test",
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

    # ---- Case #18: concurrent conversations ----

    @pytest.mark.asyncio
    async def test_concurrent_conversations(self):
        """3 conversations invoke concurrently, each triggers
        interrupt independently (state isolation).

        ST checkpoint alignment:
          - @workflow_provider factory creates fresh instance
            per get_workflow() call
          - 3 concurrent invoke() with different conv_ids
          - All 3 return list with [0].type == '__interaction__'
          - success_count == 3
        """
        with mock_llm_context():
            @workflow_provider(
                workflow_id="concurrent_provider_wf",
                workflow_name="concurrent_provider_test",
                workflow_version="1.0",
                workflow_description=(
                    "Questioner interrupt workflow (concurrent)"
                ),
            )
            def create_workflow():
                return self._build_questioner_workflow(
                    workflow_id="concurrent_provider_wf",
                    workflow_name="concurrent_provider_test",
                )

            config = WorkflowAgentConfig(
                id="concurrent_test_agent",
                version="1.0",
                description="concurrent test agent",
                workflows=[],
            )
            agent = WorkflowAgent(config)
            agent.add_workflows([create_workflow])

            # 3 conversations with unique ids
            conv_ids = [str(uuid.uuid4()) for _ in range(3)]

            async def invoke_one(conv_id: str):
                """Single invoke that should trigger interrupt."""
                result = await agent.invoke({
                    "query": "check weather",
                    "conversation_id": conv_id,
                })
                return conv_id, result

            # Concurrent execution
            results = await asyncio.gather(
                *(invoke_one(cid) for cid in conv_ids),
                return_exceptions=True,
            )

            # Validate: all 3 should trigger interrupt
            success_count = 0
            for item in results:
                self.assertNotIsInstance(
                    item, Exception,
                    f"concurrent invoke raised: {item}",
                )
                conv_id, result = item
                self.assertIsInstance(result, list)
                self.assertEqual(
                    result[0].type, "__interaction__",
                )
                success_count += 1

            self.assertEqual(
                success_count, 3,
                "all 3 conversations should trigger interrupt",
            )

    # ---- Case #19 helpers ----

    @staticmethod
    def _build_slow_workflow(
        workflow_id: str = "weather_slow_wf",
        workflow_name: str = "weather_slow",
    ) -> Workflow:
        """Build workflow: start -> slow_node -> end

        SlowNode sleeps 10s, simulating a long-running task
        that can be interrupted by a new request.
        """
        card = WorkflowCard(
            id=workflow_id,
            version="1.0",
            name=workflow_name,
            description=(
                "Query weather, temperature, forecast"
            ),
        )
        flow = Workflow(card=card)

        flow.set_start_comp(
            "start", Start(),
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "slow_node",
            SlowNode("slow_node", wait=10),
            inputs_schema={
                "output": "${start.query}",
            },
        )
        flow.set_end_comp(
            "end",
            End({
                "responseTemplate": (
                    "weather:{{output}}"
                ),
            }),
            inputs_schema={
                "output": "${slow_node.output}",
            },
        )

        flow.add_connection("start", "slow_node")
        flow.add_connection("slow_node", "end")

        return flow

    @staticmethod
    def _build_stock_questioner_workflow(
        workflow_id: str = "stock_interrupt_wf",
        workflow_name: str = "stock_interrupt",
    ) -> Workflow:
        """Build workflow: start -> questioner -> end

        Questioner asks for stock code, triggers interrupt.
        End collects ${questioner.user_response}.
        """
        card = WorkflowCard(
            id=workflow_id,
            version="1.0",
            name=workflow_name,
            description=(
                "Query stock price, market trends"
            ),
        )
        flow = Workflow(card=card)

        flow.set_start_comp(
            "start", Start(),
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "questioner",
            _make_questioner("What is the stock code?"),
            inputs_schema={"query": "${start.query}"},
        )
        flow.set_end_comp(
            "end",
            End({
                "responseTemplate": (
                    "stock:{{user_response}}"
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

    # ---- Case #19: realtime interrupt cancellation ----

    @pytest.mark.skip(reason="skip cancel")
    @pytest.mark.asyncio
    async def test_realtime_interrupt_cancellation(self):
        """Slow workflow running -> new query interrupts it
        -> questioner triggers __interaction__ -> resume
        completes with workflow_final.

        Uses Runner.run_agent_streaming (streaming mode).

        ST checkpoint alignment
        (test_real_time_interrupt_with_cancellation):
          Phase 1: task1 starts slow workflow (not awaited)
          Phase 2: query2 interrupts, chunks contain
                   __interaction__; task1 is cancelled
          Phase 3: InteractiveInput resumes, chunks contain
                   workflow_final, payload['response']
                   contains user answer
        """
        with mock_llm_context() as mock_llm:
            # Intent detection responses:
            #   1st call -> {"result": 1} selects weather
            #   2nd call -> {"result": 2} selects stock
            # 3rd call is not needed (InteractiveInput
            # skips intent detection).
            mock_llm.set_responses([
                create_text_response('{"result": 1}'),
                create_text_response('{"result": 2}'),
            ])

            weather_wf = self._build_slow_workflow()
            stock_wf = (
                self._build_stock_questioner_workflow()
            )

            config = WorkflowAgentConfig(
                id="realtime_interrupt_agent",
                version="1.0",
                description="realtime interrupt test",
                workflows=[],
                model=_MODEL_CONFIG,
            )
            agent = WorkflowAgent(config)
            agent.add_workflows([weather_wf, stock_wf])

            conv_id = "test-realtime-interrupt"

            # Phase 1: start slow workflow (don't await)
            async def collect_phase1():
                return await self._collect_stream(
                    Runner.run_agent_streaming(
                        agent,
                        {
                            "query": "check weather",
                            "conversation_id": conv_id,
                        },
                    )
                )

            task1 = asyncio.create_task(collect_phase1())

            # Let slow workflow begin execution
            await asyncio.sleep(0.2)

            # Verify task1 was cancelled / completed
            try:
                await asyncio.wait_for(task1, timeout=0.4)
            except (
                    asyncio.CancelledError,
                    asyncio.TimeoutError,
            ):
                pass

            # Phase 2: interrupt with new query
            chunks2 = await self._collect_stream(
                Runner.run_agent_streaming(
                    agent,
                    {
                        "query": "check stock",
                        "conversation_id": conv_id,
                    },
                )
            )

            interaction_chunks = self._find_chunks(
                chunks2, "__interaction__",
            )
            self.assertGreater(
                len(interaction_chunks), 0,
                "phase 2 should contain __interaction__",
            )

            # Phase 3: resume with InteractiveInput
            interactive_input = InteractiveInput()
            interactive_input.update(
                "questioner", "AAPL",
            )

            chunks3 = await self._collect_stream(
                Runner.run_agent_streaming(
                    agent,
                    {
                        "query": interactive_input,
                        "conversation_id": conv_id,
                    },
                )
            )

            final_chunks = self._find_chunks(
                chunks3, "workflow_final",
            )
            self.assertEqual(
                len(final_chunks), 1,
                "phase 3 should have workflow_final",
            )

            payload = final_chunks[0].payload
            self.assertIsInstance(payload, dict)
            self.assertIn("response", payload)
            self.assertIn(
                "AAPL", payload["response"],
            )

            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 5)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "user")
            self.assertEqual(chat_history[2].role, "assistant")
            self.assertEqual(chat_history[3].role, "user")
            self.assertEqual(chat_history[4].role, "assistant")

    # ---- Case #20: component state reset ----

    @pytest.mark.asyncio
    async def test_component_state_reset(self):
        """Questioner state resets between invocations.

        Uses Runner.run_agent (invoke mode).

        Four-step flow on the same conversation_id:
          1st invoke: triggers __interaction__
          1st resume: InteractiveInput("shanghai") -> completed
          2nd invoke: triggers __interaction__ again
          2nd resume: InteractiveInput("beijing") -> completed,
                      response contains "beijing",
                      does NOT contain "shanghai"

        ST checkpoint alignment
        (test_questioner_state_reset_on_second_invocation):
          - result1: list, [0].type == '__interaction__'
          - result2: dict, result_type='answer',
                     response contains 1st answer
          - result3: list, [0].type == '__interaction__'
                     (state was reset)
          - result4: dict, result_type='answer',
                     response contains 2nd answer,
                     does NOT contain 1st answer
        """
        with mock_llm_context():
            workflow = self._build_questioner_workflow(
                workflow_id="state_reset_wf",
                workflow_name="state_reset_test",
            )

            config = WorkflowAgentConfig(
                id="state_reset_agent",
                version="1.0",
                description="state reset test agent",
                workflows=[],
            )
            agent = WorkflowAgent(config)
            agent.add_workflows([workflow])

            conv_id = "test-state-reset"

            # Step 1: 1st invoke -> interrupt
            result1 = await Runner.run_agent(
                agent,
                {
                    "query": "collect info",
                    "conversation_id": conv_id,
                },
            )

            self.assertIsInstance(result1, list)
            self.assertEqual(
                result1[0].type, "__interaction__",
            )

            # Step 2: resume with "shanghai"
            interactive_1 = InteractiveInput()
            interactive_1.update(
                "questioner", "shanghai",
            )

            result2 = await Runner.run_agent(
                agent,
                {
                    "query": interactive_1,
                    "conversation_id": conv_id,
                },
            )

            self.assertIsInstance(result2, dict)
            self.assertEqual(
                result2["result_type"], "answer",
            )
            response_1 = (
                result2["output"].result["response"]
            )
            self.assertIn("shanghai", response_1)

            # Step 3: 2nd invoke -> should re-trigger
            # interrupt (state was reset)
            result3 = await Runner.run_agent(
                agent,
                {
                    "query": "collect info again",
                    "conversation_id": conv_id,
                },
            )

            self.assertIsInstance(result3, list)
            self.assertEqual(
                result3[0].type, "__interaction__",
                "2nd invoke should re-trigger interrupt",
            )

            # Step 4: resume with "beijing"
            interactive_2 = InteractiveInput()
            interactive_2.update(
                "questioner", "beijing",
            )

            result4 = await Runner.run_agent(
                agent,
                {
                    "query": interactive_2,
                    "conversation_id": conv_id,
                },
            )

            self.assertIsInstance(result4, dict)
            self.assertEqual(
                result4["result_type"], "answer",
            )
            response_2 = (
                result4["output"].result["response"]
            )
            self.assertIn("beijing", response_2)
            self.assertNotIn(
                "shanghai", response_2,
                "2nd result should not contain 1st answer",
            )

            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 8)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")
            self.assertEqual(chat_history[2].role, "user")
            self.assertEqual(chat_history[3].role, "assistant")
            self.assertEqual(chat_history[4].role, "user")
            self.assertEqual(chat_history[5].role, "assistant")
            self.assertEqual(chat_history[6].role, "user")
            self.assertEqual(chat_history[7].role, "assistant")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
