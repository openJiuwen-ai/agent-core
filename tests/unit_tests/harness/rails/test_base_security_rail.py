# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    InvokeInputs,
    ModelCallInputs,
)
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


class _BeforeInvokeRail(BaseSecurityRail):
    supported_events = {AgentCallbackEvent.BEFORE_INVOKE}

    def __init__(self):
        super().__init__()
        self.events = []

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        self.events.append(security_ctx.event)
        return self.allow()


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
async def test_base_security_rail_supports_non_model_tool_events():
    rail = _BeforeInvokeRail()
    callbacks = rail.get_callbacks()
    ctx = AgentCallbackContext(
        agent=object(),
        inputs=InvokeInputs(query="hello"),
    )

    assert set(callbacks) == {AgentCallbackEvent.BEFORE_INVOKE}
    await callbacks[AgentCallbackEvent.BEFORE_INVOKE](ctx)

    assert rail.events == [AgentCallbackEvent.BEFORE_INVOKE]


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