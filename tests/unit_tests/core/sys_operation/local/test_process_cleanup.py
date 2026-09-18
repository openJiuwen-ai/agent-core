# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen.core.sys_operation.local import utils


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "timeout"])
async def test_windows_cleanup_includes_children(outcome):
    process = Mock(pid=1234)
    killer = Mock(returncode=0 if outcome == "success" else 1)
    killer.wait = AsyncMock(return_value=killer.returncode)
    if outcome == "timeout":
        killer.returncode = None
        killer.wait.side_effect = [asyncio.TimeoutError, 1]
    create = AsyncMock(return_value=killer)
    windows = SimpleNamespace(name="nt", path=os.path, environ={"SystemRoot": r"C:\Windows"})
    with (
        patch.object(utils, "os", windows),
        patch.object(utils, "subprocess", SimpleNamespace(CREATE_NO_WINDOW=0x08000000)),
        patch.object(utils.asyncio, "create_subprocess_exec", create),
    ):
        await utils.AsyncProcessHandler(process)._kill_process_tree()

    create.assert_awaited_once_with(
        os.path.join(r"C:\Windows", "System32", "taskkill.exe"),
        "/PID",
        "1234",
        "/T",
        "/F",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=0x08000000,
    )
    if outcome == "success":
        process.kill.assert_not_called()
    else:
        process.kill.assert_called_once()
    if outcome == "timeout":
        killer.kill.assert_called_once()
        assert killer.wait.await_count == 2


@pytest.mark.asyncio
async def test_posix_cleanup_uses_process_group():
    process = Mock(pid=1234)
    posix = SimpleNamespace(name="posix", killpg=Mock())
    with patch.object(utils, "os", posix), patch.object(utils, "signal", SimpleNamespace(SIGKILL=9)):
        await utils.AsyncProcessHandler(process)._kill_process_tree()
    posix.killpg.assert_called_once_with(1234, 9)
    process.kill.assert_not_called()
