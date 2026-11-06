# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PermissionInterruptRail first check: direct path baseline and ``tool.check`` event path."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.events.builtins.event_types.tool_check import TOOL_CHECK, ToolCheckOutput
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, RejectResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult


def _rail(scene_out=None, engine_result: PermissionResult | None = None, seen: list | None = None):
    async def scene_hook(hook_input):
        if seen is not None:
            hook_input.tool_args["scene"] = True
            seen.append(hook_input.tool_args)
        return scene_out

    rail = PermissionInterruptRail(
        config={"enabled": True, "tools": {"bash": "ask"}},
        host=ToolPermissionHost(permission_scene_hook=scene_hook),
    )
    rail._engine.check_permission = AsyncMock(
        return_value=engine_result or PermissionResult(permission=PermissionLevel.ALLOW)
    )
    return rail


def _ctx() -> tuple[AgentCallbackContext, ToolCall]:
    ctx = AgentCallbackContext(agent=object(), session=create_agent_session(session_id="tool-check-session"))
    tool_call = ToolCall(id="c1", type="function", name="bash", arguments='{"command": "ls"}')
    return ctx, tool_call


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scene_out", "expected"),
    [
        (("approve",), ApproveResult()),
        (("reject", "custom refusal"), RejectResult(tool_result="custom refusal")),
        (("reject",), RejectResult(tool_result="[PERMISSION_DENIED]")),
    ],
    ids=["approve", "reject-message", "reject-default"],
)
@pytest.mark.parametrize("user_input", [None, {"approved": True}], ids=["first-check", "resume"])
async def test_scene_hook_decision_short_circuits_engine(scene_out, expected, user_input) -> None:
    rail = _rail(scene_out=scene_out)
    ctx, tool_call = _ctx()

    decision = await rail.resolve_interrupt(ctx=ctx, tool_call=tool_call, user_input=user_input)

    assert decision == expected
    assert rail._engine.check_permission.await_count == 0


@pytest.mark.asyncio
async def test_engine_receives_tool_args_mutated_by_scene_hook() -> None:
    seen: list[dict] = []
    rail = _rail(seen=seen)
    ctx, tool_call = _ctx()

    await rail.resolve_interrupt(ctx=ctx, tool_call=tool_call, user_input=None)

    engine_args = rail._engine.check_permission.await_args.kwargs["tool_args"]
    assert engine_args is seen[0]
    assert engine_args == {"command": "ls", "scene": True}


async def _published_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    Runner.set_extension_bus(bus)
    return bus


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scene_out", "engine_result"),
    [
        (None, PermissionResult(permission=PermissionLevel.ALLOW, matched_rule="r-allow")),
        (None, PermissionResult(permission=PermissionLevel.DENY, matched_rule="r-deny", reason="nope")),
        (None, PermissionResult(permission=PermissionLevel.ASK, matched_rule="r-ask", reason="confirm")),
        (("reject", "custom refusal"), None),
    ],
    ids=["allow", "deny", "ask", "scene-reject"],
)
async def test_empty_extension_chain_matches_direct_first_check(scene_out, engine_result) -> None:
    ctx, tool_call = _ctx()
    direct_rail = _rail(scene_out=scene_out, engine_result=engine_result)
    direct = await direct_rail.resolve_interrupt(ctx=ctx, tool_call=tool_call, user_input=None)

    bus_rail = _rail(scene_out=scene_out, engine_result=engine_result)
    try:
        bus = await _published_bus()
        with patch.object(bus, "trigger", wraps=bus.trigger) as trigger:
            via_bus = await bus_rail.resolve_interrupt(ctx=ctx, tool_call=tool_call, user_input=None)
    finally:
        Runner.set_extension_bus(None)

    assert via_bus == direct
    expected_trigger_count = 0 if scene_out is not None else 1
    assert trigger.await_count == expected_trigger_count
    if expected_trigger_count:
        assert trigger.await_args.args[0].event_type == TOOL_CHECK
    assert bus_rail._engine.check_permission.await_count == direct_rail._engine.check_permission.await_count


@pytest.mark.asyncio
async def test_empty_extension_chain_preserves_engine_result_for_confirmation() -> None:
    engine_result = PermissionResult(
        permission=PermissionLevel.ASK,
        matched_rule="r-ask",
        reason="confirm",
        external_paths=["/outside"],
    )
    captured: list[PermissionResult] = []

    async def request_confirmation(request):
        captured.append(request.result)
        return None

    rail = PermissionInterruptRail(
        config={"enabled": True, "tools": {"bash": "ask"}},
        host=ToolPermissionHost(request_permission_confirmation=request_confirmation),
    )
    rail._engine.check_permission = AsyncMock(return_value=engine_result)
    ctx, tool_call = _ctx()
    try:
        await _published_bus()
        await rail.resolve_interrupt(ctx=ctx, tool_call=tool_call, user_input=None)
    finally:
        Runner.set_extension_bus(None)

    assert captured == [engine_result]
    assert captured[0] is engine_result


@pytest.mark.asyncio
async def test_handler_output_changes_only_public_permission_fields() -> None:
    engine_result = PermissionResult(
        permission=PermissionLevel.ASK,
        matched_rule="r-ask",
        reason="original",
        external_paths=["/outside"],
    )
    captured: list[PermissionResult] = []

    async def rewrite_reason(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        output = await event_next(event)
        return ToolCheckOutput(decision=output.decision, reason="rewritten")

    async def request_confirmation(request):
        captured.append(request.result)
        return None

    rail = PermissionInterruptRail(
        config={"enabled": True, "tools": {"bash": "ask"}},
        host=ToolPermissionHost(request_permission_confirmation=request_confirmation),
    )
    rail._engine.check_permission = AsyncMock(return_value=engine_result)
    ctx, tool_call = _ctx()
    try:
        bus = await _published_bus()
        bus.register_event_handler(TOOL_CHECK, rewrite_reason, {}, HandlerLevel.USER)
        await rail.resolve_interrupt(ctx=ctx, tool_call=tool_call, user_input=None)
    finally:
        Runner.set_extension_bus(None)

    assert len(captured) == 1
    assert captured[0] is not engine_result
    assert captured[0].permission == PermissionLevel.ASK
    assert captured[0].reason == "rewritten"
    assert captured[0].matched_rule == "r-ask"
    assert captured[0].external_paths is engine_result.external_paths


@pytest.mark.asyncio
async def test_resume_path_does_not_trigger_tool_check() -> None:
    rail = _rail()
    ctx, tool_call = _ctx()
    try:
        bus = await _published_bus()
        with patch.object(bus, "trigger", wraps=bus.trigger) as trigger:
            decision = await rail.resolve_interrupt(ctx=ctx, tool_call=tool_call, user_input={"approved": True})
    finally:
        Runner.set_extension_bus(None)

    assert isinstance(decision, ApproveResult)
    assert trigger.await_count == 0


@pytest.mark.asyncio
async def test_handler_short_circuit_decides_without_engine() -> None:
    async def deny(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return ToolCheckOutput(decision="deny", reason="blocked by extension")

    rail = _rail()
    ctx, tool_call = _ctx()
    try:
        bus = await _published_bus()
        bus.register_event_handler(TOOL_CHECK, deny, {"tool_name": "bash"}, HandlerLevel.USER)
        decision = await rail.resolve_interrupt(ctx=ctx, tool_call=tool_call, user_input=None)
    finally:
        Runner.set_extension_bus(None)

    assert decision == RejectResult(tool_result="[PERMISSION_DENIED] blocked by extension")
    assert rail._engine.check_permission.await_count == 0
