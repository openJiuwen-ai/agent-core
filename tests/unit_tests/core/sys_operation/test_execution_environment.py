# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavioral coverage for task-local execution environment isolation."""

import asyncio
import os
import shlex
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.core.sys_operation.execution_environment import (
    ExecutionEnvironment,
    current_execution_environment,
    execution_environment_overlay,
    execution_environment_scope,
)
from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation
from openjiuwen.core.sys_operation.local.utils import OperationUtils


def _python_command(code: str) -> str:
    arguments = [sys.executable, "-c", code]
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def test_overlay_precedence_removal_and_immutability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JW_ENV_REMOVE", "host")
    values = {"JW_ENV_VALUE": "default", "JW_ENV_REMOVE": None}
    snapshot = ExecutionEnvironment(values, {"JW_ENV_POLICY": "member"})
    values["JW_ENV_VALUE"] = "mutated"
    with execution_environment_scope(snapshot):
        result = OperationUtils.prepare_environment({"JW_ENV_VALUE": "call", "JW_ENV_POLICY": "other"})
        assert result["JW_ENV_VALUE"] == "call"
        assert result["JW_ENV_POLICY"] == "member"
        assert "JW_ENV_REMOVE" not in result
        assert current_execution_environment().values["JW_ENV_VALUE"] == "default"
    assert os.environ["JW_ENV_REMOVE"] == "host"
    assert execution_environment_overlay() == {}


def test_windows_environment_names_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.core.sys_operation import execution_environment as environment

    monkeypatch.setattr(environment, "os", SimpleNamespace(name="nt"))
    snapshot = ExecutionEnvironment({"Path": "default"}, {"path": "policy"})
    overlay = snapshot.merge({"PATH": "explicit", "Remove": None})
    result = environment.apply_environment_overlay({"Path": "host", "REMOVE": "old"}, overlay)
    assert result == {"PATH": "policy"}


def test_windows_overwritten_key_retains_policy_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.core.sys_operation import execution_environment as environment

    snapshot = ExecutionEnvironment({"Path": "default"}, {"Path": "policy"})
    overlay = snapshot.merge({"PATH": "explicit"})
    assert list(overlay) == ["PATH", "Path"]
    monkeypatch.setattr(environment, "os", SimpleNamespace(name="nt"))
    assert environment.apply_environment_overlay({"path": "host"}, overlay) == {"PATH": "policy"}


@pytest.mark.asyncio
async def test_windows_client_preserves_linux_sandbox_variable_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.core.sys_operation import execution_environment as environment
    from openjiuwen.core.sys_operation.sandbox.shell_operation import ShellOperation as SandboxShell

    shell = object.__new__(SandboxShell)
    invoke = AsyncMock(return_value={"code": 0, "message": "ok", "data": None})
    monkeypatch.setattr(shell, "invoke", invoke)
    with monkeypatch.context() as client:
        client.setattr(environment, "os", SimpleNamespace(name="nt"))
        with execution_environment_scope(ExecutionEnvironment({"memberToken": "alice", "removeMe": None})):
            await shell.execute_cmd("git status")
    overlay = invoke.call_args.kwargs["environment"]
    assert overlay == {"memberToken": "alice", "removeMe": None}
    with monkeypatch.context() as server:
        server.setattr(environment, "os", SimpleNamespace(name="posix"))
        assert environment.apply_environment_overlay({"removeMe": "old"}, overlay) == {"memberToken": "alice"}


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_sandbox_code_transports_environment_policy(
    monkeypatch: pytest.MonkeyPatch, stream: bool,
) -> None:
    from openjiuwen.core.sys_operation.sandbox.code_operation import CodeOperation

    operation = object.__new__(CodeOperation)
    received = []
    invoke = AsyncMock(return_value={"code": 0, "message": "ok", "data": None})

    async def invoke_stream(method: str, **params: object) -> AsyncIterator[dict[str, object]]:
        received.append(params["environment"])
        yield {"code": 0, "message": "ok", "data": None}

    monkeypatch.setattr(operation, "invoke", invoke)
    monkeypatch.setattr(operation, "invoke_stream", invoke_stream)
    monkeypatch.setenv("JW_HOST_SECRET", "do-not-send")
    snapshot = ExecutionEnvironment({"memberToken": "default", "removeMe": None}, {"GIT_AUTHOR_NAME": "alice"})
    with execution_environment_scope(snapshot):
        explicit = {"memberToken": "explicit", "GIT_AUTHOR_NAME": "other"}
        if stream:
            results = [item async for item in operation.execute_code_stream("pass", environment=explicit)]
            assert len(results) == 1
        else:
            await operation.execute_code("pass", environment=explicit)
            received.append(invoke.call_args.kwargs["environment"])
    assert received == [{"memberToken": "explicit", "removeMe": None, "GIT_AUTHOR_NAME": "alice"}]
    assert execution_environment_overlay() == {}


@pytest.mark.parametrize("values", [{"": "x"}, {"A=B": "x"}, {"A": 1}, {"A": "x\0y"}])
def test_invalid_environment_is_rejected(values: dict) -> None:
    with pytest.raises(ValueError):
        ExecutionEnvironment(values)


@pytest.mark.asyncio
async def test_parallel_owners_and_cancellation_restore_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def run(name: str) -> str:
        manager = AbilityManager()
        manager.execution_environment_resolver = AsyncMock(return_value=ExecutionEnvironment({"MEMBER": name}))

        async def invoke(*args, **kwargs):
            entered.set()
            await release.wait()
            return execution_environment_overlay()["MEMBER"]

        monkeypatch.setattr(manager, "_execute_single_tool_call_bound", invoke)
        return await manager._execute_single_tool_call(None, None)

    first = asyncio.create_task(run("first"))
    await entered.wait()
    second = asyncio.create_task(run("second"))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == "second"
    assert execution_environment_overlay() == {}


@pytest.mark.asyncio
async def test_unconfigured_agent_shadows_parent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = AbilityManager()

    async def invoke(*args, **kwargs):
        return execution_environment_overlay()

    monkeypatch.setattr(manager, "_execute_single_tool_call_bound", invoke)
    with execution_environment_scope(ExecutionEnvironment({"MEMBER": "parent"})):
        assert await manager._execute_single_tool_call(None, None) == {}
        assert execution_environment_overlay() == {"MEMBER": "parent"}


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_real_shell_receives_overlay(tmp_path: Path, stream: bool) -> None:
    shell = ShellOperation("shell", OperationMode.LOCAL, "test", LocalWorkConfig())
    command = _python_command("import os; assert os.environ['JW_MEMBER'] == 'alice'")
    with execution_environment_scope(ExecutionEnvironment({"JW_MEMBER": "alice"})):
        if stream:
            results = [item async for item in shell.execute_cmd_stream(command, cwd=str(tmp_path))]
            assert results[-1].data.exit_code == 0
        else:
            result = await shell.execute_cmd(command, cwd=str(tmp_path))
            assert result.data.exit_code == 0


@pytest.mark.asyncio
async def test_sandbox_transports_only_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.core.sys_operation.sandbox.shell_operation import ShellOperation as SandboxShell

    shell = object.__new__(SandboxShell)
    invoke = AsyncMock(return_value={"code": 0, "message": "ok", "data": None})
    monkeypatch.setattr(shell, "invoke", invoke)
    monkeypatch.setenv("JW_HOST_SECRET", "must-not-travel")
    with execution_environment_scope(ExecutionEnvironment({"MEMBER": "alice", "REMOVE": None})):
        await shell.execute_cmd("git status")
        assert invoke.call_args.kwargs["environment"] == {"MEMBER": "alice", "REMOVE": None}
        await shell.execute_cmd_background("git status")
        assert invoke.call_args.kwargs["environment"] == {"MEMBER": "alice", "REMOVE": None}


@pytest.mark.asyncio
async def test_background_process_keeps_launch_snapshot(tmp_path: Path) -> None:
    shell = ShellOperation("shell", OperationMode.LOCAL, "test", LocalWorkConfig())
    output = tmp_path / "member.txt"
    code = "import os,pathlib,time; time.sleep(.2); pathlib.Path('member.txt').write_text(os.environ['JW_MEMBER'])"
    command = _python_command(code)
    with execution_environment_scope(ExecutionEnvironment({"JW_MEMBER": "alice"})):
        result = await shell.execute_cmd_background(command, cwd=str(tmp_path), grace=0.01)
        assert result.data.pid > 0
    with execution_environment_scope(ExecutionEnvironment({"JW_MEMBER": "bob"})):
        for _ in range(100):
            if output.exists():
                break
            await asyncio.sleep(0.02)
        assert output.read_text() == "alice"


def test_sys_operation_log_redacts_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.core.sys_operation import base

    captured = {}

    def record(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(base, "create_log_event", record)
    shell = ShellOperation("shell", OperationMode.LOCAL, "test", LocalWorkConfig())
    params = {"environment": {"TOKEN": "private-value"}, "command": "git status"}
    shell._create_sys_operation_event(event_type="test", method_name="execute_cmd", method_params=params)
    assert "private-value" not in str(captured)
    assert params["environment"]["TOKEN"] == "private-value"


@pytest.mark.asyncio
async def test_native_harness_binds_member_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.agent_teams.harness.native_harness import NativeHarness
    from openjiuwen.harness.schema.build_context import BuildContext
    from tests.unit_tests.agent_teams.harness.fixtures import make_spec

    context = BuildContext(member_name="alice")
    resolver = AsyncMock(return_value=ExecutionEnvironment({"MEMBER": "alice"}))
    monkeypatch.setattr(context, "resolve_execution_environment", resolver)
    harness = NativeHarness(make_spec(), context)
    try:
        assert harness.ability_manager.execution_environment_resolver is resolver

        async def invoke(*args, **kwargs):
            return execution_environment_overlay()

        monkeypatch.setattr(harness.ability_manager, "_execute_single_tool_call_bound", invoke)
        result = await harness.ability_manager._execute_single_tool_call(None, None)
        assert result == {"MEMBER": "alice"}
        resolver.assert_awaited_once()
    finally:
        await harness.dispose()


@pytest.mark.skipif(os.name == "nt", reason="Remote POSIX shell wrapper contract")
@pytest.mark.parametrize("provider", ["aio", "yuanrong"])
def test_remote_shell_wrappers_remove_variables(provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.extensions.sys_operation.sandbox.providers.aio import AIOShellProvider
    from openjiuwen.extensions.sys_operation.sandbox.providers.yuanrong import _exec_args

    monkeypatch.setenv("JW_REMOVE_ME", "old")
    script = "test -z \"${JW_REMOVE_ME+x}\" && test \"$JW_VALUE\" = \"hello world\""
    environment = {"JW_REMOVE_ME": None, "JW_VALUE": "hello world"}
    if provider == "aio":
        command = AIOShellProvider._build_wrapped_command(script, environment=environment)
    else:
        # The ADX server runs ``cmd`` through ``/bin/sh -c`` itself.
        command = _exec_args(script, environment=environment)["cmd"]
    subprocess.run(command, shell=True, check=True)


def test_jiuwenbox_hybrid_wrapper_preserves_removal_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import _merge_local_env, wrap_remote_leaf

    monkeypatch.setenv("JW_REMOVE_ME", "old")
    assert "JW_REMOVE_ME" not in _merge_local_env({"JW_REMOVE_ME": None})
    wrapped = wrap_remote_leaf(
        "git status", sandbox_id="sandbox", base_url="http://example.test",
        cli_bin="jiuwenbox", env={"JW_REMOVE_ME": None, "JW_VALUE": "value"},
    )
    assert "-- env -u JW_REMOVE_ME bash" in wrapped
    assert "JW_REMOVE_ME=None" not in wrapped
