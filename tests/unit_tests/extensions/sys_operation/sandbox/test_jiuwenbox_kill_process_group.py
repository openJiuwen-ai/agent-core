# coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for jiuwenbox local subprocess group cleanup.

On Windows os.killpg does not exist; _kill_process_group must fall back to
proc.terminate()/proc.kill() instead of letting AttributeError escape and
leave the timed-out child running as an orphan.
"""

import subprocess
import sys
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import (
    _kill_process_group,
    _run_local_subprocess_process_group,
)


def _spawn_sleep_process(seconds: float = 30.0) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _process_alive(proc: subprocess.Popen[Any]) -> bool:
    return proc.poll() is None


def _killpg_missing():
    """Context manager that removes os.killpg, mimicking Windows."""
    import contextlib
    import os

    @contextlib.contextmanager
    def _remove():
        sentinel = object()
        saved = getattr(os, "killpg", sentinel)
        if saved is not sentinel:
            del os.killpg
        try:
            yield
        finally:
            if saved is not sentinel:
                os.killpg = saved  # type: ignore[method-assign]

    return _remove()


class TestKillProcessGroupFallback:
    def test_missing_killpg_still_terminates_child(self):
        """AttributeError from a missing os.killpg must not escape the helper.

        The fallback terminate() path must run and stop the child instead of
        letting the exception propagate and leaving an orphan behind.
        """
        proc = _spawn_sleep_process()
        try:
            with _killpg_missing():
                _kill_process_group(proc)
            assert not _process_alive(proc), "fallback terminate/kill must stop the child"
            assert proc.returncode is not None
        finally:
            if _process_alive(proc):
                proc.kill()
                proc.wait(timeout=5)

    @pytest.mark.skipif(
        not hasattr(__import__("os"), "killpg"),
        reason="os.killpg does not exist on Windows; the AttributeError path above covers it",
    )
    def test_killpg_oserror_falls_back_to_terminate(self):
        """An OSError from killpg (e.g. ESRCH) must also reach the fallback."""
        import os

        proc = _spawn_sleep_process()
        try:
            with patch.object(os, "killpg", side_effect=OSError("no such process")):
                _kill_process_group(proc)
            assert not _process_alive(proc)
        finally:
            if _process_alive(proc):
                proc.kill()
                proc.wait(timeout=5)

    @pytest.mark.asyncio
    async def test_timeout_run_stops_the_child_group(self):
        """End-to-end: a timed-out child must not outlive its runner."""
        argv = [sys.executable, "-c", "import time; time.sleep(30)"]
        result = await _run_local_subprocess_process_group(argv, timeout=1)
        assert result["exit_code"] == 124
        assert "timeout" in result["stderr"]
