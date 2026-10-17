# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

import pytest

from openjiuwen.core.sys_operation.local import utils as process_utils
from openjiuwen.core.sys_operation.local.utils import AsyncProcessHandler


class _ScriptedStream:
    """Stdout/stderr stand-in. After scripted chunks, either EOF or hang."""

    def __init__(self, chunks: list[bytes], *, hang: bool) -> None:
        self._chunks = list(chunks)
        self._hang = hang

    async def read(self, _n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        if self._hang:
            await asyncio.Event().wait()
        return b""


class _ExitedProcess:
    """Process that has already exited; pipes may still be held open."""

    def __init__(self, stdout: _ScriptedStream, stderr: _ScriptedStream, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = None
        self.pid = 1234
        self._returncode = returncode

    async def wait(self) -> int:
        self.returncode = self._returncode
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


@pytest.mark.asyncio
async def test_invoke_returns_partial_output_when_pipes_stay_open(monkeypatch):
    """Start-Process -NoNewWindow leaves the write ends open after powershell exits."""
    monkeypatch.setattr(process_utils, "_POST_EXIT_DRAIN_GRACE_SECONDS", 0.2)
    handler = AsyncProcessHandler(
        _ExitedProcess(
            _ScriptedStream([b"partial-out"], hang=True),
            _ScriptedStream([b"partial-err"], hang=True),
        ),
        timeout=30,
    )

    started = asyncio.get_running_loop().time()
    result = await asyncio.wait_for(handler.invoke(), timeout=2)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 1.5
    assert result.exit_code == 0
    assert result.exception is None
    assert "partial-out" in result.stdout
    assert "partial-err" in result.stderr
    assert "still open" in result.stderr


@pytest.mark.asyncio
async def test_invoke_returns_full_output_when_pipes_close(monkeypatch):
    """A normal exit closes the pipes; the grace is not spent."""
    monkeypatch.setattr(process_utils, "_POST_EXIT_DRAIN_GRACE_SECONDS", 5)
    handler = AsyncProcessHandler(
        _ExitedProcess(
            _ScriptedStream([b"hello"], hang=False),
            _ScriptedStream([b""], hang=False),
        ),
        timeout=30,
    )

    started = asyncio.get_running_loop().time()
    result = await asyncio.wait_for(handler.invoke(), timeout=2)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 1
    assert result.exit_code == 0
    assert result.stdout == "hello"
    assert "still open" not in result.stderr
