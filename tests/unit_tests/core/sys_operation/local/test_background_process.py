# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import sys

import pytest

from openjiuwen.core.sys_operation.local.utils import AsyncProcessHandler


@pytest.mark.asyncio
async def test_background_reports_stderr_when_process_exits_early():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('useful parser error\\n'); raise SystemExit(7)",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    pid, error = await AsyncProcessHandler(process).background(grace=2)

    assert pid == process.pid
    assert error == "process exited early with code 7: useful parser error"


@pytest.mark.asyncio
async def test_background_stderr_is_bounded_but_fully_drained():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('x' * 100_000); raise SystemExit(9)",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    _, error = await AsyncProcessHandler(process).background(grace=2)

    assert error is not None
    assert error.startswith("process exited early with code 9: ")
    assert error.endswith("[stderr truncated]")
    assert len(error) < 9000
