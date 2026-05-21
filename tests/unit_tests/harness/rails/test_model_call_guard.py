# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ModelCallGuard example rail with pop behavior."""

import pytest

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.harness.rails.security.base_security_rail import (
    SecurityAlert,
    SecurityAlertLevel,
    SecurityAllow,
    SecurityCheckContext,
    SecurityReject,
)

from examples.security_rail_demo.ModelCallGuard.rail import ModelcallguardRail


class MockModelContext:
    """Mock ModelContext that tracks messages and pops from back."""

    def __init__(self, messages=None):
        self._messages = list(messages) if messages else []
        self._popped = []

    def get_messages(self, size=None, with_history=True):
        if size is None:
            return list(self._messages)
        return self._messages[-size:]

    def pop_messages(self, size=1, with_history=True):
        popped = self._messages[-(size if size else 1):]
        self._messages = self._messages[:-size] if size else []
        self._popped.extend(popped)
        return popped

    def set_messages(self, messages, with_history=True):
        old = list(self._messages)
        self._messages = list(messages)
        self._popped = [m for m in old if m not in self._messages]

    def __len__(self):
        return len(self._messages)


class TestModelCallGuardInputPop:
    """Tests for BEFORE_MODEL_CALL pop behavior."""

    @pytest.mark.asyncio
    async def test_before_pops_user_message_with_secret(self):
        rail = ModelcallguardRail()
        context = MockModelContext(messages=[
            UserMessage(content="My API key is sk-1234567890abcdefghijklmnop"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
            ),
            context=context,
        )

        await rail.before_model_call(ctx)

        assert len(context._messages) == 0
        assert len(context._popped) == 1
        assert "sk-1234567890abcdefghijklmnop" in context._popped[0].content
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "API key" in finish.result["output"]

    @pytest.mark.asyncio
    async def test_before_pops_all_messages_with_secret(self):
        rail = ModelcallguardRail()
        context = MockModelContext(messages=[
            UserMessage(content="Hello"),
            AssistantMessage(content="Hi there!"),
            UserMessage(content="My secret token=secret123"),
            AssistantMessage(content="Also token=secret123 here"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
            ),
            context=context,
        )

        await rail.before_model_call(ctx)

        assert len(context._messages) == 2
        assert context._messages[0].content == "Hello"
        assert context._messages[1].content == "Hi there!"
        assert len(context._popped) == 2
        finish = ctx.consume_force_finish()
        assert finish is not None

    @pytest.mark.asyncio
    async def test_before_skips_if_no_context(self):
        rail = ModelcallguardRail()
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=[
                    UserMessage(content="My API key is sk-1234567890abcdefghijklmnop"),
                ],
            ),
            context=None,
        )

        await rail.before_model_call(ctx)

        assert ctx.consume_force_finish() is None

    @pytest.mark.asyncio
    async def test_before_allows_clean_user_message(self):
        rail = ModelcallguardRail()
        context = MockModelContext(messages=[
            UserMessage(content="What is the weather today?"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
            ),
            context=context,
        )

        await rail.before_model_call(ctx)

        assert len(context._messages) == 1
        assert len(context._popped) == 0
        assert ctx.consume_force_finish() is None

    @pytest.mark.asyncio
    async def test_before_pops_tool_message_with_secret(self):
        """ToolMessage with secret is popped, UserMessage without secret remains."""
        rail = ModelcallguardRail()
        secret = "sk-1234567890abcdefghijklmnop"
        context = MockModelContext(messages=[
            UserMessage(content="read the config file"),
            ToolMessage(
                content=f"API_KEY={secret}\nSECRET=xyz",
                tool_call_id="call_001",
            ),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
            ),
            context=context,
        )

        await rail.before_model_call(ctx)

        # UserMessage without secret remains, ToolMessage with secret popped
        assert len(context._messages) == 1
        assert context._messages[0].content == "read the config file"
        assert len(context._popped) == 1
        assert secret in context._popped[0].content
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "conversation history" in finish.result["output"]

    @pytest.mark.asyncio
    async def test_before_keeps_clean_messages_when_tool_result_has_secret(self):
        """Only messages with secret are popped, clean messages kept."""
        rail = ModelcallguardRail()
        secret = "sk-abc123def456ghi789jkl012"
        context = MockModelContext(messages=[
            UserMessage(content="Hello"),
            AssistantMessage(content="Hi!"),
            UserMessage(content="read config"),
            ToolMessage(
                content=f"API_KEY={secret}",
                tool_call_id="call_001",
            ),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
            ),
            context=context,
        )

        await rail.before_model_call(ctx)

        # Clean messages remain, ToolMessage with secret popped
        assert len(context._messages) == 3
        assert context._messages[0].content == "Hello"
        assert context._messages[1].content == "Hi!"
        assert context._messages[2].content == "read config"
        assert len(context._popped) == 1
        assert secret in context._popped[0].content
        finish = ctx.consume_force_finish()
        assert finish is not None


class TestModelCallGuardOutputPop:
    """Tests for AFTER_MODEL_CALL pop behavior."""

    @pytest.mark.asyncio
    async def test_after_pops_history_on_response_secret(self):
        rail = ModelcallguardRail()
        context = MockModelContext(messages=[
            UserMessage(content="Hello"),
            UserMessage(content="My key is sk-abc123def456ghi789jkl"),
            AssistantMessage(content="I can help."),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=AssistantMessage(
                    content="I see you mentioned sk-abc123def456ghi789jkl earlier."
                ),
            ),
            context=context,
        )

        await rail.after_model_call(ctx)

        assert len(context._popped) > 0
        popped_contents = [getattr(m, "content", "") for m in context._popped]
        assert any("sk-abc123def456ghi789jkl" in c for c in popped_contents)
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "model response" in finish.result["output"]

    @pytest.mark.asyncio
    async def test_after_pops_all_matching_messages(self):
        rail = ModelcallguardRail()
        context = MockModelContext(messages=[
            UserMessage(content="First message"),
            UserMessage(content="sk-test12345678901234567 here"),
            AssistantMessage(content="sk-test12345678901234567 too"),
            UserMessage(content="Last clean message"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=AssistantMessage(
                    content="Response with sk-test12345678901234567"
                ),
            ),
            context=context,
        )

        await rail.after_model_call(ctx)

        popped_contents = [getattr(m, "content", "") for m in context._popped]
        assert any("sk-test12345678901234567" in c for c in popped_contents)
        finish = ctx.consume_force_finish()
        assert finish is not None

    @pytest.mark.asyncio
    async def test_after_skips_if_response_none(self):
        rail = ModelcallguardRail()
        context = MockModelContext(messages=[
            UserMessage(content="Hello"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=None,
            ),
            context=context,
        )

        await rail.after_model_call(ctx)

        assert len(context._messages) == 1
        assert len(context._popped) == 0
        assert ctx.consume_force_finish() is None

    @pytest.mark.asyncio
    async def test_after_allows_clean_response(self):
        rail = ModelcallguardRail()
        context = MockModelContext(messages=[
            UserMessage(content="What is weather?"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=AssistantMessage(content="The weather is sunny."),
            ),
            context=context,
        )

        await rail.after_model_call(ctx)

        assert len(context._messages) == 1
        assert len(context._popped) == 0
        assert ctx.consume_force_finish() is None

    @pytest.mark.asyncio
    async def test_after_reject_secret_in_tool_arguments(self):
        rail = ModelcallguardRail()
        context = MockModelContext(messages=[
            UserMessage(content="Send email"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_123",
                            type="function",
                            name="send_email",
                            arguments='{"body": "My API_KEY=sk-secret12345678901234"}',
                        )
                    ],
                ),
            ),
            context=context,
        )

        await rail.after_model_call(ctx)

        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "tool arguments" in finish.result["output"]

    @pytest.mark.asyncio
    async def test_after_response_has_secret_then_context_cleaned(self):
        """Verify context is cleaned after model response contains API key."""
        rail = ModelcallguardRail()
        secret = "sk-abc123def456ghi789jkl"
        
        # History contains messages WITHOUT the secret
        context = MockModelContext(messages=[
            UserMessage(content="What is my API key?"),
            AssistantMessage(content="Your API key is shown below."),
            UserMessage(content="Show me again"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=AssistantMessage(
                    content=f"Here is your key: {secret}"
                ),
            ),
            context=context,
        )

        await rail.after_model_call(ctx)

        # Should reject
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "model response" in finish.result["output"]
        
        # History messages don't contain secret, so they remain
        assert len(context._messages) == 3
        
    @pytest.mark.asyncio
    async def test_after_response_has_secret_and_history_has_secret(self):
        """Verify both response secret and history secrets are cleaned."""
        rail = ModelcallguardRail()
        secret = "sk-abc123def456ghi789jkl"
        
        # History contains messages WITH the secret
        context = MockModelContext(messages=[
            UserMessage(content=f"My key is {secret}"),
            AssistantMessage(content="I see."),
            UserMessage(content="Show me again"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=AssistantMessage(
                    content=f"Here is your key: {secret}"
                ),
            ),
            context=context,
        )

        await rail.after_model_call(ctx)

        # Should reject
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "model response" in finish.result["output"]
        
        # Context should be cleaned - no messages with secret
        for msg in context._messages:
            content = getattr(msg, "content", "")
            assert secret not in content, f"Secret still in context: {content}"
        
        # Only first user message has secret, others are clean
        assert len(context._messages) == 2
        assert context._messages[0].content == "I see."
        assert context._messages[1].content == "Show me again"


class TestConversationContinues:
    """Tests for conversation continuing after rejection."""

    @pytest.mark.asyncio
    async def test_second_turn_succeeds_after_first_rejected(self):
        rail = ModelcallguardRail()
        
        context1 = MockModelContext(messages=[
            UserMessage(content="My API key is sk-1234567890abcdefghijklmnop"),
        ])
        ctx1 = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context1.get_messages()),
            context=context1,
        )
        await rail.before_model_call(ctx1)
        assert ctx1.consume_force_finish() is not None
        assert len(context1._messages) == 0
        
        context2 = MockModelContext(messages=[
            UserMessage(content="What is the weather today?"),
        ])
        ctx2 = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context2.get_messages()),
            context=context2,
        )
        await rail.before_model_call(ctx2)
        assert ctx2.consume_force_finish() is None
        assert len(context2._messages) == 1

    @pytest.mark.asyncio
    async def test_full_flow_tool_result_secret_then_second_turn(self):
        """Simulate real flow: Turn 1 tool result has secret, Turn 2 should succeed."""
        rail = ModelcallguardRail()
        secret = "sk-abc123def456ghi789jkl"
        
        # Turn 1 iteration 1: user asks to read file (clean message)
        context_turn1 = MockModelContext(messages=[
            UserMessage(content="read the config file"),
        ])
        ctx_turn1_iter1 = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context_turn1.get_messages()),
            context=context_turn1,
        )
        await rail.before_model_call(ctx_turn1_iter1)
        assert ctx_turn1_iter1.consume_force_finish() is None
        assert len(context_turn1._messages) == 1
        
        # Simulate tool execution adds ToolMessage with secret
        context_turn1._messages.append(
            ToolMessage(content=f"API_KEY={secret}", tool_call_id="call_001")
        )
        
        # Turn 1 iteration 2: BEFORE_MODEL_CALL sees ToolMessage with secret
        ctx_turn1_iter2 = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context_turn1.get_messages()),
            context=context_turn1,
        )
        await rail.before_model_call(ctx_turn1_iter2)
        
        # Should reject and pop ToolMessage
        finish = ctx_turn1_iter2.consume_force_finish()
        assert finish is not None
        assert "conversation history" in finish.result["output"]
        
        # ToolMessage popped, UserMessage remains
        assert len(context_turn1._messages) == 1
        assert context_turn1._messages[0].content == "read the config file"
        
        # Turn 2: new user message "hi"
        context_turn2 = MockModelContext(messages=[
            UserMessage(content="hi"),
        ])
        ctx_turn2 = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context_turn2.get_messages()),
            context=context_turn2,
        )
        await rail.before_model_call(ctx_turn2)
        
        # Should allow - no secret in clean history
        assert ctx_turn2.consume_force_finish() is None
        assert len(context_turn2._messages) == 1


class TestBothEvents:
    """Tests for both events enabled together."""

    @pytest.mark.asyncio
    async def test_both_events_registered(self):
        rail = ModelcallguardRail()
        callbacks = rail.get_callbacks()
        
        from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
        assert AgentCallbackEvent.BEFORE_MODEL_CALL in callbacks
        assert AgentCallbackEvent.AFTER_MODEL_CALL in callbacks
        assert len(callbacks) == 2


class TestSingleEventOnly:
    """Tests for rails with only one event enabled."""

    @pytest.mark.asyncio
    async def test_only_before_model_call(self):
        """Rail with only BEFORE_MODEL_CALL should only check input."""
        rail = ModelcallguardRail()
        rail.supported_events = {AgentCallbackEvent.BEFORE_MODEL_CALL}
        
        secret = "sk-abc123def456ghi789jkl"
        context = MockModelContext(messages=[
            UserMessage(content=f"My key is {secret}"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context.get_messages()),
            context=context,
        )
        
        await rail.before_model_call(ctx)
        
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert len(context._messages) == 0

    @pytest.mark.asyncio
    async def test_only_after_model_call(self):
        """Rail with only AFTER_MODEL_CALL should only check output."""
        rail = ModelcallguardRail()
        rail.supported_events = {AgentCallbackEvent.AFTER_MODEL_CALL}
        
        secret = "sk-abc123def456ghi789jkl"
        context = MockModelContext(messages=[
            UserMessage(content="Hello"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=AssistantMessage(content=f"Key is {secret}"),
            ),
            context=context,
        )
        
        await rail.after_model_call(ctx)
        
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "model response" in finish.result["output"]

    @pytest.mark.asyncio
    async def test_only_before_allows_clean_input(self):
        """Rail with only BEFORE should allow clean input."""
        rail = ModelcallguardRail()
        rail.supported_events = {AgentCallbackEvent.BEFORE_MODEL_CALL}
        
        context = MockModelContext(messages=[
            UserMessage(content="What is the weather?"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context.get_messages()),
            context=context,
        )
        
        await rail.before_model_call(ctx)
        
        assert ctx.consume_force_finish() is None
        assert len(context._messages) == 1

    @pytest.mark.asyncio
    async def test_only_after_allows_clean_output(self):
        """Rail with only AFTER should allow clean output."""
        rail = ModelcallguardRail()
        rail.supported_events = {AgentCallbackEvent.AFTER_MODEL_CALL}
        
        context = MockModelContext(messages=[
            UserMessage(content="Hello"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=AssistantMessage(content="Hi there!"),
            ),
            context=context,
        )
        
        await rail.after_model_call(ctx)
        
        assert ctx.consume_force_finish() is None
        assert len(context._messages) == 1


class TestSanitizeHelper:
    """Tests for _sanitize_matching_messages helper."""

    @pytest.mark.asyncio
    async def test_sanitize_replaces_secret_in_user_message(self):
        """Sanitize should replace secret with [REDACTED]."""
        from examples.security_rail_demo.SensitiveDataSanitize.rail import SensitivedatasanitizeRail
        
        rail = SensitivedatasanitizeRail()
        secret = "sk-abc123def456ghi789jkl"
        
        context = MockModelContext(messages=[
            UserMessage(content=f"My key is {secret}"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context.get_messages()),
            context=context,
        )
        
        await rail.before_model_call(ctx)
        
        assert len(context._messages) == 1
        assert "[REDACTED]" in context._messages[0].content
        assert secret not in context._messages[0].content
        assert ctx.consume_force_finish() is None

    @pytest.mark.asyncio
    async def test_sanitize_preserves_other_content(self):
        """Sanitize should preserve non-secret content."""
        from examples.security_rail_demo.SensitiveDataSanitize.rail import SensitivedatasanitizeRail
        
        rail = SensitivedatasanitizeRail()
        secret = "sk-abc123def456ghi789jkl"
        
        context = MockModelContext(messages=[
            UserMessage(content=f"Hello, my API_KEY={secret}, goodbye"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context.get_messages()),
            context=context,
        )
        
        await rail.before_model_call(ctx)
        
        assert "Hello," in context._messages[0].content
        assert "goodbye" in context._messages[0].content
        assert "[REDACTED]" in context._messages[0].content

    @pytest.mark.asyncio
    async def test_sanitize_response_content(self):
        """Sanitize should replace secret in response."""
        from examples.security_rail_demo.SensitiveDataSanitize.rail import SensitivedatasanitizeRail
        
        rail = SensitivedatasanitizeRail()
        secret = "sk-abc123def456ghi789jkl"
        
        context = MockModelContext(messages=[
            UserMessage(content="Hello"),
        ])
        response = AssistantMessage(content=f"The key is {secret}")
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=response,
            ),
            context=context,
        )
        
        await rail.after_model_call(ctx)
        
        assert "[REDACTED]" in response.content
        assert secret not in response.content
        assert ctx.consume_force_finish() is None


class TestInterruptResumeHelper:
    """Tests for _handle_interrupt_resume helper."""

    def test_auto_confirmed_returns_allow(self):
        """Auto-confirmed key should return SecurityAllow."""
        rail = ModelcallguardRail()
        security_ctx = SecurityCheckContext(
            callback_ctx=AgentCallbackContext(
                agent=object(),
                inputs=ModelCallInputs(messages=[]),
                context=None,
            ),
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
            auto_confirm_config={"test_key": True},
        )
        
        decision = rail._handle_interrupt_resume(security_ctx, "test_key")
        
        assert isinstance(decision, SecurityAllow)

    def test_no_user_input_returns_none(self):
        """No user_input should return None (first call)."""
        rail = ModelcallguardRail()
        security_ctx = SecurityCheckContext(
            callback_ctx=AgentCallbackContext(
                agent=object(),
                inputs=ModelCallInputs(messages=[]),
                context=None,
            ),
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
            user_input=None,
            auto_confirm_config={},
        )
        
        decision = rail._handle_interrupt_resume(security_ctx, "test_key")
        
        assert decision is None

    def test_user_approved_returns_allow(self):
        """User approved should return SecurityAllow."""
        rail = ModelcallguardRail()
        security_ctx = SecurityCheckContext(
            callback_ctx=AgentCallbackContext(
                agent=object(),
                inputs=ModelCallInputs(messages=[]),
                context=None,
            ),
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
            user_input={"approved": True},
            auto_confirm_config={},
        )
        
        decision = rail._handle_interrupt_resume(security_ctx, "test_key")
        
        assert isinstance(decision, SecurityAllow)

    def test_user_rejected_returns_reject(self):
        """User rejected should return SecurityReject."""
        rail = ModelcallguardRail()
        security_ctx = SecurityCheckContext(
            callback_ctx=AgentCallbackContext(
                agent=object(),
                inputs=ModelCallInputs(messages=[]),
                context=None,
            ),
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
            user_input={"approved": False},
            auto_confirm_config={},
        )
        
        decision = rail._handle_interrupt_resume(security_ctx, "test_key")
        
        assert isinstance(decision, SecurityReject)

    def test_user_approved_with_auto_confirm_stores_it(self):
        """User approved with auto_confirm should store it."""
        rail = ModelcallguardRail()
        
        stored_state = {}
        mock_session = type("MockSession", (), {
            "get_state": lambda self, key: stored_state.get(key, {}),
            "update_state": lambda self, state: stored_state.update(state),
        })()
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=[]),
            context=None,
            session=mock_session,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
            user_input={"approved": True, "auto_confirm": True},
            auto_confirm_config={},
        )
        
        decision = rail._handle_interrupt_resume(security_ctx, "test_key")
        
        assert isinstance(decision, SecurityAllow)


class TestToolInterruptReject:
    """Tests for tool interrupt and reject behavior."""

    @pytest.mark.asyncio
    async def test_after_reject_triggers_force_finish(self):
        """AFTER_TOOL_CALL reject should request_force_finish."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        
        rail = ApikeyguardinterruptRail()
        secret = "sk-abc123def456ghi789jkl"
        
        mock_session = type("MockSession", (), {
            "get_state": lambda self, key: {},
            "update_state": lambda self, state: None,
        })()
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_result=f"API_KEY={secret}",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
            session=mock_session,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
            user_input={"approved": False},
            auto_confirm_config={},
        )
        
        decision = await rail.run_security_check(security_ctx)
        await rail.apply_security_decision(security_ctx, decision)
        
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "Rejected" in finish.result["output"]

    @pytest.mark.asyncio
    async def test_before_reject_skips_tool_no_force_finish(self):
        """BEFORE_TOOL_CALL reject should skip tool without force_finish."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        
        rail = ApikeyguardinterruptRail()
        secret = "sk-abc123def456ghi789jkl"
        
        mock_session = type("MockSession", (), {
            "get_state": lambda self, key: {},
            "update_state": lambda self, state: None,
        })()
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_args=f"{{\"path\": \"file_with_{secret}\"}}",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
            session=mock_session,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.BEFORE_TOOL_CALL,
            user_input={"approved": False},
            auto_confirm_config={},
        )
        
        decision = await rail.run_security_check(security_ctx)
        await rail.apply_security_decision(security_ctx, decision)
        
        finish = ctx.consume_force_finish()
        assert finish is None
        assert ctx.extra.get("_skip_tool") is True
        assert "Tool execution skipped" in ctx.inputs.tool_msg.content

    @pytest.mark.asyncio
    async def test_before_interrupt_on_args_secret(self):
        """BEFORE_TOOL_CALL should interrupt if tool_args contains secret."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        from openjiuwen.harness.rails.security.base_security_rail import SecurityInterrupt
        
        rail = ApikeyguardinterruptRail()
        secret = "sk-abc123def456ghi789jkl"
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_args=f"{{\"path\": \"file_with_{secret}\"}}",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.BEFORE_TOOL_CALL,
            user_input=None,
            auto_confirm_config={},
        )
        
        decision = await rail.run_security_check(security_ctx)
        
        assert isinstance(decision, SecurityInterrupt)
        assert "tool arguments" in decision.request.message.lower()

    @pytest.mark.asyncio
    async def test_after_interrupt_on_result_secret(self):
        """AFTER_TOOL_CALL should interrupt if tool_result contains secret."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        from openjiuwen.harness.rails.security.base_security_rail import SecurityInterrupt
        
        rail = ApikeyguardinterruptRail()
        secret = "sk-abc123def456ghi789jkl"
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_result=f"API_KEY={secret}",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
            user_input=None,
            auto_confirm_config={},
        )
        
        decision = await rail.run_security_check(security_ctx)
        
        assert isinstance(decision, SecurityInterrupt)
        assert "tool result" in decision.request.message.lower()

    @pytest.mark.asyncio
    async def test_before_approve_executes_tool(self):
        """BEFORE_TOOL_CALL approve should allow tool execution."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        
        rail = ApikeyguardinterruptRail()
        secret = "sk-abc123def456ghi789jkl"
        
        mock_session = type("MockSession", (), {
            "get_state": lambda self, key: {},
            "update_state": lambda self, state: None,
        })()
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_args=f"{{\"path\": \"file_with_{secret}\"}}",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
            session=mock_session,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.BEFORE_TOOL_CALL,
            user_input={"approved": True},
            auto_confirm_config={},
        )
        
        decision = await rail.run_security_check(security_ctx)
        await rail.apply_security_decision(security_ctx, decision)
        
        assert ctx.consume_force_finish() is None
        assert ctx.extra.get("_skip_tool") is None

    @pytest.mark.asyncio
    async def test_both_events_registered(self):
        """ApiKeyGuardInterrupt should register both BEFORE and AFTER events."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        
        rail = ApikeyguardinterruptRail()
        callbacks = rail.get_callbacks()
        
        assert AgentCallbackEvent.BEFORE_TOOL_CALL in callbacks
        assert AgentCallbackEvent.AFTER_TOOL_CALL in callbacks
        assert len(callbacks) == 2


class TestApiKeyGuardInterruptAutoConfirm:
    """Tests for auto_confirm key format."""

    @pytest.mark.asyncio
    async def test_before_auto_confirm_key_format(self):
        """Auto_confirm key for BEFORE should be api_key_guard:{tool_name}:before."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        
        rail = ApikeyguardinterruptRail()
        
        stored_state = {"api_key_guard:read_file:before": True}
        mock_session = type("MockSession", (), {
            "get_state": lambda self, key: stored_state.get(key, {}),
            "update_state": lambda self, state: stored_state.update(state),
        })()
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_args='{"path": "secret_file"}',
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
            session=mock_session,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.BEFORE_TOOL_CALL,
            auto_confirm_config={"api_key_guard:read_file:before": True},
        )
        
        decision = await rail.run_security_check(security_ctx)
        
        assert isinstance(decision, SecurityAllow)

    @pytest.mark.asyncio
    async def test_after_auto_confirm_key_format(self):
        """Auto_confirm key for AFTER should be api_key_guard:{tool_name}:after."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        
        rail = ApikeyguardinterruptRail()
        
        stored_state = {"api_key_guard:read_file:after": True}
        mock_session = type("MockSession", (), {
            "get_state": lambda self, key: stored_state.get(key, {}),
            "update_state": lambda self, state: stored_state.update(state),
        })()
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_result="API_KEY=sk-secret123",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
            session=mock_session,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
            auto_confirm_config={"api_key_guard:read_file:after": True},
        )
        
        decision = await rail.run_security_check(security_ctx)
        
        assert isinstance(decision, SecurityAllow)


class TestUnderscoreKeyFormats:
    """Tests for API keys with underscore/hyphen characters."""

    @pytest.mark.asyncio
    async def test_model_before_pops_underscore_key(self):
        """Model BEFORE should pop messages with sk-xxx_yyy format."""
        rail = ModelcallguardRail()
        secret = "sk-A7nhsRpv_vcK55IGQe6hSQ"
        
        context = MockModelContext(messages=[
            UserMessage(content=f"My key is {secret}"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context.get_messages()),
            context=context,
        )
        
        await rail.before_model_call(ctx)
        
        assert len(context._messages) == 0
        assert secret in context._popped[0].content
        finish = ctx.consume_force_finish()
        assert finish is not None

    @pytest.mark.asyncio
    async def test_model_after_rejects_underscore_key_in_response(self):
        """Model AFTER should reject response with underscore key."""
        rail = ModelcallguardRail()
        secret = "sk-proj_abc123def456ghi789"
        
        context = MockModelContext(messages=[
            UserMessage(content="What is my key?"),
        ])
        response = AssistantMessage(content=f"Your key is {secret}")
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(
                messages=context.get_messages(),
                response=response,
            ),
            context=context,
        )
        
        await rail.after_model_call(ctx)
        
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "model response" in finish.result["output"]

    @pytest.mark.asyncio
    async def test_tool_before_interrupt_underscore_key_in_args(self):
        """Tool BEFORE should interrupt on args with underscore key."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        from openjiuwen.harness.rails.security.base_security_rail import SecurityInterrupt
        
        rail = ApikeyguardinterruptRail()
        secret = "sk-A7nhsRpv_vcK55IGQe6hSQ"
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="glob",
                tool_args=f'{{"pattern": "*{secret}*"}}',
                tool_call=ToolCall(id="call_001", type="function", name="glob", arguments="{}"),
            ),
            context=None,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.BEFORE_TOOL_CALL,
            user_input=None,
            auto_confirm_config={},
        )
        
        decision = await rail.run_security_check(security_ctx)
        
        assert isinstance(decision, SecurityInterrupt)

    @pytest.mark.asyncio
    async def test_tool_after_interrupt_underscore_key_in_result(self):
        """Tool AFTER should interrupt on result with underscore key."""
        from examples.security_rail_demo.ApiKeyGuardInterrupt.rail import ApikeyguardinterruptRail
        from openjiuwen.harness.rails.security.base_security_rail import SecurityInterrupt
        
        rail = ApikeyguardinterruptRail()
        secret = "sk-A7nhsRpv_vcK55IGQe6hSQ"
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_result=f"Found key: {secret}",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
            user_input=None,
            auto_confirm_config={},
        )
        
        decision = await rail.run_security_check(security_ctx)
        
        assert isinstance(decision, SecurityInterrupt)

    @pytest.mark.asyncio
    async def test_sanitize_underscore_key(self):
        """Sanitize should replace underscore key with [REDACTED]."""
        from examples.security_rail_demo.SensitiveDataSanitize.rail import SensitivedatasanitizeRail
        
        rail = SensitivedatasanitizeRail()
        secret = "sk-proj_abc123def456ghi789"
        
        context = MockModelContext(messages=[
            UserMessage(content=f"My key is {secret}"),
        ])
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(messages=context.get_messages()),
            context=context,
        )
        
        await rail.before_model_call(ctx)
        
        assert "[REDACTED]" in context._messages[0].content
        assert secret not in context._messages[0].content
        assert ctx.consume_force_finish() is None


__all__ = [
    "TestModelCallGuardInputPop",
    "TestModelCallGuardOutputPop",
    "TestConversationContinues",
    "TestBothEvents",
    "TestSingleEventOnly",
    "TestSanitizeHelper",
    "TestInterruptResumeHelper",
    "TestToolInterruptReject",
    "TestApiKeyGuardInterruptAutoConfirm",
    "TestUnderscoreKeyFormats",
    "TestToolRejectExample",
    "TestSecurityAlert",
    "TestApiKeyGuardAlert",
]


class TestToolRejectExample:
    """Tests for ToolRejectExample demonstrating BEFORE/AFTER reject behavior."""

    @pytest.mark.asyncio
    async def test_before_reject_skips_tool_continues(self):
        """BEFORE reject should skip tool without force_finish."""
        from examples.security_rail_demo.ToolRejectExample.rail import ToolrejectexampleRail
        
        rail = ToolrejectexampleRail()
        secret = "sk-A7nhsRpv_vcK55IGQe6hSQ"
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="glob",
                tool_args=f'{{"pattern": "*{secret}*"}}',
                tool_call=ToolCall(id="call_001", type="function", name="glob", arguments="{}"),
            ),
            context=None,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.BEFORE_TOOL_CALL,
        )
        
        decision = await rail.run_security_check(security_ctx)
        await rail.apply_security_decision(security_ctx, decision)
        
        # BEFORE reject: skip_tool, NO force_finish
        assert ctx.consume_force_finish() is None
        assert ctx.extra.get("_skip_tool") is True
        assert "Tool skipped" in ctx.inputs.tool_msg.content

    @pytest.mark.asyncio
    async def test_after_reject_force_finish(self):
        """AFTER reject should terminate agent with force_finish."""
        from examples.security_rail_demo.ToolRejectExample.rail import ToolrejectexampleRail
        
        rail = ToolrejectexampleRail()
        secret = "sk-A7nhsRpv_vcK55IGQe6hSQ"
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_result=f"API_KEY={secret}",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )
        
        decision = await rail.run_security_check(security_ctx)
        await rail.apply_security_decision(security_ctx, decision)
        
        # AFTER reject: force_finish, agent terminates
        finish = ctx.consume_force_finish()
        assert finish is not None
        assert "Agent terminated" in finish.result["output"]

    @pytest.mark.asyncio
    async def test_before_allows_clean_args(self):
        """BEFORE should allow tools with clean arguments."""
        from examples.security_rail_demo.ToolRejectExample.rail import ToolrejectexampleRail
        
        rail = ToolrejectexampleRail()
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="glob",
                tool_args='{"pattern": "*.txt"}',
                tool_call=ToolCall(id="call_001", type="function", name="glob", arguments="{}"),
            ),
            context=None,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.BEFORE_TOOL_CALL,
        )
        
        decision = await rail.run_security_check(security_ctx)
        await rail.apply_security_decision(security_ctx, decision)
        
        assert isinstance(decision, SecurityAllow)
        assert ctx.consume_force_finish() is None
        assert ctx.extra.get("_skip_tool") is None

    @pytest.mark.asyncio
    async def test_after_allows_clean_result(self):
        """AFTER should allow tools with clean results."""
        from examples.security_rail_demo.ToolRejectExample.rail import ToolrejectexampleRail
        
        rail = ToolrejectexampleRail()
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_result="File content without secrets",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )
        
        decision = await rail.run_security_check(security_ctx)
        await rail.apply_security_decision(security_ctx, decision)
        
        assert isinstance(decision, SecurityAllow)
        assert ctx.consume_force_finish() is None

    @pytest.mark.asyncio
    async def test_non_whitelist_tool_allowed(self):
        """Non-whitelist tools should always be allowed."""
        from examples.security_rail_demo.ToolRejectExample.rail import ToolrejectexampleRail
        
        rail = ToolrejectexampleRail()
        secret = "sk-abc123def456ghi789jkl"
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="calculate",  # Not in whitelist
                tool_args=f'{{"data": "{secret}"}}',
                tool_call=ToolCall(id="call_001", type="function", name="calculate", arguments="{}"),
            ),
            context=None,
        )
        
        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.BEFORE_TOOL_CALL,
        )
        
        decision = await rail.run_security_check(security_ctx)
        
        assert isinstance(decision, SecurityAllow)

    @pytest.mark.asyncio
    async def test_both_events_registered(self):
        """ToolRejectExample should register both events."""
        from examples.security_rail_demo.ToolRejectExample.rail import ToolrejectexampleRail
        
        rail = ToolrejectexampleRail()
        callbacks = rail.get_callbacks()
        
        assert AgentCallbackEvent.BEFORE_TOOL_CALL in callbacks
        assert AgentCallbackEvent.AFTER_TOOL_CALL in callbacks
        assert len(callbacks) == 2


class TestSecurityAlert:
    """Tests for SecurityAlert decision."""

    def test_security_alert_level_enum_values(self):
        """SecurityAlertLevel should have INFO, WARNING, ERROR, CRITICAL."""
        assert SecurityAlertLevel.INFO.value == "info"
        assert SecurityAlertLevel.WARNING.value == "warning"
        assert SecurityAlertLevel.ERROR.value == "error"
        assert SecurityAlertLevel.CRITICAL.value == "critical"

    def test_security_alert_dataclass_fields(self):
        """SecurityAlert should have message, level, alert_type, display_mode."""
        rail = ModelcallguardRail()
        alert = rail.alert(
            message="Test alert",
            level=SecurityAlertLevel.WARNING,
            alert_type="test",
            display_mode="popup",
        )

        assert alert.message == "Test alert"
        assert alert.level == SecurityAlertLevel.WARNING
        assert alert.alert_type == "test"
        assert alert.display_mode == "popup"

    def test_security_alert_default_values(self):
        """SecurityAlert should have default values."""
        rail = ModelcallguardRail()
        alert = rail.alert(message="Default test")

        assert alert.message == "Default test"
        assert alert.level == SecurityAlertLevel.WARNING
        assert alert.alert_type == "security"
        assert alert.display_mode == "popup"

    def test_security_alert_display_modes(self):
        """SecurityAlert should support popup, history, inline display modes."""
        rail = ModelcallguardRail()

        alert_popup = rail.alert(message="popup", display_mode="popup")
        assert alert_popup.display_mode == "popup"

        alert_history = rail.alert(message="history", display_mode="history")
        assert alert_history.display_mode == "history"

        alert_inline = rail.alert(message="inline", display_mode="inline")
        assert alert_inline.display_mode == "inline"

    @pytest.mark.asyncio
    async def test_apply_alert_streams_to_session(self):
        """_apply_alert should stream OutputSchema type=message to session."""
        from openjiuwen.core.session.stream import OutputSchema
        from unittest.mock import AsyncMock, MagicMock

        rail = ModelcallguardRail()
        alert = rail.alert(
            message="Stream test",
            level=SecurityAlertLevel.ERROR,
            display_mode="popup",
        )

        mock_session = MagicMock()
        mock_session.write_stream = AsyncMock()

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read",
                tool_result="test",
                tool_call=ToolCall(id="call_001", type="function", name="read", arguments="{}"),
            ),
            context=None,
            session=mock_session,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        await rail._apply_alert(security_ctx, alert)

        mock_session.write_stream.assert_called_once()
        stream_data = mock_session.write_stream.call_args[0][0]
        assert isinstance(stream_data, OutputSchema)
        assert stream_data.type == "message"
        assert stream_data.payload["role"] == "system"
        assert "[ERROR]" in stream_data.payload["content"]
        assert "Stream test" in stream_data.payload["content"]
        assert stream_data.payload["metadata"]["is_security_alert"] is True
        assert stream_data.payload["metadata"]["level"] == "error"
        assert stream_data.payload["metadata"]["display_mode"] == "popup"

    @pytest.mark.asyncio
    async def test_apply_alert_with_history_mode(self):
        """_apply_alert with history mode should still use type=message (frontend compatibility)."""
        from openjiuwen.core.session.stream import OutputSchema
        from unittest.mock import AsyncMock, MagicMock

        rail = ModelcallguardRail()
        alert = rail.alert(
            message="History test",
            display_mode="history",
        )

        mock_session = MagicMock()
        mock_session.write_stream = AsyncMock()

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read",
                tool_result="test",
                tool_call=ToolCall(id="call_001", type="function", name="read", arguments="{}"),
            ),
            context=None,
            session=mock_session,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        await rail._apply_alert(security_ctx, alert)

        stream_data = mock_session.write_stream.call_args[0][0]
        assert stream_data.type == "message"
        assert stream_data.payload["metadata"]["display_mode"] == "history"

    @pytest.mark.asyncio
    async def test_apply_alert_with_inline_mode(self):
        """_apply_alert with inline mode should still use type=message (frontend compatibility)."""
        from openjiuwen.core.session.stream import OutputSchema
        from unittest.mock import AsyncMock, MagicMock

        rail = ModelcallguardRail()
        alert = rail.alert(
            message="Inline test",
            display_mode="inline",
        )

        mock_session = MagicMock()
        mock_session.write_stream = AsyncMock()

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read",
                tool_result="test",
                tool_call=ToolCall(id="call_001", type="function", name="read", arguments="{}"),
            ),
            context=None,
            session=mock_session,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        await rail._apply_alert(security_ctx, alert)

        stream_data = mock_session.write_stream.call_args[0][0]
        assert stream_data.type == "message"
        assert stream_data.payload["metadata"]["display_mode"] == "inline"

    @pytest.mark.asyncio
    async def test_apply_alert_without_session(self):
        """_apply_alert should work without session (no stream, just log)."""
        rail = ModelcallguardRail()
        alert = rail.alert(message="No session test")

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read",
                tool_result="test",
                tool_call=ToolCall(id="call_001", type="function", name="read", arguments="{}"),
            ),
            context=None,
            session=None,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        await rail._apply_alert(security_ctx, alert)


class TestApiKeyGuardAlert:
    """Tests for ApiKeyGuardAlert example rail."""

    @pytest.mark.asyncio
    async def test_alert_on_api_key_in_result(self):
        """ApiKeyGuardAlert should return SecurityAlert when API key detected."""
        from examples.security_rail_demo.ApiKeyGuardAlert.rail import ApikeyguardalertRail

        rail = ApikeyguardalertRail(display_mode="popup")
        secret = "sk-abc123def456ghi789jkl012mno"

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_result=f"API_KEY={secret}",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        decision = await rail.run_security_check(security_ctx)

        assert isinstance(decision, SecurityAlert)
        assert "API key" in decision.message
        assert decision.display_mode == "popup"

    @pytest.mark.asyncio
    async def test_allow_clean_result(self):
        """ApiKeyGuardAlert should allow clean tool results."""
        from examples.security_rail_demo.ApiKeyGuardAlert.rail import ApikeyguardalertRail

        rail = ApikeyguardalertRail()

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_result="Normal file content",
                tool_call=ToolCall(id="call_001", type="function", name="read_file", arguments="{}"),
            ),
            context=None,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        decision = await rail.run_security_check(security_ctx)

        assert isinstance(decision, SecurityAllow)

    @pytest.mark.asyncio
    async def test_allow_non_whitelist_tool(self):
        """ApiKeyGuardAlert should allow tools not in whitelist."""
        from examples.security_rail_demo.ApiKeyGuardAlert.rail import ApikeyguardalertRail

        rail = ApikeyguardalertRail()
        secret = "sk-abc123"

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="write_file",  # Not in FILE_READING_TOOLS
                tool_result=f"API_KEY={secret}",
                tool_call=ToolCall(id="call_001", type="function", name="write_file", arguments="{}"),
            ),
            context=None,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        decision = await rail.run_security_check(security_ctx)

        assert isinstance(decision, SecurityAllow)

    @pytest.mark.asyncio
    async def test_custom_display_mode(self):
        """ApiKeyGuardAlert should use custom display_mode."""
        from examples.security_rail_demo.ApiKeyGuardAlert.rail import ApikeyguardalertRail

        rail = ApikeyguardalertRail(display_mode="history")
        secret = "sk-abc123def456ghi789jkl"

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="bash",
                tool_result=f"Found: {secret}",
                tool_call=ToolCall(id="call_001", type="function", name="bash", arguments="{}"),
            ),
            context=None,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        decision = await rail.run_security_check(security_ctx)

        assert isinstance(decision, SecurityAlert)
        assert decision.display_mode == "history"

    @pytest.mark.asyncio
    async def test_custom_alert_level(self):
        """ApiKeyGuardAlert should use custom alert_level."""
        from examples.security_rail_demo.ApiKeyGuardAlert.rail import ApikeyguardalertRail

        rail = ApikeyguardalertRail(alert_level=SecurityAlertLevel.CRITICAL)
        secret = "sk-abc123def456ghi789jkl012"

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="read",
                tool_result=f"Key: {secret}",
                tool_call=ToolCall(id="call_001", type="function", name="read", arguments="{}"),
            ),
            context=None,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        decision = await rail.run_security_check(security_ctx)

        assert isinstance(decision, SecurityAlert)
        assert decision.level == SecurityAlertLevel.CRITICAL

    @pytest.mark.asyncio
    async def test_underscore_key_detected(self):
        """ApiKeyGuardAlert should detect underscore keys."""
        from examples.security_rail_demo.ApiKeyGuardAlert.rail import ApikeyguardalertRail

        rail = ApikeyguardalertRail()
        secret = "sk-A7nhsRpv_vcK55IGQe6hSQ"

        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ToolCallInputs(
                tool_name="grep",
                tool_result=f"Found key: {secret}",
                tool_call=ToolCall(id="call_001", type="function", name="grep", arguments="{}"),
            ),
            context=None,
        )

        security_ctx = SecurityCheckContext(
            callback_ctx=ctx,
            event=AgentCallbackEvent.AFTER_TOOL_CALL,
        )

        decision = await rail.run_security_check(security_ctx)

        assert isinstance(decision, SecurityAlert)

    def test_both_events_registered(self):
        """ApiKeyGuardAlert should register AFTER_TOOL_CALL event."""
        from examples.security_rail_demo.ApiKeyGuardAlert.rail import ApikeyguardalertRail

        rail = ApikeyguardalertRail()
        callbacks = rail.get_callbacks()

        assert AgentCallbackEvent.AFTER_TOOL_CALL in callbacks
        assert len(callbacks) == 1