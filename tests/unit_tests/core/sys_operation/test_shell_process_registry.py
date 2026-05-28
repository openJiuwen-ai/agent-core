# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.sys_operation.shell_process_registry import (
    SHELL_PROCESS_REGISTRY,
    kill_shell_processes_for_session,
    set_shell_session_id,
    reset_shell_session_id,
)


@pytest.mark.asyncio
async def test_kill_tracked_asyncio_process_for_session() -> None:
    token = set_shell_session_id("sess_kill")
    proc = await asyncio.create_subprocess_exec(
        "sleep",
        "30",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    SHELL_PROCESS_REGISTRY.register("sess_kill", proc)
    await asyncio.sleep(0.05)
    killed = kill_shell_processes_for_session("sess_kill")
    assert killed == 1
    await asyncio.wait_for(proc.wait(), timeout=3)
    reset_shell_session_id(token)
