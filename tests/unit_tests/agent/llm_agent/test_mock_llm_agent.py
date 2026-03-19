# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for LLMAgentRefactor.

Covers:
  - Scenario 1: workflow interrupt + user feedback
  - Scenario 2: memory engine integration (load + write)
  - Scenario 3: multiple interrupts in a single superstep (two parallel questioners)
  - Scenario 4: two serial workflows each with their own interrupt/resume
  - Scenario 5: set_prompt_template
  - Scenario 6: add_tools (idempotency)
  - Scenario 7: add_workflows / remove_workflows (idempotency + cleanup)

Mock strategy:
- Questioner components use question_content (no LLM call needed for questioner itself)
- ReActAgent LLM calls mocked via MockLLMModel
- Use Runner.run_agent_streaming; collect __interaction__ OutputSchema from stream
- component_id is read from chunk.payload.id
"""
import asyncio
import os
import unittest
from typing import List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit_tests.agent.llm_agent.mock_llm_agent import (
    MockLLMAgent as LLMAgent,
    create_llm_agent,
    create_llm_agent_config,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseModelInfo,
    ModelConfig,
    ModelClientConfig,
    ModelRequestConfig,
    ToolCall,
    UsageMetadata,
)
from openjiuwen.core.memory.config.config import AgentMemoryConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.legacy import WorkflowSchema
from openjiuwen.core.workflow import (
    End,
    QuestionerComponent,
    QuestionerConfig,
    Start,
    Workflow,
    WorkflowCard,
)

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

_MODEL_CONFIG = ModelConfig(
    model_provider="OpenAI",
    model_info=BaseModelInfo(
        model="gpt-3.5-turbo",
        api_base="https://mock.api",
        api_key="mock-key",
    ),
)
_MODEL_CLIENT_CONFIG = ModelClientConfig(
    client_provider="OpenAI",
    api_key="sk-fake",
    api_base="https://mock.api/v1",
    verify_ssl=False,
)
_MODEL_REQUEST_CONFIG = ModelRequestConfig(model="gpt-3.5-turbo")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent_config(agent_id: str = "test_agent", **kwargs):
    return create_llm_agent_config(
        agent_id=agent_id,
        agent_version="1.0",
        description="test",
        workflows=kwargs.pop("workflows", []),
        plugins=kwargs.pop("plugins", []),
        model=kwargs.pop("model", _MODEL_CONFIG),
        prompt_template=kwargs.pop("prompt_template", [{"role": "system", "content": "You are a test assistant."}]),
        **kwargs,
    )


def _make_questioner(question: str) -> QuestionerComponent:
    """Questioner with preset question — no LLM call needed."""
    cfg = QuestionerConfig(
        model_client_config=_MODEL_CLIENT_CONFIG,
        model_config=_MODEL_REQUEST_CONFIG,
        question_content=question,
        extract_fields_from_response=False,
        with_chat_history=False,
    )
    return QuestionerComponent(questioner_comp_config=cfg)


def _make_single_questioner_workflow(wf_id: str, version: str = "1.0") -> Workflow:
    """start -> questioner -> end"""
    card = WorkflowCard(id=wf_id, name=wf_id, version=version,
                        input_params={"type": "object",
                                      "properties": {"query": {"type": "string"}},
                                      "required": ["query"]})
    flow = Workflow(card=card)
    flow.set_start_comp("s", Start(), inputs_schema={"query": "${query}"})
    flow.set_end_comp("e", End({"responseTemplate": "done: {{user_response}}"}),
                      inputs_schema={"user_response": "${questioner.user_response}"})
    flow.add_workflow_comp("questioner", _make_questioner("请问你的地点是什么？"),
                           inputs_schema={"query": "${s.query}"})
    flow.add_connection("s", "questioner")
    flow.add_connection("questioner", "e")
    return flow


def _make_workflow_schema(flow: Workflow) -> WorkflowSchema:
    return WorkflowSchema(
        id=flow.card.id, name=flow.card.name, version=flow.card.version,
        description=flow.card.description or "",
        inputs={"type": "object", "properties": {"query": {"type": "string"}}},
    )


async def _run_streaming(agent, query, conversation_id) -> Tuple[List[OutputSchema], List]:
    """Drain run_agent_streaming; return (__interaction__ chunks, all chunks)."""
    interaction_chunks, all_chunks = [], []
    async for chunk in Runner.run_agent_streaming(
        agent=agent,
        inputs={"conversation_id": conversation_id, "query": query},
    ):
        if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
            interaction_chunks.append(chunk)
        all_chunks.append(chunk)
    return interaction_chunks, all_chunks


# ---------------------------------------------------------------------------
# Scenario 1: workflow interrupt + user feedback
# ---------------------------------------------------------------------------

class TestLLMAgentRefactorWorkflowInterrupt(unittest.IsolatedAsyncioTestCase):
    """Scenario 1: single questioner workflow, interrupt then resume."""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_workflow_interrupt_and_resume(self):
        """1 interaction chunk on first invoke; 0 on second; final answer present."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call_001", type="function",
                                     name="wf_weather", arguments='{"query": "weather"}')],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            create_text_response("The weather in Shanghai is sunny."),
        ])

        flow = _make_single_questioner_workflow("wf_weather", "1.0")
        agent_config = _make_agent_config(
            agent_id="agent_s1", workflows=[_make_workflow_schema(flow)]
        )

        with patch("openjiuwen.core.foundation.llm.model.Model.invoke",
                   side_effect=mock_llm.invoke), \
             patch("openjiuwen.core.foundation.llm.model.Model.stream",
                   side_effect=mock_llm.stream), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()):

            agent: LLMAgent = create_llm_agent(
                agent_config=agent_config, workflows=[flow], tools=[]
            )
            conv_id = "conv_s1_interrupt"

            # First invoke — expect 1 interaction chunk
            chunks1, _ = await _run_streaming(agent, "weather query", conv_id)
            self.assertEqual(len(chunks1), 1, f"Expected 1 interaction chunk, got: {chunks1}")
            self.assertEqual(chunks1[0].payload.id, "questioner")

            # Second invoke — resume, expect 0 interaction chunks and a final answer
            user_input = InteractiveInput()
            user_input.update(chunks1[0].payload.id, "Shanghai")
            chunks2, all_chunks2 = await _run_streaming(agent, user_input, conv_id)
            self.assertEqual(len(chunks2), 0, f"Expected no interaction chunks, got: {chunks2}")
            self.assertGreater(len(all_chunks2), 0)
            last = all_chunks2[-1]
            self.assertIsInstance(last, OutputSchema)
            self.assertIn("Shanghai", str(last.payload))


# ---------------------------------------------------------------------------
# Scenario 2: memory engine integration
# ---------------------------------------------------------------------------

class TestLLMAgentRefactorMemory(unittest.IsolatedAsyncioTestCase):
    """Scenario 2: MemoryRail loads memory before invoke and writes after answer."""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_memory_load_and_write_on_answer(self):
        """search_user_mem called once; add_messages called after answer."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("Hello! I remember you.")])

        agent_config = _make_agent_config(agent_id="agent_memory")
        agent_config.memory_scope_id = "scope_001"
        agent_config.agent_memory_config = AgentMemoryConfig(
            enable_long_term_mem=True,
            enable_fragment_memory=True,
            enable_summary_memory=False,
        )

        mock_search = AsyncMock(return_value=[])
        mock_add = AsyncMock(return_value=None)

        agent: LLMAgent = create_llm_agent(
            agent_config=agent_config, workflows=[], tools=[]
        )

        with patch("openjiuwen.core.foundation.llm.model.Model.invoke",
                   side_effect=mock_llm.invoke), \
             patch("openjiuwen.core.foundation.llm.model.Model.stream",
                   side_effect=mock_llm.stream), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   new=AsyncMock(return_value=None)), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.search_user_mem",
                   mock_search), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.add_messages",
                   mock_add):

            interaction_chunks, all_chunks = [], []
            async for chunk in Runner.run_agent_streaming(
                agent=agent,
                inputs={"conversation_id": "conv_mem", "query": "Hello", "user_id": "user_001"},
            ):
                if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                    interaction_chunks.append(chunk)
                all_chunks.append(chunk)

            self.assertEqual(len(interaction_chunks), 0)
            self.assertGreater(len(all_chunks), 0)

            mock_search.assert_called_once()
            self.assertIn("user_001", str(mock_search.call_args))

            await asyncio.sleep(0.1)
            mock_add.assert_called_once()
            self.assertIn("user_001", str(mock_add.call_args))

    @pytest.mark.asyncio
    async def test_memory_not_written_on_interrupt(self):
        """add_messages must NOT be called when result is interrupt."""
        mock_llm = MockLLMModel()
        flow = _make_single_questioner_workflow("mem_wf", "1.0")
        mock_llm.set_responses([
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="c1", type="function", name="mem_wf",
                                     arguments='{"query": "q"}')],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
        ])

        agent_config = _make_agent_config(
            agent_id="agent_mem_interrupt", workflows=[_make_workflow_schema(flow)]
        )
        agent_config.memory_scope_id = "scope_001"
        agent_config.agent_memory_config = AgentMemoryConfig(
            enable_long_term_mem=True, enable_fragment_memory=True,
        )
        mock_add = AsyncMock(return_value=None)

        agent: LLMAgent = create_llm_agent(
            agent_config=agent_config, workflows=[flow], tools=[]
        )

        with patch("openjiuwen.core.foundation.llm.model.Model.invoke",
                   side_effect=mock_llm.invoke), \
             patch("openjiuwen.core.foundation.llm.model.Model.stream",
                   side_effect=mock_llm.stream), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   new=AsyncMock(return_value=None)), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.search_user_mem",
                   AsyncMock(return_value=[])), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.add_messages",
                   mock_add):

            interaction_chunks = []
            async for chunk in Runner.run_agent_streaming(
                agent=agent,
                inputs={"conversation_id": "conv_mi", "query": "q", "user_id": "user_001"},
            ):
                if isinstance(chunk, OutputSchema) and chunk.type == "__interaction__":
                    interaction_chunks.append(chunk)

            self.assertEqual(len(interaction_chunks), 1)

            await asyncio.sleep(0.1)
            mock_add.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 3: single superstep with two parallel questioners
# ---------------------------------------------------------------------------

class TestLLMAgentRefactorParallelInterrupt(unittest.IsolatedAsyncioTestCase):
    """Scenario 3: one workflow, two questioners in parallel (same superstep)."""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _build_parallel_workflow(wf_id: str) -> Workflow:
        """start -> questioner + questioner_2 -> end"""
        card = WorkflowCard(id=wf_id, name=wf_id, version="1.0",
                            input_params={"type": "object",
                                          "properties": {"query": {"type": "string"}},
                                          "required": ["query"]})
        flow = Workflow(card=card)
        flow.set_start_comp("s", Start(), inputs_schema={"query": "${query}"})
        flow.set_end_comp(
            "e",
            End({"responseTemplate": "name:{{name_resp}} addr:{{addr_resp}}"}),
            inputs_schema={
                "name_resp": "${questioner.user_response}",
                "addr_resp": "${questioner_2.user_response}",
            },
        )
        flow.add_workflow_comp("questioner", _make_questioner("请问你的姓名是什么？"),
                               inputs_schema={"query": "${s.query}"})
        flow.add_workflow_comp("questioner_2", _make_questioner("请问你的地址是什么？"),
                               inputs_schema={"query": "${s.query}"})
        flow.add_connection("s", "questioner")
        flow.add_connection("s", "questioner_2")
        flow.add_connection("questioner", "e")
        flow.add_connection("questioner_2", "e")
        return flow

    @pytest.mark.asyncio
    async def test_parallel_questioners_sequential_resume(self):
        """Two questioners in same superstep: each resolved one at a time."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call_p1", type="function",
                                     name="wf_parallel", arguments='{"query": "info"}')],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            create_text_response("Got both name and address."),
        ])

        flow = self._build_parallel_workflow("wf_parallel")
        agent_config = _make_agent_config(
            agent_id="agent_s3", workflows=[_make_workflow_schema(flow)]
        )

        with patch("openjiuwen.core.foundation.llm.model.Model.invoke",
                   side_effect=mock_llm.invoke), \
             patch("openjiuwen.core.foundation.llm.model.Model.stream",
                   side_effect=mock_llm.stream), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()):

            agent: LLMAgent = create_llm_agent(
                agent_config=agent_config, workflows=[flow], tools=[]
            )
            conv_id = "conv_s3_parallel"

            # invoke 1 — first questioner interrupts
            chunks1, _ = await _run_streaming(agent, "collect info", conv_id)
            self.assertEqual(len(chunks1), 1, f"invoke 1: expected 1 interaction chunk, got {chunks1}")
            first_comp = chunks1[0].payload.id
            self.assertIn(first_comp, ("questioner", "questioner_2"))

            # invoke 2 — answer first questioner, second questioner interrupts
            user_input_1 = InteractiveInput()
            user_input_1.update(first_comp, "Alice")
            chunks2, _ = await _run_streaming(agent, user_input_1, conv_id)
            self.assertEqual(len(chunks2), 1, f"invoke 2: expected 1 interaction chunk, got {chunks2}")
            second_comp = chunks2[0].payload.id
            self.assertNotEqual(second_comp, first_comp)

            # invoke 3 — answer second questioner, workflow completes
            user_input_2 = InteractiveInput()
            user_input_2.update(second_comp, "Beijing")
            chunks3, all_chunks3 = await _run_streaming(agent, user_input_2, conv_id)
            self.assertEqual(len(chunks3), 0, f"invoke 3: expected no interaction chunks, got {chunks3}")
            self.assertGreater(len(all_chunks3), 0)
            last = all_chunks3[-1]
            self.assertIsInstance(last, OutputSchema)
            self.assertIsNotNone(last.payload)


# ---------------------------------------------------------------------------
# Scenario 4: two serial workflows each with their own interrupt/resume
# ---------------------------------------------------------------------------

class TestLLMAgentRefactorTwoWorkflowsInterrupt(unittest.IsolatedAsyncioTestCase):
    """Scenario 4: LLM returns two tool_calls; each workflow interrupts and resumes serially."""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_two_workflows_serial_interrupt_resume(self):
        """wf_a interrupts first, then wf_b interrupts, then final answer."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="call_a", type="function",
                             name="wf_city_a", arguments='{"query": "city a"}'),
                    ToolCall(id="call_b", type="function",
                             name="wf_city_b", arguments='{"query": "city b"}'),
                ],
                usage_metadata=UsageMetadata(model_name="mock", finish_reason="tool_calls"),
            ),
            create_text_response("Cities collected: Shanghai and Beijing."),
        ])

        flow_a = _make_single_questioner_workflow("wf_city_a", "1.0")
        flow_b = _make_single_questioner_workflow("wf_city_b", "1.0")
        agent_config = _make_agent_config(
            agent_id="agent_s4",
            workflows=[_make_workflow_schema(flow_a), _make_workflow_schema(flow_b)],
        )

        with patch("openjiuwen.core.foundation.llm.model.Model.invoke",
                   side_effect=mock_llm.invoke), \
             patch("openjiuwen.core.foundation.llm.model.Model.stream",
                   side_effect=mock_llm.stream), \
             patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()):

            agent: LLMAgent = create_llm_agent(
                agent_config=agent_config, workflows=[flow_a, flow_b], tools=[]
            )
            conv_id = "conv_s4_two_wf"

            # invoke 1 — wf_a interrupts first
            chunks1, _ = await _run_streaming(agent, "collect cities", conv_id)
            self.assertEqual(len(chunks1), 1, f"invoke 1: expected 1 interaction chunk, got {chunks1}")
            self.assertEqual(chunks1[0].payload.id, "questioner")

            # invoke 2 — resume wf_a; wf_b interrupts
            user_input_1 = InteractiveInput()
            user_input_1.update(chunks1[0].payload.id, "Shanghai")
            chunks2, _ = await _run_streaming(agent, user_input_1, conv_id)
            self.assertEqual(len(chunks2), 1, f"invoke 2: expected 1 interaction chunk, got {chunks2}")
            self.assertEqual(chunks2[0].payload.id, "questioner")

            # invoke 3 — resume wf_b; final answer
            user_input_2 = InteractiveInput()
            user_input_2.update(chunks2[0].payload.id, "Beijing")
            chunks3, all_chunks3 = await _run_streaming(agent, user_input_2, conv_id)
            self.assertEqual(len(chunks3), 0, f"invoke 3: expected no interaction chunks, got {chunks3}")
            self.assertGreater(len(all_chunks3), 0)
            last = all_chunks3[-1]
            self.assertIsInstance(last, OutputSchema)
            self.assertIn("Shanghai", str(last.payload))
            self.assertIn("Beijing", str(last.payload))


# ---------------------------------------------------------------------------
# Scenario 5: set_prompt_template
# ---------------------------------------------------------------------------

class TestLLMAgentRefactorSetPromptTemplate(unittest.IsolatedAsyncioTestCase):
    """Scenario 5: set_prompt_template updates both agent_config and _inner."""

    def test_set_prompt_template_updates_config_and_inner(self):
        with patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()):
            agent_config = _make_agent_config(agent_id="agent_s5")
            agent = LLMAgent(agent_config)

            new_template = [{"role": "system", "content": "You are a new assistant."}]
            agent.set_prompt_template(new_template)

            self.assertEqual(agent.agent_config.prompt_template, new_template)
            # Verify _inner also received the update
            inner_template = agent._inner._config.prompt_template
            self.assertEqual(inner_template, new_template)


# ---------------------------------------------------------------------------
# Scenario 6: add_tools (idempotency)
# ---------------------------------------------------------------------------

class TestLLMAgentRefactorAddTools(unittest.IsolatedAsyncioTestCase):
    """Scenario 6: add_tools registers tools without duplicates."""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    def test_add_tools_idempotent(self):
        with patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()), \
             patch("openjiuwen.core.runner.Runner.resource_mgr") as mock_resource_mgr:
            mock_resource_mgr.add_tool = MagicMock()
            agent_config = _make_agent_config(agent_id="agent_s6")
            agent = LLMAgent(agent_config)

            mock_tool = MagicMock()
            mock_tool.card.name = "tool_alpha"
            mock_tool.card.id = "tool_alpha_id"
            mock_tool.description = "mock tool description"

            agent.add_tools([mock_tool])
            agent.add_tools([mock_tool])  # duplicate — should be ignored

            tool_names_in_config = [t for t in agent.agent_config.tools if t == "tool_alpha"]
            self.assertEqual(len(tool_names_in_config), 1,
                             f"Expected tool_alpha once in agent_config.tools, got: {agent.agent_config.tools}")

            tool_names_in_list = [t for t in agent.tools if t.card.name == "tool_alpha"]
            self.assertEqual(len(tool_names_in_list), 1,
                             f"Expected tool_alpha once in agent.tools, got: {agent.tools}")


# ---------------------------------------------------------------------------
# Scenario 7: add_workflows / remove_workflows (idempotency + cleanup)
# ---------------------------------------------------------------------------

class TestLLMAgentRefactorWorkflowManagement(unittest.IsolatedAsyncioTestCase):
    """Scenario 7: add_workflows is idempotent; remove_workflows cleans up."""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    def test_add_workflows_idempotent_and_remove(self):
        with patch("openjiuwen.core.memory.long_term_memory.LongTermMemory.set_scope_config",
                   return_value=MagicMock()):
            agent_config = _make_agent_config(agent_id="agent_s7")
            agent = LLMAgent(agent_config)

            flow = _make_single_questioner_workflow("wf_mgmt", "2.0")

            agent.add_workflows([flow])
            agent.add_workflows([flow])  # duplicate — should be ignored

            matching = [w for w in agent.agent_config.workflows
                        if w.id == "wf_mgmt" and w.version == "2.0"]
            self.assertEqual(len(matching), 1,
                             f"Expected wf_mgmt once in agent_config.workflows, got: {agent.agent_config.workflows}")

            agent.remove_workflows([("wf_mgmt", "2.0")])

            remaining = [w for w in agent.agent_config.workflows
                         if w.id == "wf_mgmt" and w.version == "2.0"]
            self.assertEqual(len(remaining), 0,
                             f"Expected wf_mgmt removed, but still found: {agent.agent_config.workflows}")
