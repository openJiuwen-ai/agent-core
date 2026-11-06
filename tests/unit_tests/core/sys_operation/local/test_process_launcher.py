# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host launch adapter preserves defaults and isolates concurrent tool tasks."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.core.sys_operation.local.code_operation import CodeOperation
from openjiuwen.core.sys_operation.local.process_launcher import bind_process_launcher, create_process
from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation
from openjiuwen.core.sys_operation.shell import ShellType


@pytest.mark.asyncio
async def test_default_and_exception_reset_preserve_arguments():
    factory = AsyncMock(return_value=object())
    expected = await create_process(factory, "program", cwd="work", creationflags=8)
    factory.assert_awaited_once_with("program", cwd="work", creationflags=8)
    adapter = AsyncMock(side_effect=RuntimeError("injection failed"))
    with pytest.raises(RuntimeError, match="injection failed"):
        with bind_process_launcher(adapter):
            await create_process(factory, "protected")
    assert factory.await_count == 1  # No unprotected fallback.
    assert await create_process(factory, "normal") is expected


@pytest.mark.asyncio
async def test_concurrent_contexts_do_not_leak():
    factory = AsyncMock(return_value="normal")

    async def run(name):
        async def adapter(original, *args, **kwargs):
            assert original is factory
            await asyncio.sleep(0)
            return name
        with bind_process_launcher(adapter):
            await asyncio.sleep(0)
            return await create_process(factory)

    assert await asyncio.gather(run("a"), run("b"), create_process(factory)) == ["a", "b", "normal"]


@pytest.mark.asyncio
@pytest.mark.parametrize("use_shell", [True, False])
@pytest.mark.parametrize("background,stream", [(False, False), (False, True), (True, False)])
async def test_shell_all_launch_modes_use_host_adapter(monkeypatch, tmp_path, use_shell, background, stream):
    operation = ShellOperation("shell", OperationMode.LOCAL, "test", LocalWorkConfig())
    argv = "echo test" if use_shell else ["program", "test"]
    monkeypatch.setattr(operation, "_resolve_execution_plan", lambda *_args: (argv, use_shell, "cmd"))
    monkeypatch.setattr(operation, "_wrap_command_with_buffering", lambda command: command)
    direct = AsyncMock(side_effect=AssertionError("unprotected launch"))
    monkeypatch.setattr(asyncio, "create_subprocess_shell" if use_shell else "create_subprocess_exec", direct)
    adapter = AsyncMock(return_value=object())
    with bind_process_launcher(adapter):
        result = await operation._create_subprocess("echo test", tmp_path, {"KEY": "VALUE"},
            shell_type=ShellType.CMD, background=background, stream=stream)
    assert result is adapter.return_value
    assert adapter.call_args.args[0] is direct
    assert adapter.call_args.kwargs["env"] == {"KEY": "VALUE"}
    assert adapter.call_args.kwargs["stdout"] == (asyncio.subprocess.DEVNULL if background else asyncio.subprocess.PIPE)
    direct.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_code_both_entries_stop_on_host_launch_failure(monkeypatch, stream):
    operation = CodeOperation("code", OperationMode.LOCAL, "test", LocalWorkConfig())
    monkeypatch.setattr(operation, "_build_subprocess_cmd", AsyncMock(return_value=(["python", "-c", "print(1)"], None)))
    direct = AsyncMock(side_effect=AssertionError("unprotected launch"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", direct)
    adapter = AsyncMock(side_effect=RuntimeError("guard refused startup"))
    with bind_process_launcher(adapter):
        if stream:
            result = [item async for item in operation.execute_code_stream("print(1)")]
        else:
            result = await operation.execute_code("print(1)")
    assert "guard refused startup" in str(result)
    adapter.assert_awaited_once()
    direct.assert_not_awaited()
