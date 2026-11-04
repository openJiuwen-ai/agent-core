"""Dialect binding follows the registered executor on every platform."""

import builtins
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig
from openjiuwen.harness.rails.interrupt.interrupt_base import RejectResult
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
