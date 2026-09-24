# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as jb


@pytest.fixture
def provider(monkeypatch):
    instance = object.__new__(jb.JiuwenBoxShellProvider)
    instance._launcher_extra_params = Mock(return_value={})
    instance._get_sandbox_id = Mock(return_value="sandbox-a")
    client = Mock()
    client.exec.return_value = {"stdout": "sandbox", "stderr": "", "exit_code": 0}
    instance._get_client = Mock(return_value=client)
    local = AsyncMock(return_value={"stdout": "host", "stderr": "", "exit_code": 0, "local": True})
    monkeypatch.setattr(jb, "_run_local_subprocess", local)
    return instance, client, local


@pytest.mark.asyncio
@pytest.mark.parametrize("shell_type, argv", [
    ("bash", ["bash", "-lc"]),
    ("sh", ["sh", "-c"]),
    ("powershell", ["powershell" if os.name == "nt" else "pwsh", "-NoProfile", "-NonInteractive", "-Command"]),
    ("cmd", ["cmd", "/d", "/s", "/c"]),
    (None, ["bash", "-lc"]),
    (" BASH ", ["bash", "-lc"]),
    (" CMD ", ["cmd", "/d", "/s", "/c"]),
    (" PowerShell ", ["powershell" if os.name == "nt" else "pwsh", "-NoProfile", "-NonInteractive", "-Command"]),
])
@pytest.mark.parametrize("excluded", [False, True])
async def test_shell_type_preserved_for_sandbox_and_host_exception(provider, shell_type, argv, excluded):
    instance, client, local = provider
    if excluded:
        instance._launcher_extra_params.return_value = {"excluded_commands": ["echo *"]}
    result = await instance.execute_cmd("echo test", shell_type=shell_type, cwd="work", timeout=12)
    assert result.code == StatusCode.SUCCESS.code
    if excluded:
        local.assert_awaited_once_with(argv + ["echo test"], cwd="work", env=None, timeout=12)
        client.exec.assert_not_called()
    else:
        client.exec.assert_called_once_with(
            "sandbox-a", argv + ["echo test"], cwd="work", timeout=12, environment=None,
        )
        local.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_failure_respects_configured_fallback(provider, fallback):
    instance, client, local = provider
    instance._launcher_extra_params.return_value = {"fallback_on_failure": fallback}
    client.exec.side_effect = RuntimeError("unavailable")
    result = await instance.execute_cmd("echo test", shell_type="cmd")
    if fallback:
        assert result.data.stdout == "host"
        assert local.call_args.args[0] == ["cmd", "/d", "/s", "/c", "echo test"]
    else:
        assert result.code != StatusCode.SUCCESS.code
        local.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_preserves_shell_type(provider):
    instance, client, local = provider
    chunks = [chunk async for chunk in instance.execute_cmd_stream("echo test", shell_type="powershell")]
    assert chunks[-1].data.exit_code == 0
    assert client.exec.call_args.args[1][0] == ("powershell" if os.name == "nt" else "pwsh")
    local.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_shell_never_executes(provider):
    instance, client, local = provider
    result = await instance.execute_cmd("echo test", shell_type="unknown")
    assert result.code != StatusCode.SUCCESS.code
    client.exec.assert_not_called()
    local.assert_not_awaited()


@pytest.mark.parametrize("platform,expected", [("nt", "powershell"), ("posix", "pwsh")])
def test_powershell_platform_default(monkeypatch, platform, expected):
    # Keep the patch local to argv construction; don't alter pathlib's platform.
    with monkeypatch.context() as patch:
        patch.setattr(jb.os, "name", platform)
        assert jb.JiuwenBoxShellProvider._shell_argv("gc file.txt", "powershell")[0] == expected
