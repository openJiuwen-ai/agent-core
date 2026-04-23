# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for PowerShellTool."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "darwin", reason="PowerShell tests are not supported on macOS")

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.harness.tools import PowerShellTool


def _mock_result(*, stdout: str = "", stderr: str = "", exit_code: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        code=StatusCode.SUCCESS.code,
        message="",
        data=SimpleNamespace(stdout=stdout, stderr=stderr, exit_code=exit_code),
    )


@pytest.mark.asyncio
async def test_invoke_forces_powershell_shell_type() -> None:
    shell = MagicMock()
    shell.execute_cmd = AsyncMock(return_value=_mock_result(stdout="ok\n"))

    sys_op = MagicMock()
    sys_op.work_dir = None
    sys_op.shell.return_value = shell

    tool = PowerShellTool(sys_op, language="en")
    res = await tool.invoke({"command": "Write-Output ok"})

    assert res.success is True
    shell.execute_cmd.assert_awaited_once()
    assert shell.execute_cmd.await_args.kwargs["shell_type"] == "powershell"


@pytest.mark.asyncio
async def test_read_only_blocks_write_commands() -> None:
    sys_op = MagicMock()
    sys_op.work_dir = None

    tool = PowerShellTool(sys_op, permission_mode="read_only")
    res = await tool.invoke({"command": "Set-Content test.txt hi"})

    assert res.success is False
    assert "Read-only" in res.error


@pytest.mark.asyncio
async def test_injection_pattern_blocked() -> None:
    sys_op = MagicMock()
    sys_op.work_dir = None

    tool = PowerShellTool(sys_op)
    res = await tool.invoke({"command": "Invoke-Expression \"Get-ChildItem\""})

    assert res.success is False
    assert "injection" in res.error.lower()
