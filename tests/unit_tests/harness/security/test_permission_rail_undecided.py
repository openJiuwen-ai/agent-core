# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``PermissionInterruptRail`` must reject a call it could not decide.

A rail stops a tool call only by raising ``AbortError`` or by setting
``ctx.extra["_skip_tool"]``. ``AsyncCallbackFramework.trigger`` logs and
swallows every other exception and then lets the call proceed, so an exception
escaping ``before_tool_call`` means the gate reached no decision and the call
runs as if approved. These tests pin that every escape becomes a rejection
instead, and that a genuine ASK interrupt still passes through untouched.
"""

from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.factory import build_permission_interrupt_rail
from openjiuwen.harness.security.host import ToolPermissionHost
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


ASK_POLICY = {"enabled": True, "tools": {"write_file": "ask"}}
DENY_POLICY = {"enabled": True, "tools": {"write_file": "deny"}}
ALLOW_POLICY = {"enabled": True, "tools": {"write_file": "allow"}}

BOOM = "orbital mind control laser offline"


def _tool_call() -> ToolCall:
    return ToolCall(
        id="call_zaphod_1",
        type="function",
        name="write_file",
        arguments='{"file_path": "beeblebrox.md", "content": "mostly harmless"}',
    )


def _ctx(tool_call: ToolCall | None = None) -> AgentCallbackContext:
    call = _tool_call() if tool_call is None else tool_call
    return AgentCallbackContext(
        agent=object(),
        inputs=ToolCallInputs(
            tool_call=call,
            tool_name=call.name,
            tool_args=call.arguments,
        ),
    )


def _rail(
    policy: dict[str, Any] = ASK_POLICY,
    host: ToolPermissionHost | None = None,
) -> PermissionInterruptRail:
    # Copied, because the rail keeps the dict it is given and the engine may
    # rewrite it; the module-level policies must not leak between tests.
    return PermissionInterruptRail(
        config=deepcopy(policy), host=host or ToolPermissionHost()
    )


def _rejected(ctx: AgentCallbackContext) -> bool:
    """Whether the rail stopped the call by the reject mechanism."""
    return ctx.extra.get("_skip_tool") is True


# --------------------------------------------------------------------------
# Every route by which an exception can escape the gate becomes a rejection.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_host_confirmation_raising_rejects_the_call() -> None:
    """A host approval channel that raises must deny, as returning ``None`` does.

    ``RequestPermissionConfirmationHook`` already documents ``None`` as "hosted
    confirmation failed, the call will be rejected". An exception from the same
    hook is the same failure and must reach the same outcome.
    """

    async def unavailable(_request: Any) -> Any:
        raise RuntimeError(BOOM)

    rail = _rail(host=ToolPermissionHost(request_permission_confirmation=unavailable))
    ctx = _ctx()

    await rail.before_tool_call(ctx)

    assert _rejected(ctx)
    assert "[PERMISSION_DENIED]" in str(ctx.inputs.tool_result)


@pytest.mark.asyncio
async def test_permission_check_raising_rejects_the_call() -> None:
    """``check_permission`` logs and reraises, so its failure reaches the framework."""
    rail = _rail()

    async def unevaluable(**_kwargs: Any) -> Any:
        raise ValueError(BOOM)

    rail._engine.check_permission = unevaluable
    ctx = _ctx()

    await rail.before_tool_call(ctx)

    assert _rejected(ctx)
    assert "[PERMISSION_DENIED]" in str(ctx.inputs.tool_result)


@pytest.mark.asyncio
async def test_permission_check_raising_under_a_deny_rule_still_rejects() -> None:
    """An unevaluated policy must not become weaker than the policy it failed to read.

    The rule here is ``deny``. Because the evaluation failed, the rail cannot
    know that, so the outcome must be the strictest one the policy could have
    carried rather than a question the user can answer with "yes".
    """
    rail = _rail(policy=DENY_POLICY)

    async def unevaluable(**_kwargs: Any) -> Any:
        raise ValueError(BOOM)

    rail._engine.check_permission = unevaluable
    ctx = _ctx()

    await rail.before_tool_call(ctx)

    assert _rejected(ctx)
    assert "_interrupt_decision" not in ctx.extra


@pytest.mark.asyncio
async def test_auto_confirm_key_raising_rejects_the_call() -> None:
    """A failure above the first-check branch escapes on the resume pass too.

    ``_get_auto_confirm_key`` runs before ``resolve_interrupt`` splits on
    ``user_input``, so it is reached whether or not the user has already
    answered. Turning this into an interrupt would ask a question whose answer
    leads straight back to the same failure.
    """
    rail = _rail()

    def unusable(_tool_call: Any) -> str:
        raise ValueError(BOOM)

    rail._get_auto_confirm_key = unusable
    ctx = _ctx()

    await rail.before_tool_call(ctx)

    assert _rejected(ctx)


@pytest.mark.asyncio
async def test_permissions_snapshot_of_the_wrong_shape_rejects_the_call() -> None:
    """The snapshot call is guarded; applying what it returns is not.

    ``get_permissions_snapshot`` is wrapped in ``try/except``, but the
    ``update_config`` that consumes its result is outside that guard, so a host
    returning a dict the engine cannot load still escapes.
    """
    rail = _rail()

    def hostile_snapshot() -> dict[str, Any]:
        return {"enabled": True, "tools": {"write_file": "ask"}}

    rail._host = ToolPermissionHost(get_permissions_snapshot=hostile_snapshot)

    def unusable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError(BOOM)

    rail._engine.update_config = unusable
    ctx = _ctx()

    await rail.before_tool_call(ctx)

    assert _rejected(ctx)


@pytest.mark.asyncio
async def test_a_context_too_broken_to_reject_aborts_instead() -> None:
    """When even rejecting is impossible, the chain is aborted rather than continued.

    A context whose ``inputs`` cannot be read fails before a tool call can be
    identified, so there is nothing to attach a rejection to. Aborting is then
    the only remaining way to keep the call from running, and ``AbortError`` is
    the one exception the framework honours.
    """

    class Unreadable:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError(BOOM)

    rail = _rail()
    ctx = AgentCallbackContext(agent=object(), inputs=Unreadable())

    with pytest.raises(AbortError):
        await rail.before_tool_call(ctx)


@pytest.mark.asyncio
async def test_rejection_names_the_failure_type_but_not_its_message() -> None:
    """The tool result is fed back to the model, so the detail stays in the log."""

    async def unavailable(_request: Any) -> Any:
        raise RuntimeError(BOOM)

    rail = _rail(host=ToolPermissionHost(request_permission_confirmation=unavailable))
    ctx = _ctx()

    await rail.before_tool_call(ctx)

    result = str(ctx.inputs.tool_result)
    assert "RuntimeError" in result
    assert BOOM not in result


# --------------------------------------------------------------------------
# Guards: what must not change.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ask_still_raises_the_interrupt() -> None:
    """Guard. ``AbortError`` is how an ASK becomes a pause and must pass through.

    Catching it alongside every other exception would convert every question
    the rail exists to raise into a silent rejection, which would break the
    feature while closing the hole. Passes before and after the fix.
    """
    rail = _rail()
    ctx = _ctx()

    with pytest.raises(AbortError) as excinfo:
        await rail.before_tool_call(ctx)

    assert isinstance(excinfo.value.cause, ToolInterruptException)
    assert not _rejected(ctx)


@pytest.mark.asyncio
async def test_a_deny_rule_still_rejects_normally() -> None:
    """Guard. A decided DENY is unaffected. Passes before and after the fix."""
    rail = _rail(policy=DENY_POLICY)
    ctx = _ctx()

    await rail.before_tool_call(ctx)

    assert _rejected(ctx)
    assert "[PERMISSION_DENIED]" in str(ctx.inputs.tool_result)
    assert "could not be completed" not in str(ctx.inputs.tool_result)


@pytest.mark.asyncio
async def test_an_allow_rule_still_lets_the_call_through() -> None:
    """Guard. A decided ALLOW is unaffected. Passes before and after the fix."""
    rail = _rail(policy=ALLOW_POLICY)
    ctx = _ctx()

    await rail.before_tool_call(ctx)

    assert not _rejected(ctx)
    assert ctx.inputs.tool_result is None


@pytest.mark.asyncio
async def test_cancellation_is_not_turned_into_a_rejection() -> None:
    """Guard. ``CancelledError`` is a ``BaseException`` and must keep propagating.

    Swallowing it would leave a cancelled task looking like a denied tool call
    and would break cancellation for everything above the rail.
    """
    rail = _rail()

    async def cancelled(**_kwargs: Any) -> Any:
        raise asyncio.CancelledError

    rail._engine.check_permission = cancelled
    ctx = _ctx()

    with pytest.raises(asyncio.CancelledError):
        await rail.before_tool_call(ctx)

    assert not _rejected(ctx)


# --------------------------------------------------------------------------
# End to end: what the swallowed exception costs when a real agent runs.
# --------------------------------------------------------------------------


class _RecordingTool(Tool):
    """A stand-in for any tool an operator would put behind an ASK rule."""

    def __init__(self) -> None:
        super().__init__(
            ToolCard(
                name="write_file",
                description="Write a file",
                input_params={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path"},
                        "content": {"type": "string", "description": "Content"},
                    },
                    "required": ["file_path", "content"],
                },
            )
        )
        self.calls: list[Any] = []

    async def invoke(self, inputs: Any, session: Any = None, **_kwargs: Any) -> Any:
        self.calls.append(inputs)
        return {"success": True}

    async def stream(self, inputs: Any, **kwargs: Any) -> Any:
        yield await self.invoke(inputs, **kwargs)


@pytest.mark.asyncio
async def test_a_broken_gate_does_not_let_the_tool_run_in_a_real_agent() -> None:
    """The whole chain: a raising host must not end with the tool having run.

    The unit tests above pin the rail's own behaviour. This one pins the
    consequence, because the fail-open is a property of the rail *and* the
    callback framework together: the framework logs the escaped exception and
    carries on, so nothing between the rail and the tool notices that no
    decision was ever made.
    """
    os.environ.setdefault("LLM_SSL_VERIFY", "false")

    async def unavailable(_request: Any) -> Any:
        raise RuntimeError(BOOM)

    await Runner.start()
    try:
        tool = _RecordingTool()
        agent = ReActAgent(card=AgentCard(id="undecided_gate_agent"))
        config = ReActAgentConfig()
        config.configure_model_client(
            provider="OpenAI",
            api_key="sk-not-a-real-key",
            api_base="https://api.example.invalid/v1",
            model_name="mock-model",
            verify_ssl=False,
        )
        config.configure_prompt_template([{"role": "system", "content": ""}])
        agent.configure(config)
        Runner.resource_mgr.add_tool(tool)
        agent.ability_manager.add(tool.card)

        rail = build_permission_interrupt_rail(
            permissions=ASK_POLICY,
            host=ToolPermissionHost(request_permission_confirmation=unavailable),
        )
        await agent.register_rail(rail)

        session = create_agent_session(
            session_id="undecided_gate_session",
            card=AgentCard(id="undecided_gate_agent"),
        )
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "write_file",
                '{"file_path": "beeblebrox.md", "content": "mostly harmless"}',
            ),
            create_text_response("Finished."),
        ])

        with patch(
            "openjiuwen.core.foundation.llm.model.Model.stream",
            side_effect=mock_llm.stream,
        ), patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            side_effect=mock_llm.invoke,
        ):
            await Runner.run_agent(
                agent=agent,
                inputs={"query": "write beeblebrox.md"},
                session=session,
            )

        assert tool.calls == []
    finally:
        await Runner.stop()
