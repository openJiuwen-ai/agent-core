# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WorkflowAgent multi-workflow UT

Covers ST:
  - test_multi_workflow_routing_via_intent_detection
  - test_multi_workflow_jump_and_recovery
  - test_interactive_input_skips_llm_intent_detection

Workflow topologies:
  Case #11 (routing):
    weather_flow: start -> end  (prefix="weather:")
    stock_flow:   start -> end  (prefix="stock:")

  Case #12 (jump & recovery):
    weather_flow: start -> questioner -> end
    stock_flow:   start -> questioner -> end
    Both use preset question (no field extraction LLM).

  Case #13 (InteractiveInput fast path):
    weather_flow: start -> questioner -> end
    stock_flow:   start -> questioner -> end
    Same topology as #12.

  Case #14 (InteractiveInput targets correct workflow):
    weather_flow: start -> questioner -> end
    stock_flow:   start -> questioner -> end
    Same topology as #12.

Case #11: invoke, intent detection routes to stock_flow
Case #12: stream, A interrupts -> jump to B -> resume A -> resume B
Case #13: stream, interrupt -> InteractiveInput resumes (no LLM)
Case #14: invoke, both interrupt -> InteractiveInput resumes each

Mock strategy:
  - mock_llm_context() patches Model.invoke/stream +
    LongTermMemory as safety net
  - Intent detection LLM returns {"result": N} to
    select the target workflow (1-indexed)
  - Questioner uses preset question_content with
    extract_fields_from_response=False (no LLM needed)
  - InteractiveInput bypasses intent detection entirely
  - Case #14 needs only 2 LLM mocks (step1+step2),
    step3+step4 use InteractiveInput (no LLM)
"""
import os
import uuid
import unittest

import pytest

from openjiuwen.core.single_agent.legacy import (
    WorkflowAgentConfig,
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
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    mock_llm_context,
    create_text_response,
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

# Shared model configs for questioner (no real calls needed)
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


class TestWorkflowAgentMultiWorkflow(
    unittest.IsolatedAsyncioTestCase
):
    """WorkflowAgent multi-workflow intent routing tests.

    Corresponds to ST:
      - test_multi_workflow_routing_via_intent_detection
      - test_multi_workflow_jump_and_recovery
      - test_interactive_input_skips_llm_intent_detection
      - test_interactive_input_resumes_correct_workflow_in_multi_workflow
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    # ---- stream helpers ----

    @staticmethod
    async def _collect_stream(stream) -> list:
        """Drain an async generator into a list."""
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        return chunks

    @staticmethod
    def _find_chunks(
        chunks, chunk_type: str,
    ) -> list:
        """Filter OutputSchema chunks by type."""
        result = []
        for c in chunks:
            if isinstance(c, OutputSchema) and c.type == chunk_type:
                result.append(c)
        return result

    # ---- workflow helpers ----

    @staticmethod
    def _build_prefixed_workflow(
        workflow_id: str,
        workflow_name: str,
        prefix: str,
    ) -> Workflow:
        """Build a simple workflow: start -> end

        End uses responseTemplate with prefix so we can
        verify which workflow was selected by checking
        the prefix in the response.
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

    # ---- Case #11: intent detection routing ----

    @pytest.mark.asyncio
    async def test_multi_workflow_routing(self):
        """Invoke mode: two workflows registered, LLM-based
        intent detection routes query to the correct one.

        ST checkpoint alignment:
          - result is dict, result_type='answer'
          - response contains 'stock:' prefix
            (proving stock_flow was selected)
        """
        with mock_llm_context() as mock_llm:
            # IntentDetector expects JSON: {"result": N}
            # where N is 1-indexed category number.
            # category_list = [weather_desc, stock_desc]
            # so {"result": 2} selects stock workflow.
            mock_llm.set_responses([
                create_text_response('{"result": 2}'),
            ])

            # Build two workflows with descriptions
            weather_wf = self._build_prefixed_workflow(
                workflow_id="weather_flow",
                workflow_name="天气查询",
                prefix="weather:",
            )
            stock_wf = self._build_prefixed_workflow(
                workflow_id="stock_flow",
                workflow_name="股票查询",
                prefix="stock:",
            )

            # Set descriptions for intent detection
            weather_wf.card.description = (
                "查询某地的天气情况、温度、气象信息"
            )
            stock_wf.card.description = (
                "查询股票价格、股市行情、"
                "股票走势等金融信息"
            )

            # Create agent with model config
            # (required for multi-workflow intent detection)
            config = WorkflowAgentConfig(
                id="test_multi_wf_agent",
                version="1.0",
                description=(
                    "multi workflow routing test"
                ),
                workflows=[],
                model=_MODEL_CONFIG,
            )
            agent = WorkflowAgent(config)
            agent.add_workflows([weather_wf, stock_wf])

            # Invoke with stock-related query
            conv_id = str(uuid.uuid4())
            result = await agent.invoke({
                "query": "查看上海股票走势",
                "conversation_id": conv_id,
            })

            # Verify routing result
            self.assertIsInstance(result, dict)
            self.assertEqual(
                result["result_type"], "answer",
            )

            response = result["output"].result[
                "response"
            ]
            self.assertIn("stock:", response)
            self.assertIn("查看上海股票走势", response)

            # check get chat history from context
            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 2)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")

    # ---- helpers for Case #12 ----

    @staticmethod
    def _build_questioner_workflow(
        workflow_id: str,
        workflow_name: str,
        workflow_description: str,
        question: str,
    ) -> Workflow:
        """Build workflow: start -> questioner -> end

        Questioner uses preset question_content with
        extract_fields_from_response=False, so it triggers
        interrupt immediately without any LLM call.
        End collects ${questioner.user_response}.
        """
        card = WorkflowCard(
            id=workflow_id,
            name=workflow_name,
            version="1.0",
            description=workflow_description,
        )
        flow = Workflow(card=card)

        flow.set_start_comp(
            "start",
            Start(),
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "questioner",
            _make_questioner(question),
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

    # ---- Case #12: jump & recovery (stream) ----

    @pytest.mark.asyncio
    async def test_multi_workflow_jump_and_recovery(
        self,
    ):
        """Stream mode: workflow A interrupts, jump to B
        (via intent detection), then resume A and B
        with plain string queries.

        4-step scenario:
          step1: stream "查询天气" -> intent selects
                 weather_flow -> questioner interrupts
          step2: stream "查看股票" -> intent selects
                 stock_flow -> questioner interrupts
          step3: stream "查询北京天气" -> intent selects
                 weather_flow -> resumes interrupted task
                 -> workflow_final chunk
          step4: stream "查看AAPL股票" -> intent selects
                 stock_flow -> resumes interrupted task
                 -> workflow_final chunk

        ST checkpoint alignment:
          step1: interaction chunk, type='__interaction__'
          step2: interaction chunk, type='__interaction__'
          step3: workflow_final chunk, state=COMPLETED
          step4: workflow_final chunk, state=COMPLETED
        """
        with mock_llm_context() as mock_llm:
            # Intent detection mock responses (4 calls):
            # step1: {"result": 1} -> weather
            # step2: {"result": 2} -> stock
            # step3: {"result": 1} -> weather (resume)
            # step4: {"result": 2} -> stock (resume)
            mock_llm.set_responses([
                create_text_response(
                    '{"result": 1}',
                ),
                create_text_response(
                    '{"result": 2}',
                ),
                create_text_response(
                    '{"result": 1}',
                ),
                create_text_response(
                    '{"result": 2}',
                ),
            ])

            weather_wf = self._build_questioner_workflow(
                workflow_id="weather_flow_jump",
                workflow_name="天气查询",
                workflow_description=(
                    "查询某地的天气情况、温度、气象信息"
                ),
                question="请提供地点",
            )
            stock_wf = self._build_questioner_workflow(
                workflow_id="stock_flow_jump",
                workflow_name="股票查询",
                workflow_description=(
                    "查询股票价格、股市行情、"
                    "股票走势等金融信息"
                ),
                question="请提供股票代码",
            )

            config = WorkflowAgentConfig(
                id="test_jump_agent",
                version="1.0",
                description="jump recovery test",
                workflows=[],
                model=_MODEL_CONFIG,
            )
            agent = WorkflowAgent(config)
            agent.add_workflows(
                [weather_wf, stock_wf],
            )

            conv_id = "test_jump_recovery"

            # == Step 1: weather_flow interrupts ==
            chunks1 = await self._collect_stream(
                Runner.run_agent_streaming(
                    agent,
                    {
                        "query": "查询天气",
                        "conversation_id": conv_id,
                    },
                )
            )
            interactions1 = self._find_chunks(
                chunks1, "__interaction__",
            )
            self.assertEqual(
                len(interactions1), 1,
            )

            # == Step 2: jump to stock_flow ==
            chunks2 = await self._collect_stream(
                Runner.run_agent_streaming(
                    agent,
                    {
                        "query": "查看股票",
                        "conversation_id": conv_id,
                    },
                )
            )
            interactions2 = self._find_chunks(
                chunks2, "__interaction__",
            )
            self.assertEqual(
                len(interactions2), 1,
            )

            # == Step 3: resume weather_flow via string ==
            chunks3 = await self._collect_stream(
                Runner.run_agent_streaming(
                    agent,
                    {
                        "query": "查询北京天气",
                        "conversation_id": conv_id,
                    },
                )
            )
            finals3 = self._find_chunks(
                chunks3, "workflow_final",
            )
            self.assertGreater(len(finals3), 0)
            self.assertIsInstance(
                finals3[-1].payload, dict,
            )
            self.assertIn(
                "response", finals3[-1].payload,
            )

            # == Step 4: resume stock_flow via string ==
            chunks4 = await self._collect_stream(
                Runner.run_agent_streaming(
                    agent,
                    {
                        "query": "查看AAPL股票",
                        "conversation_id": conv_id,
                    },
                )
            )
            finals4 = self._find_chunks(
                chunks4, "workflow_final",
            )
            self.assertGreater(len(finals4), 0)
            self.assertIsInstance(
                finals4[-1].payload, dict,
            )
            self.assertIn(
                "response", finals4[-1].payload,
            )

            # check get chat history from context
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

    # ---- Case #13: InteractiveInput fast path ----

    @pytest.mark.asyncio
    async def test_interactive_input_fast_path(self):
        """Stream mode: InteractiveInput with node_id
        bypasses LLM intent detection entirely.

        2-step scenario:
          step1: stream "查询天气" -> intent detection LLM
                 selects weather_flow -> questioner
                 interrupts, capture node_id from
                 interaction chunk
          step2: stream InteractiveInput(node_id, "北京")
                 -> skips intent detection -> directly
                 resumes weather_flow -> workflow_final

        Only 1 LLM mock needed (step1 intent detection).
        Step2 uses InteractiveInput fast path, no LLM.

        ST checkpoint alignment:
          step1: interaction chunk, type='__interaction__'
                 payload has .id (node_id)
          step2: workflow_final chunk, payload has
                 'response' containing user answer
        """
        with mock_llm_context() as mock_llm:
            # Only 1 intent detection call (step1).
            # Step2 uses InteractiveInput -> no LLM.
            mock_llm.set_responses([
                create_text_response(
                    '{"result": 1}',
                ),
            ])

            weather_wf = self._build_questioner_workflow(
                workflow_id="weather_flow_skip",
                workflow_name="天气查询",
                workflow_description=(
                    "查询某地的天气情况、"
                    "温度、气象信息"
                ),
                question="请提供地点",
            )
            stock_wf = self._build_questioner_workflow(
                workflow_id="stock_flow_skip",
                workflow_name="股票查询",
                workflow_description=(
                    "查询股票价格、股市行情、"
                    "股票走势等金融信息"
                ),
                question="请提供股票代码",
            )

            config = WorkflowAgentConfig(
                id="test_interactive_skip",
                version="1.0",
                description=(
                    "interactive input fast path"
                ),
                workflows=[],
                model=_MODEL_CONFIG,
            )
            agent = WorkflowAgent(config)
            agent.add_workflows(
                [weather_wf, stock_wf],
            )

            conv_id = "test_interactive_fast_path"

            # == Step 1: weather_flow interrupts ==
            interaction_chunks = []
            async for chunk in agent.stream({
                "query": "查询天气",
                "conversation_id": conv_id,
            }):
                if (
                    hasattr(chunk, "type")
                    and chunk.type
                    == "__interaction__"
                ):
                    interaction_chunks.append(chunk)

            self.assertEqual(
                len(interaction_chunks), 1,
            )

            # Capture node_id from interaction payload
            node_id = (
                interaction_chunks[0].payload.id
            )
            self.assertIsNotNone(node_id)

            # == Step 2: resume via InteractiveInput ==
            # InteractiveInput carries node_id, so
            # workflow controller skips LLM intent
            # detection and directly resumes the
            # interrupted workflow.
            interactive_input = InteractiveInput()
            interactive_input.update(
                node_id, "北京",
            )

            final_chunk = None
            async for chunk in agent.stream({
                "query": interactive_input,
                "conversation_id": conv_id,
            }):
                if (
                    hasattr(chunk, "type")
                    and chunk.type
                    == "workflow_final"
                ):
                    final_chunk = chunk

            self.assertIsNotNone(final_chunk)
            self.assertIsInstance(
                final_chunk.payload, dict,
            )
            self.assertIn(
                "response", final_chunk.payload,
            )
            # Verify the response contains user's
            # answer "北京" (proving weather_flow
            # was resumed, not a new workflow).
            self.assertIn(
                "北京",
                final_chunk.payload["response"],
            )

            # check get chat history from context
            chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
            self.assertEqual(len(chat_history), 4)
            self.assertEqual(chat_history[0].role, "user")
            self.assertEqual(chat_history[1].role, "assistant")
            self.assertEqual(chat_history[2].role, "user")
            self.assertEqual(chat_history[3].role, "assistant")

    # ---- Case #14: InteractiveInput targets correct wf ----

    @pytest.mark.asyncio
    async def test_interactive_input_targets_correct_workflow(
        self,
    ):
        """Invoke mode: two workflows both interrupt,
        InteractiveInput with node_id resumes the correct
        one each time.

        4-step scenario:
          step1: invoke "查询天气" -> intent LLM selects
                 weather_flow -> questioner interrupts
                 -> capture node_id_1
          step2: invoke "查询股票" -> intent LLM selects
                 stock_flow -> questioner interrupts
                 -> capture node_id_2
          step3: invoke InteractiveInput(node_id_1, "北京")
                 -> skips intent detection -> resumes
                 weather_flow -> completed
          step4: invoke InteractiveInput(node_id_2, "AAPL")
                 -> skips intent detection -> resumes
                 stock_flow -> completed

        Only 2 LLM mocks needed (step1 + step2 intent).
        Step3/4 use InteractiveInput fast path, no LLM.

        ST checkpoint alignment:
          step1: result is list, [0].type='__interaction__'
                 payload.id = node_id_1
          step2: result is list, [0].type='__interaction__'
                 payload.id = node_id_2
          step3: result is dict, result_type='answer',
                 response contains '北京'
          step4: result is dict, result_type='answer',
                 response contains 'AAPL'
        """
        with mock_llm_context() as mock_llm:
            # 2 intent detection calls (step1 + step2).
            # step3/4 use InteractiveInput -> no LLM.
            mock_llm.set_responses([
                create_text_response(
                    '{"result": 1}',
                ),
                create_text_response(
                    '{"result": 2}',
                ),
            ])

            weather_wf = self._build_questioner_workflow(
                workflow_id="weather_flow_resume",
                workflow_name="天气查询",
                workflow_description=(
                    "查询某地的天气情况、"
                    "温度、气象信息"
                ),
                question="请提供地点",
            )
            stock_wf = self._build_questioner_workflow(
                workflow_id="stock_flow_resume",
                workflow_name="股票查询",
                workflow_description=(
                    "查询股票价格、股市行情、"
                    "股票走势等金融信息"
                ),
                question="请提供股票代码",
            )

            config = WorkflowAgentConfig(
                id="test_precise_resume",
                version="1.0",
                description=(
                    "precise resume test"
                ),
                workflows=[],
                model=_MODEL_CONFIG,
            )
            agent = WorkflowAgent(config)
            agent.add_workflows(
                [weather_wf, stock_wf],
            )

            conv_id = "test_precise_resume"

            # == Step 1: weather_flow interrupts ==
            result1 = await Runner.run_agent(
                agent,
                {
                    "query": "查询天气",
                    "conversation_id": conv_id,
                },
            )

            self.assertIsInstance(result1, list)
            self.assertGreater(len(result1), 0)
            self.assertEqual(
                result1[0].type,
                "__interaction__",
            )
            node_id_1 = result1[0].payload.id
            self.assertIsNotNone(node_id_1)

            # == Step 2: stock_flow interrupts ==
            result2 = await Runner.run_agent(
                agent,
                {
                    "query": "查询股票",
                    "conversation_id": conv_id,
                },
            )

            self.assertIsInstance(result2, list)
            self.assertGreater(len(result2), 0)
            self.assertEqual(
                result2[0].type,
                "__interaction__",
            )
            node_id_2 = result2[0].payload.id
            self.assertIsNotNone(node_id_2)

            # == Step 3: resume weather via node_id_1 ==
            interactive_1 = InteractiveInput()
            interactive_1.update(
                node_id_1, "北京",
            )

            result3 = await Runner.run_agent(
                agent,
                {
                    "query": interactive_1,
                    "conversation_id": conv_id,
                },
            )

            self.assertIsInstance(result3, dict)
            self.assertEqual(
                result3["result_type"], "answer",
            )
            resp_3 = result3["output"].result.get(
                "response", "",
            )
            self.assertIn("北京", resp_3)

            # == Step 4: resume stock via node_id_2 ==
            interactive_2 = InteractiveInput()
            interactive_2.update(
                node_id_2, "AAPL",
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
            resp_4 = result4["output"].result.get(
                "response", "",
            )
            self.assertIn("AAPL", resp_4)

            # check get chat history from context
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
