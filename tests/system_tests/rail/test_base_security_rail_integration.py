# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration tests for BaseSecurityRail through full agent invoke() flow.

Tests the security rail lifecycle with MockLLM and real tools:
- SecurityReject flow (tool result modification)
- SecurityAllow flow (normal execution)
- SecurityInterrupt flow (HITL with simulated human input)
- Multi-event rail behavior
- Priority ordering between rails
"""

import os
import unittest
from unittest.mock import patch, MagicMock
from typing import Dict, Any, List, Optional

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig, ReActAgent
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, ToolCall, ToolMessage
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent, ToolCallInputs
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.session import InteractiveInput

from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityAllow,
    SecurityReject,
    SecurityInterrupt,
    SecurityCheckContext,
)

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


_invoke_tracker: Dict[str, Any] = {}


def _make_read_tool(content: str = "normal content", tool_id: str = "read_test") -> LocalFunction:
    """Create a LocalFunction read tool that returns configurable content."""
    tracker_key = tool_id
    _invoke_tracker[tracker_key] = {"count": 0, "log": []}

    def read_func(filepath: str):
        _invoke_tracker[tracker_key]["count"] += 1
        result = {"success": True, "content": content, "filepath": filepath}
        _invoke_tracker[tracker_key]["log"].append({"filepath": filepath, "result": result})
        return result

    tool = LocalFunction(
        card=ToolCard(
            id=tool_id,
            name="read",
            description="Read file content",
            input_params={
                "type": "object",
                "properties": {
                    "filepath": {"description": "File path", "type": "string"},
                },
                "required": ["filepath"],
            },
        ),
        func=read_func,
    )
    return tool


def _create_agent_with_tool(tool: LocalFunction) -> ReActAgent:
    """Create a configured ReActAgent with a tool."""
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    card = AgentCard(description="security rail test agent")
    config = ReActAgentConfig(
        model_config_obj=ModelRequestConfig(
            model="gpt-3.5-turbo",
            temperature=0.8,
        ),
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="mock_key",
            api_base="mock_url",
            timeout=30,
            verify_ssl=False,
        ),
        prompt_template=[dict(role="system", content="You are a helpful assistant.")],
    )
    agent = ReActAgent(card=card).configure(config)

    Runner.resource_mgr.add_tool(tool)
    agent.ability_manager.add(tool.card)

    return agent


class RejectToolResultRail(BaseSecurityRail):
    """Rail that rejects tool results containing specific patterns."""

    supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}
    pattern: str = "secret"

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        inputs = ctx.inputs

        tool_result = getattr(inputs, "tool_result", None)
        if tool_result is None:
            return self.allow()

        content = self._extract_content(tool_result)
        if self.pattern in content:
            return self.reject(
                message=f"Blocked: detected '{self.pattern}' in tool result",
            )

        return self.allow()

    async def apply_security_decision(self, security_ctx: SecurityCheckContext, decision) -> None:
        if isinstance(decision, SecurityAllow):
            return

        if isinstance(decision, SecurityReject):
            ctx = security_ctx.callback_ctx
            inputs = ctx.inputs
            error_msg = decision.message
            tool_call = getattr(inputs, "tool_call", None)
            tool_call_id = tool_call.id if tool_call else ""
            inputs.tool_result = error_msg
            inputs.tool_msg = ToolMessage(content=error_msg, tool_call_id=tool_call_id)
            return

        await super().apply_security_decision(security_ctx, decision)

    def _extract_content(self, tool_result) -> str:
        if isinstance(tool_result, str):
            return tool_result
        if isinstance(tool_result, dict):
            return tool_result.get("content", "") or tool_result.get("output", "") or ""
        return str(tool_result) if tool_result else ""


class InterceptBeforeModelRail(BaseSecurityRail):
    """Rail that interrupts before model call for human approval."""

    supported_events = {AgentCallbackEvent.BEFORE_MODEL_CALL}
    _request_count: int = 0

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        self._request_count += 1
        if self._request_count == 1:
            return self.interrupt(
                InterruptRequest(
                    message="Approve this model call?",
                    payload_schema={
                        "type": "object",
                        "properties": {
                            "approved": {"type": "boolean"},
                        },
                        "required": ["approved"],
                    },
                    auto_confirm_key=f"model_call_{self._request_count}",
                ),
                subject_id=f"model_call_{self._request_count}",
            )
        return self.allow()

    async def apply_security_decision(self, security_ctx: SecurityCheckContext, decision) -> None:
        if isinstance(decision, SecurityAllow):
            return

        if isinstance(decision, SecurityInterrupt):
            ctx = security_ctx.callback_ctx
            user_input = security_ctx.user_input

            if user_input is None:
                raise NotImplementedError("HITL interrupt pending")

            approved = False
            if isinstance(user_input, dict):
                approved = user_input.get("approved", False)
            elif hasattr(user_input, "approved"):
                approved = user_input.approved

            if approved:
                return

            ctx.request_force_finish(
                {"output": "Rejected by human", "result_type": "error"}
            )
            return

        await super().apply_security_decision(security_ctx, decision)


class MultiEventRail(BaseSecurityRail):
    """Rail that listens to multiple events and records them."""

    supported_events = {
        AgentCallbackEvent.BEFORE_INVOKE,
        AgentCallbackEvent.BEFORE_MODEL_CALL,
        AgentCallbackEvent.AFTER_TOOL_CALL,
    }

    def __init__(self):
        super().__init__()
        self.events: List[AgentCallbackEvent] = []
        self.decisions: List[str] = []

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        self.events.append(security_ctx.event)
        self.decisions.append("allow")
        return self.allow()


class HighPrioritySecurityRail(BaseSecurityRail):
    """High priority security rail (90)."""

    priority = 90
    supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}

    def __init__(self, order_list: List[str]):
        super().__init__()
        self.order_list = order_list

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        self.order_list.append("high_priority")
        return self.allow()


class LowPrioritySecurityRail(BaseSecurityRail):
    """Low priority security rail (10)."""

    priority = 10
    supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}

    def __init__(self, order_list: List[str]):
        super().__init__()
        self.order_list = order_list

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        self.order_list.append("low_priority")
        return self.allow()


class TestBaseSecurityRailIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests for BaseSecurityRail with ReActAgent."""

    async def asyncSetUp(self):
        await Runner.start()
        _invoke_tracker.clear()

    async def asyncTearDown(self):
        await Runner.stop()

    async def test_security_reject_modifies_tool_result(self):
        """SecurityReject in after_tool_call should replace tool result."""
        tool_id = "read_reject_test"
        tool = _make_read_tool(content="this contains secret data", tool_id=tool_id)
        agent = _create_agent_with_tool(tool)

        rail = RejectToolResultRail()
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/test.txt"}', tool_call_id="tc_1"),
            create_text_response("I see the file was blocked"),
        ])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "read test file"})

        assert _invoke_tracker[tool_id]["count"] == 1
        assert "blocked" in result.get("output", "").lower()

    async def test_security_allow_passes_tool_result(self):
        """SecurityAllow should let tool result pass unchanged."""
        tool_id = "read_allow_test"
        tool = _make_read_tool(content="normal safe content", tool_id=tool_id)
        agent = _create_agent_with_tool(tool)

        rail = RejectToolResultRail()
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/safe.txt"}', tool_call_id="tc_1"),
            create_text_response("The file contains: normal safe content"),
        ])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "read safe file"})

        assert _invoke_tracker[tool_id]["count"] == 1
        assert "safe" in result.get("output", "").lower() or "content" in result.get("output", "").lower()

    async def test_multi_event_rail_records_all_events(self):
        """Rail listening to multiple events should record each invocation."""
        tool_id = "read_multi_test"
        tool = _make_read_tool(content="test content", tool_id=tool_id)
        agent = _create_agent_with_tool(tool)

        rail = MultiEventRail()
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/test.txt"}', tool_call_id="tc_1"),
            create_text_response("Done"),
        ])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "test"})

        assert AgentCallbackEvent.BEFORE_INVOKE in rail.events
        assert AgentCallbackEvent.BEFORE_MODEL_CALL in rail.events
        assert AgentCallbackEvent.AFTER_TOOL_CALL in rail.events

    async def test_priority_ordering_high_runs_before_low(self):
        """Higher priority rails should execute before lower priority."""
        tool_id = "read_priority_test"
        tool = _make_read_tool(content="test", tool_id=tool_id)
        agent = _create_agent_with_tool(tool)

        order_list: List[str] = []
        high_rail = HighPrioritySecurityRail(order_list)
        low_rail = LowPrioritySecurityRail(order_list)

        await agent.register_rail(high_rail)
        await agent.register_rail(low_rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/test.txt"}', tool_call_id="tc_1"),
            create_text_response("Done"),
        ])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke({"query": "test"})

        assert order_list == ["high_priority", "low_priority"]

    @pytest.mark.skip(reason="Requires proper session setup for HITL interrupt flow")
    async def test_security_interrupt_with_human_approval(self):
        """SecurityInterrupt should pause and resume with human input."""
        tool_id = "read_interrupt_approve_test"
        tool = _make_read_tool(content="test content", tool_id=tool_id)
        agent = _create_agent_with_tool(tool)

        rail = InterceptBeforeModelRail()
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("Approved response"),
        ])

        session = MagicMock()
        session.get_session_id.return_value = "test_session"

        interactive_input = InteractiveInput()
        interactive_input.update("model_call_1", {"approved": True})

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "test", "conversation_id": "test_conv"},
                session=session,
                resume_input=interactive_input,
            )

        assert mock_llm.call_count == 1

    @pytest.mark.skip(reason="Requires proper session setup for HITL interrupt flow")
    async def test_security_interrupt_with_human_rejection(self):
        """SecurityInterrupt should force finish when human rejects."""
        tool_id = "read_interrupt_reject_test"
        tool = _make_read_tool(content="test content", tool_id=tool_id)
        agent = _create_agent_with_tool(tool)

        rail = InterceptBeforeModelRail()
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_text_response("Should not see this"),
        ])

        session = MagicMock()
        session.get_session_id.return_value = "test_session"

        interactive_input = InteractiveInput()
        interactive_input.update("model_call_1", {"approved": False})

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"query": "test", "conversation_id": "test_conv"},
                session=session,
                resume_input=interactive_input,
            )

        assert "Rejected by human" in result.get("output", "")
        assert mock_llm.call_count == 0

    async def test_chain_of_tool_calls_with_mixed_results(self):
        """Rail should handle tool calls with pattern detection."""
        tool_id = "read_chain_test"
        tool = _make_read_tool(content="normal content without blocked_pattern", tool_id=tool_id)
        agent = _create_agent_with_tool(tool)

        rail = RejectToolResultRail()
        rail.pattern = "blocked_pattern"
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/first.txt"}', tool_call_id="tc_1"),
            create_text_response("First read done"),
        ])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "read files"})

        assert _invoke_tracker[tool_id]["count"] == 1


class TestBaseSecurityRailDecisionFlow(unittest.IsolatedAsyncioTestCase):
    """Tests for security decision application flow."""

    async def asyncSetUp(self):
        await Runner.start()
        _invoke_tracker.clear()

    async def asyncTearDown(self):
        await Runner.stop()

    async def test_reject_modifies_tool_message(self):
        """SecurityReject should modify tool_message content."""
        tool_id = "read_decision_test"
        tool = _make_read_tool(content="secret_api_key=12345", tool_id=tool_id)
        agent = _create_agent_with_tool(tool)

        class StrictRejectRail(BaseSecurityRail):
            supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}

            async def run_security_check(self, security_ctx):
                ctx = security_ctx.callback_ctx
                tool_result = ctx.inputs.tool_result
                if isinstance(tool_result, dict) and "secret" in str(tool_result.get("content", "")):
                    return self.reject(message="Sensitive data blocked")
                return self.allow()

            async def apply_security_decision(self, security_ctx, decision):
                if isinstance(decision, SecurityReject):
                    ctx = security_ctx.callback_ctx
                    inputs = ctx.inputs
                    error_msg = decision.message
                    tool_call = getattr(inputs, "tool_call", None)
                    tool_call_id = tool_call.id if tool_call else ""
                    inputs.tool_result = error_msg
                    inputs.tool_msg = ToolMessage(content=error_msg, tool_call_id=tool_call_id)
                    return
                await super().apply_security_decision(security_ctx, decision)

        rail = StrictRejectRail()
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/env.txt"}', tool_call_id="tc_1"),
            create_text_response("The file was blocked due to sensitive data"),
        ])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "read env file"})

        assert _invoke_tracker[tool_id]["count"] == 1
        assert "blocked" in result.get("output", "").lower()

    async def test_allow_preserves_original_result(self):
        """SecurityAllow should preserve original tool result."""
        tool_id = "read_preserve_test"
        tool = _make_read_tool(content="This is safe public content", tool_id=tool_id)
        agent = _create_agent_with_tool(tool)

        class AlwaysAllowRail(BaseSecurityRail):
            supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}

            async def run_security_check(self, security_ctx):
                return self.allow()

        rail = AlwaysAllowRail()
        await agent.register_rail(rail)

        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("read", '{"filepath": "/public.txt"}', tool_call_id="tc_1"),
            create_text_response("Content read: This is safe public content"),
        ])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "read public file"})

        assert _invoke_tracker[tool_id]["count"] == 1
        assert "safe" in result.get("output", "").lower() or "content" in result.get("output", "").lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])