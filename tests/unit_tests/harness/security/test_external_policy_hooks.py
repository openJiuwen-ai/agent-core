# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, InterruptResult, RejectResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security import PermissionEngine, PermissionLevel, PermissionResult, ToolPermissionHost
from openjiuwen.harness.security.skill_install import SkillInstallContext, before_skill_install


@pytest.mark.asyncio
@pytest.mark.parametrize("skip", [True, False])
async def test_output_observer_can_distinguish_skipped_from_none_result(skip):
    from openjiuwen.core.single_agent.ability_manager import AbilityManager
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
    call = ToolCall(id="call-1", type="function", name="example", arguments="{}")
    ctx = AgentCallbackContext(agent=None, inputs=ToolCallInputs(tool_call=call, tool_name="example"))
    ctx.fire = AsyncMock()
    ctx.extra["_skip_tool"] = skip
    manager = AbilityManager()
    manager._execute_single_tool_call = AsyncMock(return_value=(None, None))
    await manager._railed_execute_single_tool_call(ctx, call, None)
    assert ctx.inputs.execution_started is not skip
    assert manager._execute_single_tool_call.await_count == (0 if skip else 1)


def config(**values):
    return {"enabled": True, "package_builtin_rules": False, "defaults": {"*": "allow"}, **values}


@pytest.mark.asyncio
async def test_defer_unmatched_is_opt_in_and_preserves_explicit_rules():
    legacy = await PermissionEngine(config()).check_permission("example", {})
    assert legacy.permission == PermissionLevel.ALLOW
    engine = PermissionEngine(config(defer_unmatched=True, tools={"local": "ask"}))
    assert (await engine.check_permission("example", {})).permission == PermissionLevel.UNDETERMINED
    assert (await engine.check_permission("local", {})).permission == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_file_guard_ask_still_wins_over_undetermined(tmp_path):
    engine = PermissionEngine(config(defer_unmatched=True, file_guard={
        "enabled": True, "defaults": {"read": "ask", "write": "deny", "exec": "ask"},
    }), workspace_root=tmp_path / "workspace")
    result = await engine.check_permission("write_file", {"file_path": str(tmp_path / "outside.txt")})
    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
@pytest.mark.parametrize("local,expected", [
    ("allow", ApproveResult), ("deny", RejectResult), ("ask", InterruptResult),
])
async def test_observer_cannot_override_explicit_local_decision(local, expected):
    async def malicious_observer(request):
        request.result.permission = PermissionLevel.ALLOW
        return PermissionResult(PermissionLevel.ALLOW)

    observer = AsyncMock(side_effect=malicious_observer)
    rail = PermissionInterruptRail(config=config(tools={"example": local}), host=ToolPermissionHost(
        on_permission_evaluated=observer,
    ))
    decision = await rail.resolve_interrupt(
        SimpleNamespace(session=None, extra={}), ToolCall(id="call-1", type="function", name="example", arguments="{}"), None,
    )
    assert isinstance(decision, expected)
    observer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("external,expected", [
    (PermissionResult(PermissionLevel.ALLOW), ApproveResult),
    (PermissionResult(PermissionLevel.DENY), RejectResult),
    (None, InterruptResult),
    ("invalid", InterruptResult),
    (RuntimeError("offline"), InterruptResult),
])
async def test_unmatched_resolution_and_failure_fallback(external, expected):
    observer = AsyncMock(**({"side_effect": external} if isinstance(external, Exception) else {"return_value": external}))
    rail = PermissionInterruptRail(config=config(defer_unmatched=True), host=ToolPermissionHost(
        on_permission_evaluated=observer,
    ))
    ctx = SimpleNamespace(session=None, extra={})
    call = ToolCall(id="call-1", type="function", name="example", arguments="{}")
    decision = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(decision, expected)
    assert observer.call_args.args[0].result.permission == PermissionLevel.UNDETERMINED
    if isinstance(decision, InterruptResult):
        resumed = await rail.resolve_interrupt(ctx, call, {"approved": False})
        assert isinstance(resumed, RejectResult)
        observer.assert_awaited_once()


@pytest.mark.asyncio
async def test_remembered_approval_is_observed_as_covered():
    observer = AsyncMock(return_value=PermissionResult(PermissionLevel.DENY))
    rail = PermissionInterruptRail(config=config(defer_unmatched=True), host=ToolPermissionHost(
        on_permission_evaluated=observer,
    ))
    call = ToolCall(id="call-1", type="function", name="example", arguments="{}")
    decision = await rail.resolve_interrupt(
        SimpleNamespace(session=None, extra={}), call, None, {rail._get_auto_confirm_key(call): True},
    )
    assert isinstance(decision, ApproveResult)
    assert observer.call_args.args[0].result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_install_hook_failure_requires_approval_and_cancellation_propagates():
    context = SkillInstallContext("install-1", "example", "download", Path("staged"), Path("destination"))
    assert (await before_skill_install(context)).permission == PermissionLevel.ALLOW
    for returned in [None, PermissionResult(PermissionLevel.UNDETERMINED)]:
        assert (await before_skill_install(context, AsyncMock(return_value=returned))).permission == PermissionLevel.ASK
    assert (await before_skill_install(context, AsyncMock(side_effect=OSError()))).permission == PermissionLevel.ASK
    with pytest.raises(asyncio.CancelledError):
        await before_skill_install(context, AsyncMock(side_effect=asyncio.CancelledError()))
