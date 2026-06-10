# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.core.runner import set_request_id, reset_request_id
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    ModelCallInputs,
)
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.rails import (
    BaseSecurityRail,
    SecurityCheckContext,
    SecurityReject,
)


class _RejectModelRail(BaseSecurityRail):
    supported_events = {AgentCallbackEvent.BEFORE_MODEL_CALL}

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        return self.reject("blocked by test rail")

    async def apply_security_decision(self, security_ctx: SecurityCheckContext, decision):
        if isinstance(decision, SecurityReject):
            security_ctx.callback_ctx.request_force_finish(self._build_force_finish_result(decision))
            return
        await super().apply_security_decision(security_ctx, decision)


class _TestPromptRail(BaseSecurityRail):
    supported_events = {AgentCallbackEvent.BEFORE_MODEL_CALL}

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        return self.allow()


def test_base_security_rail_registers_only_supported_events():
    rail = _TestPromptRail()
    callbacks = rail.get_callbacks()

    assert set(callbacks) == {AgentCallbackEvent.BEFORE_MODEL_CALL}


@pytest.mark.asyncio
async def test_model_reject_requests_force_finish():
    rail = _RejectModelRail()
    ctx = AgentCallbackContext(
        agent=object(),
        inputs=ModelCallInputs(),
    )

    await rail.before_model_call(ctx)

    finish = ctx.consume_force_finish()
    assert finish is not None
    assert finish.result == {
        "output": "blocked by test rail",
        "result_type": "error",
    }


@pytest.mark.asyncio
async def test_test_prompt_rail_allows_model_call():
    rail = _TestPromptRail()
    ctx = AgentCallbackContext(
        agent=object(),
        inputs=ModelCallInputs(),
    )

    await rail.before_model_call(ctx)

    assert ctx.consume_force_finish() is None


class _ModelInterruptRail(BaseSecurityRail):
    supported_events = {AgentCallbackEvent.BEFORE_MODEL_CALL}

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        return self.interrupt(
            InterruptRequest(message="Should be auto-rejected"),
            subject_id="test_interrupt",
        )

    async def apply_security_decision(self, security_ctx: SecurityCheckContext, decision):
        if isinstance(decision, SecurityReject):
            security_ctx.callback_ctx.request_force_finish(self._build_force_finish_result(decision))
            return
        await super().apply_security_decision(security_ctx, decision)


@pytest.mark.asyncio
async def test_security_interrupt_on_model_event_auto_rejected():
    rail = _ModelInterruptRail()
    ctx = AgentCallbackContext(
        agent=object(),
        inputs=ModelCallInputs(),
    )

    await rail.before_model_call(ctx)

    finish = ctx.consume_force_finish()
    assert finish is not None
    assert "Should be auto-rejected" in finish.result.get("output", "")


class _RetractEventTestRail(BaseSecurityRail):
    supported_events = {AgentCallbackEvent.AFTER_MODEL_CALL}

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        return self.reject("test retract")


@pytest.mark.asyncio
async def test_send_retract_event_request_id_from_context_variable():
    rail = _RetractEventTestRail()
    
    token = set_request_id("req-789")
    try:
        session = MagicMock()
        session.session_id = "test-session-123"
        session.write_stream = AsyncMock()
        
        context = MagicMock()
        last_msg = MagicMock()
        last_msg.metadata = {
            "context_message_id": "msg-456",
        }
        context.get_messages = MagicMock(return_value=[last_msg])
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(),
        )
        ctx.session = session
        ctx.context = context
        ctx.extra = {}
        
        await rail._send_retract_event(ctx, "test message")
        
        session.write_stream.assert_called_once()
        call_args = session.write_stream.call_args
        output_schema = call_args[0][0]
        
        assert output_schema.type == "chat.retract"
        assert output_schema.payload["session_id"] == "test-session-123"
        assert output_schema.payload["request_id"] == "req-789"
        assert output_schema.payload["message"] == "test message"
        assert output_schema.payload["message_id"] == "msg-456"
    finally:
        reset_request_id(token)


@pytest.mark.asyncio
async def test_send_retract_event_request_id_empty_when_not_set():
    rail = _RetractEventTestRail()
    
    token = set_request_id("")
    try:
        session = MagicMock()
        session.session_id = "test-session-123"
        session.write_stream = AsyncMock()
        
        context = MagicMock()
        last_msg = MagicMock()
        last_msg.metadata = {
            "context_message_id": "msg-456",
        }
        context.get_messages = MagicMock(return_value=[last_msg])
        
        ctx = AgentCallbackContext(
            agent=object(),
            inputs=ModelCallInputs(),
        )
        ctx.session = session
        ctx.context = context
        ctx.extra = {}
        
        await rail._send_retract_event(ctx, "test message")
        
        session.write_stream.assert_called_once()
        call_args = session.write_stream.call_args
        output_schema = call_args[0][0]
        
        assert output_schema.type == "chat.retract"
        assert output_schema.payload["request_id"] == ""
        assert output_schema.payload["message_id"] == "msg-456"
    finally:
        reset_request_id(token)