# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import subprocess

import pytest

from openjiuwen.core.sys_operation.shell_process_registry import (
    SHELL_PROCESS_REGISTRY,
    kill_shell_processes_for_session,
    set_shell_session_id,
    reset_shell_session_id,
    terminate_shell_process,
)


class _FakePopen:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._returncode = 1

    def kill(self) -> None:
        self.kill_calls += 1
        self._returncode = 1

    def wait(self, timeout: float | None = None) -> int:
        if self._returncode is None:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)
        return self._returncode


def test_terminate_shell_process_windows_uses_taskkill_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[list[str]] = []

    monkeypatch.setattr("openjiuwen.core.sys_operation.shell_process_registry.os.name", "nt")
    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.shutil.which",
        lambda name: r"C:\Windows\System32\taskkill.exe" if name == "taskkill" else None,
    )

    proc = _FakePopen(pid=4242)

    def fake_run(cmd, **_kwargs):  # noqa: ANN001
        runs.append(list(cmd))
        proc._returncode = 1
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.subprocess.run",
        fake_run,
    )

    assert terminate_shell_process(proc) is True
    assert runs == [
        [r"C:\Windows\System32\taskkill.exe", "/PID", "4242", "/T", "/F"],
    ]
    assert proc.terminate_calls == 0
    assert proc.kill_calls == 0


def test_terminate_shell_process_windows_falls_back_without_taskkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openjiuwen.core.sys_operation.shell_process_registry.os.name", "nt")
    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.shutil.which",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("taskkill must not run")),
    )

    proc = _FakePopen()
    assert terminate_shell_process(proc) is True
    assert proc.terminate_calls == 1


def test_async_handler_windows_kill_uses_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.core.sys_operation.local.utils import AsyncProcessHandler

    runs: list[list[str]] = []
    monkeypatch.setattr("openjiuwen.core.sys_operation.local.utils.os.name", "nt")
    monkeypatch.setattr("openjiuwen.core.sys_operation.shell_process_registry.os.name", "nt")
    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.shutil.which",
        lambda name: r"C:\Windows\System32\taskkill.exe" if name == "taskkill" else None,
    )

    class _Proc:
        pid = 25788
        returncode = 1

        def kill(self) -> None:
            raise AssertionError("direct kill should not run after taskkill")

    def fake_run(cmd, **_kwargs):  # noqa: ANN001
        runs.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.subprocess.run",
        fake_run,
    )
    AsyncProcessHandler(_Proc())._kill_process_tree()  # type: ignore[arg-type]
    assert runs == [
        [r"C:\Windows\System32\taskkill.exe", "/PID", "25788", "/T", "/F"],
    ]


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
