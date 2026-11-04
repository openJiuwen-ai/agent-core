"""Dialect binding follows the registered executor on every platform."""

import asyncio
import builtins
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, RejectResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.tools.shell.bash import BashTool
from openjiuwen.harness.tools.shell.powershell import PowerShellTool


def callback(tool=None, name="bash", selected="auto"):
    args = {"command": "echo hello", "shell_type": selected}
    call = ToolCall(id="dialect", type="function", name=name, arguments=json.dumps(args))
    ctx = SimpleNamespace(
        agent=SimpleNamespace(ability_manager=SimpleNamespace(_tool_instances={name: tool})),
        inputs=ToolCallInputs(tool_call=call, tool_name=name, tool_args=call.arguments), session=None, extra={},
    )
    return ctx, call, args


def operation(sandbox):
    if not sandbox:
        return SysOperation(SysOperationCard(id="local-dialect"))
    return SysOperation(SysOperationCard(
        id="sandbox-dialect", mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(launcher_config=PreDeployLauncherConfig(
            sandbox_type="jiuwenbox", base_url="http://localhost:8321",
        )),
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("subclass", [False, True])
@pytest.mark.parametrize("selected,expected", [(None, "bash"), ("auto", "bash"), ("cmd", "cmd")])
async def test_native_jiuwenbox_dialect_is_platform_independent(subclass, selected, expected):
    class InheritedBash(BashTool):
        pass

    tool = (InheritedBash if subclass else BashTool)(operation(True))
    ctx, call, args = callback(tool, selected=selected)
    if selected is None:
        args.pop("shell_type")
    bound = await PermissionInterruptRail(config={})._bind_shell_type(ctx, call, args)
    assert bound["shell_type"] == expected
    assert json.loads(call.arguments) == bound
    assert ctx.inputs.tool_args == call.arguments


@pytest.mark.asyncio
@pytest.mark.parametrize("sandbox", [False, True])
async def test_powershell_ignores_model_selector(sandbox):
    ctx, call, args = callback(PowerShellTool(operation(sandbox)), name="powershell", selected="cmd")
    bound = await PermissionInterruptRail(config={})._bind_shell_type(ctx, call, args)
    assert bound["shell_type"] == "powershell"
    assert json.loads(call.arguments) == bound


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_host_dialect_precedes_native_inference_and_is_normalized(asynchronous):
    def resolver(*_):
        return "  PoWeRsHeLl  "

    async def async_resolver(*_):
        return resolver()

    ctx, call, args = callback(BashTool(operation(True)))
    rail = PermissionInterruptRail(config={}, host=ToolPermissionHost(
        resolve_shell_type=async_resolver if asynchronous else resolver,
    ))
    assert (await rail._bind_shell_type(ctx, call, args))["shell_type"] == "powershell"
    assert json.loads(call.arguments)["shell_type"] == "powershell"


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["exception", "unsupported", "unhashable"])
async def test_bad_host_dialect_rejects_without_ordinary_policy_fallback(asynchronous, failure):
    def resolver(*_):
        if failure == "exception":
            raise RuntimeError("resolver unavailable")
        return [] if failure == "unhashable" else "unknown-shell"

    async def async_resolver(*_):
        return resolver()

    ctx, call, _ = callback(BashTool(operation(True)))
    original = call.arguments
    rail = PermissionInterruptRail(config={"enabled": True, "tools": {"bash": "allow"}}, host=ToolPermissionHost(
        resolve_shell_type=async_resolver if asynchronous else resolver,
    ))
    rail._engine.check_permission = AsyncMock()
    decision = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(decision, RejectResult)
    assert "Cannot verify" in decision.tool_result
    rail._engine.check_permission.assert_not_awaited()
    assert call.arguments == original


@pytest.mark.asyncio
async def test_non_shell_skips_resolver_and_optional_shell_imports(monkeypatch):
    resolver = AsyncMock(side_effect=RuntimeError("must not resolve a file tool"))
    rail = PermissionInterruptRail(config={}, host=ToolPermissionHost(resolve_shell_type=resolver))
    ctx, call, args = callback(name="read_file")
    original_import = builtins.__import__

    def guarded_import(name, *a, **kw):
        if name.startswith("openjiuwen.harness.tools.shell"):
            raise ImportError("optional shell package omitted")
        return original_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert await rail._bind_shell_type(ctx, call, args) is args
    resolver.assert_not_called()


@pytest.mark.asyncio
async def test_missing_powershell_does_not_break_bash_binding(monkeypatch):
    ctx, call, args = callback(BashTool(operation(True)))
    original_import = builtins.__import__

    def guarded_import(name, *a, **kw):
        if name.startswith("openjiuwen.harness.tools.shell.powershell"):
            raise ImportError("optional powershell package omitted")
        return original_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    bound = await PermissionInterruptRail(config={})._bind_shell_type(ctx, call, args)
    assert bound["shell_type"] == "bash"


@pytest.mark.asyncio
@pytest.mark.parametrize("was_shell,is_shell", [(False, True), (True, False)])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_snapshot_categories_apply_before_binding_and_execution_verification(was_shell, is_shell, asynchronous):
    name = "custom_command"
    events = []
    observations = []

    def config(shell):
        return {"enabled": True, "package_builtin_rules": False, "tools": {name: "allow"},
                "categories": {"shell": [name if shell else "other_command"]}}

    fresh = config(is_shell)

    def snapshot():
        events.append("snapshot")
        return fresh

    def resolver(*_):
        events.append("bind")
        return "bash"

    async def async_resolver(*args):
        return resolver(*args)

    async def verify(_ctx, _call, args):
        events.append("verify")
        observations.append((rail._engine.config["categories"], args["shell_type"]))

    rail = PermissionInterruptRail(config=config(was_shell), host=ToolPermissionHost(
        get_permissions_snapshot=snapshot,
        resolve_shell_type=async_resolver if asynchronous else resolver,
        resolve_shell_execution=verify,
    ))
    ctx, call, _ = callback(name=name)
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), ApproveResult)
    assert events == (["snapshot", "bind", "verify"] if is_shell else ["snapshot", "verify"])
    assert observations == [(fresh["categories"], "bash" if is_shell else "auto")]
    assert json.loads(call.arguments)["shell_type"] == ("bash" if is_shell else "auto")


@pytest.mark.asyncio
@pytest.mark.parametrize("inactive", ["disabled", "host", "engine"])
@pytest.mark.parametrize("failure", ["exception", "unsupported", "unhashable"])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_failed_binding_preserves_disabled_checks_contract(inactive, failure, asynchronous):
    def resolver(*_):
        if failure == "exception":
            raise RuntimeError("resolver unavailable")
        return [] if failure == "unhashable" else "unknown-shell"

    async def async_resolver(*args):
        return resolver(*args)

    host = ToolPermissionHost(
        resolve_shell_type=async_resolver if asynchronous else resolver,
        tool_permission_checks_active=(lambda: False) if inactive == "host" else None,
    )
    rail = PermissionInterruptRail(config={"enabled": inactive != "disabled"}, host=host)
    if inactive == "engine":
        rail._engine.set_permission_checks_active(lambda: False)
    ctx, call, _ = callback(BashTool(operation(True)))
    original = call.arguments
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), ApproveResult)
    assert call.arguments == original
    assert ctx.inputs.tool_args == original


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_binding_failure_uses_fresh_master_switch(enabled):
    rail = PermissionInterruptRail(config={"enabled": not enabled}, host=ToolPermissionHost(
        get_permissions_snapshot=lambda: {"enabled": enabled},
        resolve_shell_type=Mock(side_effect=RuntimeError("resolver unavailable")),
    ))
    ctx, call, _ = callback()
    result = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(result, RejectResult if enabled else ApproveResult)


@pytest.mark.asyncio
@pytest.mark.parametrize("inactive", ["disabled", "host"])
async def test_successful_binding_is_retained_when_checks_are_disabled(inactive):
    resolver = Mock(return_value="bash")
    rail = PermissionInterruptRail(config={"enabled": inactive != "disabled"}, host=ToolPermissionHost(
        resolve_shell_type=resolver,
        tool_permission_checks_active=(lambda: False) if inactive == "host" else None,
    ))
    ctx, call, _ = callback()
    assert isinstance(await rail.resolve_interrupt(ctx, call, None), ApproveResult)
    resolver.assert_called_once()
    assert json.loads(call.arguments)["shell_type"] == "bash"
    assert ctx.inputs.tool_args == call.arguments


@pytest.mark.asyncio
async def test_native_binding_accepts_sys_operation_subclass():
    class InheritedOperation(SysOperation):
        pass

    card = SysOperationCard(id="inherited-dialect", mode=OperationMode.SANDBOX,
                            gateway_config=SandboxGatewayConfig(launcher_config=PreDeployLauncherConfig(
                                sandbox_type="jiuwenbox", base_url="http://localhost:8321")))
    tool = BashTool(InheritedOperation(card))
    ctx, call, args = callback(tool)
    assert (await PermissionInterruptRail(config={})._bind_shell_type(ctx, call, args))["shell_type"] == "bash"


@pytest.mark.asyncio
async def test_shared_rail_retains_each_snapshot_after_async_dialect_binding():
    ready = asyncio.Event()
    arrivals = []

    def snapshot(session_id):
        return {"enabled": True, "package_builtin_rules": False,
                "tools": {"bash": "allow" if session_id == "allow" else "deny"}}

    async def resolver(_ctx, call, _args):
        arrivals.append(call.id)
        if len(arrivals) == 2:
            ready.set()
        await ready.wait()
        return "bash"

    rail = PermissionInterruptRail(config={}, host=ToolPermissionHost(
        get_permissions_snapshot=snapshot, resolve_shell_type=resolver,
    ))

    async def run(session_id):
        ctx, call, _ = callback()
        ctx.session = SimpleNamespace(session_id=session_id, get_state=lambda *_: None)
        call.id = session_id
        return await rail.resolve_interrupt(ctx, call, None)

    allowed, denied = await asyncio.gather(run("allow"), run("deny"))
    assert isinstance(allowed, ApproveResult)
    assert isinstance(denied, RejectResult)


@pytest.mark.parametrize("sandbox,expected", [(False, None), (True, "jiuwenbox")])
def test_native_provider_query_and_local_dialect_preservation(sandbox, expected):
    executor = operation(sandbox)
    assert executor.sandbox_type == expected
    assert BashTool(executor).resolve_shell_type({"command": "echo hello", "shell_type": "auto"}) == (
        "bash" if sandbox else None
    )
