"""A host verifier replaces native inference and fails closed on uncertainty."""

import asyncio
import json
import os
from contextvars import ContextVar
from types import SimpleNamespace
from typing import get_type_hints

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.sys_operation import OperationMode, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import LocalWorkConfig
from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.toolguard.workspace_delete import LocalDeleteExecution
from openjiuwen.harness.tools.shell.bash import BashTool


def test_host_annotation_is_available_for_sdk_introspection():
    assert "resolve_shell_execution" in get_type_hints(ToolPermissionHost)


def permissions():
    return {
        "enabled": True,
        "defer_unmatched": True,
        "strict_tool_command_merge": True,
        "tools": {"bash": "allow"},
        "rules": [{"id": "delete", "tools": ["shell"], "pattern": "*", "action": "ask", "layer": "user"}],
        "composition": {
            "schema_version": 2,
            "source_order": ["user", "user_group"],
            "workspace_layer": "user_group",
            "tool_layers": {"user": {"bash": "allow"}, "user_group": {}},
            "workspace_delete_defaults": {
                "schema_version": 1,
                "rules": [{"layer": "user", "id": "delete"}],
                "tools": [],
            },
        },
        "file_guard": {"enabled": True, "workspace": {"write": "allow"}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("verification", ["native", "sync", "async", "none", "error", "forged"])
async def test_host_verifier_has_authority_without_falling_back(tmp_path, verification):
    cfg = permissions()
    proof = LocalDeleteExecution("powershell" if os.name == "nt" else "posix", tmp_path)
    received = []

    def verifier(ctx, call, args):
        received.append((ctx, call, args))
        if verification == "error":
            raise RuntimeError("unknown provider")
        if verification == "forged":
            return {"dialect": proof.dialect, "cwd": tmp_path}
        return proof if verification == "sync" else None

    async def async_verifier(*args):
        received.append(args)
        return proof

    operation = SysOperation(SysOperationCard(id="host-proof", mode=OperationMode.LOCAL))
    operation._instances["shell"] = ShellOperation("shell", OperationMode.LOCAL, "test", LocalWorkConfig())
    tool = BashTool(operation)
    call = ToolCall(
        id="delete",
        type="function",
        name="bash",
        arguments=json.dumps(
            {
                "command": "Remove-Item -LiteralPath data.txt" if os.name == "nt" else "rm -- data.txt",
                "workdir": str(tmp_path),
                "shell_type": "powershell" if os.name == "nt" else "sh",
            }
        ),
    )
    ctx = SimpleNamespace(
        agent=SimpleNamespace(ability_manager=SimpleNamespace(_tool_instances={"bash": tool})),
        session=SimpleNamespace(session_id="host-proof", get_state=lambda *_: None),
        inputs=SimpleNamespace(tool_call=call),
        extra={},
    )
    host = ToolPermissionHost(resolve_workspace_dir=lambda: tmp_path)
    if verification != "native":
        host.resolve_shell_execution = async_verifier if verification == "async" else verifier
    rail = PermissionInterruptRail(config=cfg, host=host)
    result = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(result, ApproveResult) == (verification in {"native", "sync", "async"})
    if received:
        assert received[0][0] is ctx and received[0][1] is call


@pytest.mark.asyncio
async def test_shared_rail_binds_each_sessions_root_after_async_verification(tmp_path):
    root = ContextVar("batch1-root", default=None)
    ready = asyncio.Event()
    arrivals = []
    cfg = permissions()

    async def verify(_ctx, _call, _args):
        own = root.get()
        arrivals.append(own)
        if len(arrivals) == 2:
            ready.set()
        await ready.wait()
        return LocalDeleteExecution("powershell" if os.name == "nt" else "posix", own)

    rail = PermissionInterruptRail(
        config=cfg,
        host=ToolPermissionHost(
            get_permissions_snapshot=lambda **_: cfg,
            resolve_workspace_dir=root.get,
            resolve_shell_execution=verify,
        ),
    )

    async def run(name):
        own = tmp_path / name
        own.mkdir()
        token = root.set(own)
        try:
            call = ToolCall(
                id=name,
                type="function",
                name="bash",
                arguments=json.dumps(
                    {
                        "command": "Remove-Item -LiteralPath data.txt" if os.name == "nt" else "rm -- data.txt",
                        "workdir": str(own),
                    }
                ),
            )
            ctx = SimpleNamespace(
                session=SimpleNamespace(session_id=name, get_state=lambda *_: None),
                inputs=SimpleNamespace(tool_call=call),
                extra={},
            )
            result = await rail.resolve_interrupt(ctx, call, None)
            assert isinstance(result, ApproveResult)
            assert json.loads(call.arguments)["workdir"] == str(own)
        finally:
            root.reset(token)

    await asyncio.gather(run("a"), run("b"))
