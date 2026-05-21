# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    ModelCallInputs,
)
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.rails import (
    BaseSecurityRail,
    SafetyPromptRail,
    SecurityCheckContext,
    SecurityRail,
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


def test_security_rails_export_expected_names():
    assert issubclass(SafetyPromptRail, BaseSecurityRail)
    assert SecurityRail is SafetyPromptRail
    assert SafetyPromptRail.__name__ == "SafetyPromptRail"


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