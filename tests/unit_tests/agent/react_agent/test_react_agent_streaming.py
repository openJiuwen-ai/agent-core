# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for ReActAgent streaming (_railed_model_call streaming path).

Covers:
- _streaming=True  -> llm.stream() called, llm_output chunks written
- _streaming=False -> fallback to llm.invoke(), no stream writes
"""
import os
import unittest
from unittest.mock import patch

import pytest

from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.runner import Runner

from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


def _make_agent(agent_id: str = "test_stream_agent") -> ReActAgent:
    card = AgentCard(id=agent_id)
    agent = ReActAgent(card=card)
    config = ReActAgentConfig()
    config.configure_model_client(
        provider="OpenAI",
        api_key="sk-fake",
        api_base="https://api.openai.com/v1",
        model_name="gpt-3.5-turbo",
        verify_ssl=False,
    )
    config.configure_prompt_template([{"role": "system", "content": "You are a helpful assistant."}])
    agent.configure(config)
    return agent


class TestReActAgentStreaming(unittest.IsolatedAsyncioTestCase):
    """Verify that _railed_model_call uses llm.stream() when _streaming=True."""

    async def asyncSetUp(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_streaming_writes_llm_output_chunks_to_session(self):
        """When _streaming=True, llm.stream() is called and llm_output frames are written."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("Hello from streaming!")])

        written_frames = []

        with patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            side_effect=mock_llm.invoke,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model.stream",
            side_effect=mock_llm.stream,
        ):
            agent = _make_agent("agent_stream_llm_output")
            session = create_agent_session(
                session_id="sess_stream_001",
                card=AgentCard(id="agent_stream_llm_output"),
            )
            await session.pre_run(inputs={"query": "hi"})

            original_write = session.write_stream

            async def capture(frame):
                written_frames.append(frame)
                return await original_write(frame)

            session.write_stream = capture
            result = await agent.invoke({"query": "hi"}, session=session, _streaming=True)

        self.assertEqual(result.get("result_type"), "answer")
        self.assertIn("Hello from streaming!", result.get("output", ""))

        llm_output_frames = [f for f in written_frames if hasattr(f, "type") and f.type == "llm_output"]
        self.assertGreater(len(llm_output_frames), 0)
        streamed_text = "".join(f.payload.get("content", "") for f in llm_output_frames)
        self.assertIn("Hello from streaming!", streamed_text)

    @pytest.mark.asyncio
    async def test_no_session_falls_back_to_invoke(self):
        """When _streaming is False (default), llm.invoke() is used instead of stream()."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("Fallback answer.")])

        stream_call_count = {"n": 0}
        invoke_call_count = {"n": 0}
        original_stream = mock_llm.stream
        original_invoke = mock_llm.invoke

        async def counting_stream(*args, **kwargs):
            stream_call_count["n"] += 1
            async for chunk in original_stream(*args, **kwargs):
                yield chunk

        async def counting_invoke(*args, **kwargs):
            invoke_call_count["n"] += 1
            return await original_invoke(*args, **kwargs)

        with patch(
            "openjiuwen.core.foundation.llm.model.Model.stream",
            side_effect=counting_stream,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            side_effect=counting_invoke,
        ):
            agent = _make_agent("agent_no_session")
            result = await agent.invoke({"query": "hello"}, session=None)

        self.assertEqual(result.get("result_type"), "answer")
        self.assertIn("Fallback answer.", result.get("output", ""))
        self.assertEqual(stream_call_count["n"], 0, "stream() must NOT be called when session is None")
        self.assertEqual(invoke_call_count["n"], 1, "invoke() must be called once as fallback")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
