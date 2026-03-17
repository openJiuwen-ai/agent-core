# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end tests for the force-finish rail signal.

Each test creates a real ReActAgent with a MockLLM, registers a custom
AgentRail that calls ``ctx.request_force_finish()``, and verifies the
agent returns the forced result through the full invoke() path.
"""

import os
import unittest
from unittest.mock import patch

import pytest

from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig, ReActAgent
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.single_agent.rail.base import (
    AgentRail,
    AgentCallbackContext,
)

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


# ============================================================
# Helpers
# ============================================================


def _create_agent():
    """Create a configured ReActAgent with an 'add' tool."""
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    card = AgentCard(description="force-finish 测试助手")
    config = ReActAgentConfig(
        model_config_obj=ModelRequestConfig(
            model="gpt-3.5-turbo",
            temperature=0.8,
            top_p=0.9,
        ),
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="mock_key",
            api_base="mock_url",
            timeout=30,
            verify_ssl=False,
        ),
        prompt_template=[dict(role="system", content="你是一个数学计算助手。")],
    )
    agent = ReActAgent(card=card).configure(config)

    tool = LocalFunction(
        card=ToolCard(
            id="add",
            name="add",
            description="加法运算",
            input_params={
                "type": "object",
                "properties": {
                    "a": {"description": "第一个加数", "type": "number"},
                    "b": {"description": "第二个加数", "type": "number"},
                },
                "required": ["a", "b"],
            },
        ),
        func=lambda a, b: a + b,
    )
    agent.ability_manager.add(tool.card)
    from openjiuwen.core.runner import Runner

    if Runner.resource_mgr.get_tool(tool.card.id) is None:
        Runner.resource_mgr.add_tool(tool)

    return agent


# ============================================================
# Test Cases
# ============================================================


class TestForceFinishE2E(unittest.IsolatedAsyncioTestCase):
    """End-to-end force-finish tests through ReActAgent.invoke()."""

    async def test_before_model_call_skips_llm_and_returns_result(self):
        """A rail that force-finishes in before_model_call should skip the LLM entirely and return the forced result."""
        agent = _create_agent()
        forced = {"output": "intercepted", "result_type": "answer"}

        class InterceptRail(AgentRail):
            async def before_model_call(self, ctx: AgentCallbackContext):
                ctx.request_force_finish(forced)

        await agent.register_rail(InterceptRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("should not see")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "hello"})

        assert result == forced
        assert mock_llm.call_count == 0

    async def test_after_model_call_stops_before_tool_execution(self):
        """Force-finish in after_model_call prevents tool execution."""
        agent = _create_agent()
        forced = {"output": "stopped_after_model", "result_type": "answer"}
        tool_called = {"yes": False}

        class StopAfterModelRail(AgentRail):
            async def after_model_call(self, ctx: AgentCallbackContext):
                ctx.request_force_finish(forced)

            async def before_tool_call(self, ctx: AgentCallbackContext):
                tool_called["yes"] = True

        await agent.register_rail(StopAfterModelRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses(
            [
                create_tool_call_response("add", '{"a": 1, "b": 2}'),
                create_text_response("should not see"),
            ]
        )
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "1+2"})

        assert result == forced
        assert not tool_called["yes"]

    async def test_after_tool_call_breaks_loop(self):
        """A rail that force-finishes in after_tool_call should break the ReAct loop after the tool executes."""
        agent = _create_agent()
        forced = {"output": "done_after_tool", "result_type": "answer"}

        class StopAfterToolRail(AgentRail):
            async def after_tool_call(self, ctx: AgentCallbackContext):
                ctx.request_force_finish(forced)

        await agent.register_rail(StopAfterToolRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses(
            [
                create_tool_call_response("add", '{"a": 3, "b": 4}'),
                create_text_response("should not see"),
            ]
        )
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "3+4"})

        assert result == forced
        # Only one LLM call (the one that returned tool_calls)
        assert mock_llm.call_count == 1

    async def test_force_finish_result_visible_in_after_invoke(self):
        """The forced result should be accessible in after_invoke through ctx.inputs.result."""
        agent = _create_agent()
        forced = {"output": "forced_result", "result_type": "answer"}
        captured = []

        class CaptureRail(AgentRail):
            async def before_model_call(self, ctx: AgentCallbackContext):
                ctx.request_force_finish(forced)

            async def after_invoke(self, ctx: AgentCallbackContext):
                captured.append(ctx.inputs.result)

        await agent.register_rail(CaptureRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("nope")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "test"})

        assert len(captured) == 1
        assert captured[0] == forced

    async def test_force_finish_with_conversation_id(self):
        """Force-finish works correctly when conversation_id is provided."""
        agent = _create_agent()
        forced = {"output": "with_conv_id", "result_type": "answer"}

        class InterceptRail(AgentRail):
            async def before_model_call(self, ctx: AgentCallbackContext):
                ctx.request_force_finish(forced)

        await agent.register_rail(InterceptRail())

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("nope")])
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {
                    "query": "test",
                    "conversation_id": "conv_123",
                }
            )

        assert result == forced


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
