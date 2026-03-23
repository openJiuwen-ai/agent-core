# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Tests for LLMAgentRefactor auto-session management.

Verifies that LLMAgentRefactor.invoke() and .stream() work correctly
when called WITHOUT an explicit session parameter. The underlying
ReActAgent should auto-create a Session, manage its lifecycle
(pre_run / post_run), and support multi-turn conversation via
conversation_id.

Scenarios:
  - invoke: LLM returns ToolCall -> workflow executes -> LLM summarises
  - stream: same flow but via async generator, collecting OutputSchema
"""
import os
import unittest
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseModelInfo,
    ModelConfig,
    ModelClientConfig,
    ModelRequestConfig,
    ToolCall,
    UsageMetadata,
)
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

from tests.unit_tests.agent.llm_agent.mock_llm_agent import (
    MockLLMAgent as LLMAgent,
    create_llm_agent,
    create_llm_agent_config,
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
        prompt_template=kwargs.pop(
            "prompt_template",
            [{"role": "system", "content": "You are a test assistant."}],
        ),
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


def _make_single_questioner_workflow(
    wf_id: str, version: str = "1.0"
) -> Workflow:
    """start -> questioner -> end"""
    card = WorkflowCard(
        id=wf_id, name=wf_id, version=version,
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    flow = Workflow(card=card)
    flow.set_start_comp("s", Start(), inputs_schema={"query": "${query}"})
    flow.set_end_comp(
        "e",
        End({"responseTemplate": "done: {{user_response}}"}),
        inputs_schema={"user_response": "${questioner.user_response}"},
    )
    flow.add_workflow_comp(
        "questioner",
        _make_questioner("What is your location?"),
        inputs_schema={"query": "${s.query}"},
    )
    flow.add_connection("s", "questioner")
    flow.add_connection("questioner", "e")
    return flow


def _make_workflow_schema(flow: Workflow) -> WorkflowSchema:
    return WorkflowSchema(
        id=flow.card.id, name=flow.card.name, version=flow.card.version,
        description=flow.card.description or "",
        inputs={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    )


def _llm_patches(mock_llm: MockLLMModel):
    """Return a combined patch context for LLM invoke/stream + memory."""
    return (
        patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            side_effect=mock_llm.invoke,
        ),
        patch(
            "openjiuwen.core.foundation.llm.model.Model.stream",
            side_effect=mock_llm.stream,
        ),
        patch(
            "openjiuwen.core.memory.long_term_memory."
            "LongTermMemory.set_scope_config",
            return_value=MagicMock(),
        ),
    )


# ---------------------------------------------------------------------------
# Test: invoke without session
# ---------------------------------------------------------------------------

class TestLLMAgentRefactorInvokeAutoSession(
    unittest.IsolatedAsyncioTestCase
):
    """invoke() without session: auto-create, workflow interrupt/resume."""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_invoke_auto_session_workflow_interrupt_and_resume(self):
        """Call agent.invoke() directly (no session).

        1st invoke: LLM emits ToolCall -> workflow interrupts
        2nd invoke: InteractiveInput resumes -> LLM summarises
        """
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            # 1st LLM call: decide to call workflow
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(
                    id="call_001", type="function",
                    name="wf_auto", arguments='{"query": "hello"}',
                )],
                usage_metadata=UsageMetadata(
                    model_name="mock", finish_reason="tool_calls",
                ),
            ),
            # 2nd LLM call: summarise after workflow completes
            create_text_response(
                "Collected info: Shanghai. Task complete."
            ),
        ])

        flow = _make_single_questioner_workflow("wf_auto", "1.0")
        agent_config = _make_agent_config(
            agent_id="agent_invoke_auto",
            workflows=[_make_workflow_schema(flow)],
        )

        p1, p2, p3 = _llm_patches(mock_llm)
        with p1, p2, p3:
            agent: LLMAgent = create_llm_agent(
                agent_config=agent_config, workflows=[flow], tools=[],
            )
            conv_id = "conv_invoke_auto"

            # --- 1st invoke: expect interrupt ---
            result = await agent.invoke(
                {"query": "collect info", "conversation_id": conv_id},
            )
            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0)
            self.assertEqual(result[0].type, "__interaction__")
            self.assertEqual(result[0].payload.id, "questioner")

            # --- 2nd invoke: resume with user answer ---
            user_input = InteractiveInput()
            user_input.update("questioner", "Shanghai")
            result = await agent.invoke(
                {"query": user_input, "conversation_id": conv_id},
            )
            self.assertIsInstance(result, dict)
            self.assertEqual(result["result_type"], "answer")
            self.assertIn("Shanghai", result["output"])


# ---------------------------------------------------------------------------
# Test: stream without session
# ---------------------------------------------------------------------------

class TestLLMAgentRefactorStreamAutoSession(
    unittest.IsolatedAsyncioTestCase
):
    """stream() without session: auto-create, yield OutputSchema chunks."""

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_stream_auto_session_workflow_interrupt_and_resume(self):
        """Call agent.stream() directly (no session).

        1st stream: LLM emits ToolCall -> workflow interrupts
        2nd stream: InteractiveInput resumes -> LLM summarises
        """
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(
                    id="call_s01", type="function",
                    name="wf_stream", arguments='{"query": "weather"}',
                )],
                usage_metadata=UsageMetadata(
                    model_name="mock", finish_reason="tool_calls",
                ),
            ),
            create_text_response(
                "Weather in Beijing is sunny."
            ),
        ])

        flow = _make_single_questioner_workflow("wf_stream", "1.0")
        agent_config = _make_agent_config(
            agent_id="agent_stream_auto",
            workflows=[_make_workflow_schema(flow)],
        )

        p1, p2, p3 = _llm_patches(mock_llm)
        with p1, p2, p3:
            agent: LLMAgent = create_llm_agent(
                agent_config=agent_config, workflows=[flow], tools=[],
            )
            conv_id = "conv_stream_auto"

            # --- 1st stream: expect interaction chunk ---
            interaction_chunks = []
            all_chunks = []
            async for chunk in agent.stream(
                {"query": "weather query", "conversation_id": conv_id},
            ):
                if (isinstance(chunk, OutputSchema)
                        and chunk.type == "__interaction__"):
                    interaction_chunks.append(chunk)
                all_chunks.append(chunk)

            self.assertEqual(
                len(interaction_chunks), 1,
                f"Expected 1 interaction chunk, got {interaction_chunks}",
            )
            self.assertEqual(
                interaction_chunks[0].payload.id, "questioner",
            )

            # --- 2nd stream: resume, expect final answer ---
            user_input = InteractiveInput()
            user_input.update("questioner", "Beijing")
            interaction_chunks_2 = []
            all_chunks_2 = []
            async for chunk in agent.stream(
                {"query": user_input, "conversation_id": conv_id},
            ):
                if (isinstance(chunk, OutputSchema)
                        and chunk.type == "__interaction__"):
                    interaction_chunks_2.append(chunk)
                all_chunks_2.append(chunk)

            self.assertEqual(
                len(interaction_chunks_2), 0,
                f"Expected no interaction chunks, got {interaction_chunks_2}",
            )
            self.assertGreater(len(all_chunks_2), 0)
            last = all_chunks_2[-1]
            self.assertIsInstance(last, OutputSchema)
            self.assertIn("Beijing", str(last.payload))


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
