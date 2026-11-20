# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real, bounded subprocess tests. No models, API keys, or external services."""

import asyncio
import http.client
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.core.sys_operation import shell_process_registry as registry_module
from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation
from openjiuwen.core.sys_operation.shell_process_registry import reset_shell_session_id, set_shell_session_id


HELPER = Path(__file__).with_name("_preview_service.py")


def _command(lease: Path, mode: str = "launch") -> str:
    argv = [sys.executable, str(HELPER), mode, str(lease)]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _request(lease: Path, stop: bool = False) -> int | None:
    if not lease.exists():
        return None
    try:
        port = json.loads(lease.read_text(encoding="utf-8"))["port"]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", "/stop/" + lease.stem if stop else "/")
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()
    except (OSError, http.client.HTTPException, json.JSONDecodeError):
        return None


async def _wait_for(lease: Path, alive: bool) -> None:
    for _ in range(100):
        if (_request(lease) == 200) == alive:
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"Preview {lease.name} did not become {'ready' if alive else 'stopped'}")


@pytest_asyncio.fixture
async def runtime(tmp_path, monkeypatch):
    registry = registry_module.ShellProcessRegistry()
    monkeypatch.setattr(registry_module, "SHELL_PROCESS_REGISTRY", registry)
    monkeypatch.delenv("JW_START_NEW_SESSION", raising=False)
    shell = ShellOperation("shell", OperationMode.LOCAL, "lifecycle test", LocalWorkConfig(shell_allowlist=None))
    token = set_shell_session_id("lifecycle_preview")
    try:
        yield shell, registry
    finally:
        registry.close()
        reset_shell_session_id(token)
        for lease in tmp_path.glob("*.json"):
            # A failing implementation must not leave the test's own server alive.
            _request(lease, stop=True)
            await _wait_for(lease, alive=False)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invoke", "stream", "background"])
async def test_preview_survives_parent_exit_and_next_tool_until_session_stop(runtime, tmp_path, mode):
    shell, registry = runtime
    lease = tmp_path / "preview.json"
    command = _command(lease)
    if mode == "stream":
        chunks = [chunk async for chunk in shell.execute_cmd_stream(command, cwd=str(tmp_path))]
        assert chunks[-1].data.exit_code == 0
    elif mode == "background":
        result = await shell.execute_cmd_background(command, cwd=str(tmp_path), grace=0.5)
        assert result.code == 0
    else:
        result = await shell.execute_cmd(command, cwd=str(tmp_path))
        assert result.data.exit_code == 0
    await _wait_for(lease, alive=True)
    for _ in range(2):
        result = await shell.execute_cmd("echo next-tool", cwd=str(tmp_path))
        assert result.data.exit_code == 0
        registry.reap_completed()
        assert _request(lease) == 200
    assert registry.kill_session_tree("lifecycle_preview") >= 1
    await _wait_for(lease, alive=False)
    assert registry.kill_session_tree("lifecycle_preview") == 0


@pytest.mark.asyncio
async def test_stop_covers_subagent_but_preserves_other_session(runtime, tmp_path):
    shell, registry = runtime
    for sid in ("lifecycle_preview_sub_browser", "lifecycle_preview2"):
        token = set_shell_session_id(sid)
        try:
            lease = tmp_path / (sid + ".json")
            await shell.execute_cmd(_command(lease), cwd=str(tmp_path))
            await _wait_for(lease, alive=True)
        finally:
            reset_shell_session_id(token)
    registry.kill_session_tree("lifecycle_preview")
    await _wait_for(tmp_path / "lifecycle_preview_sub_browser.json", alive=False)
    assert _request(tmp_path / "lifecycle_preview2.json") == 200
    restarted = tmp_path / "restarted.json"
    await shell.execute_cmd(_command(restarted), cwd=str(tmp_path))
    await _wait_for(restarted, alive=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_timeout_reclaims_command_and_its_server(runtime, tmp_path, stream):
    shell, _ = runtime
    lease = tmp_path / "timeout.json"
    if stream:
        chunks = [chunk async for chunk in shell.execute_cmd_stream(_command(lease, "hold"), timeout=2)]
        assert any(chunk.code != 0 for chunk in chunks)
    else:
        result = await shell.execute_cmd(_command(lease, "hold"), timeout=2)
        assert result.code != 0
    assert lease.exists(), "test server should start before the timeout"
    await _wait_for(lease, alive=False)


@pytest.mark.asyncio
async def test_cancel_running_command_reclaims_server(runtime, tmp_path):
    shell, _ = runtime
    lease = tmp_path / "cancel.json"
    task = asyncio.create_task(shell.execute_cmd(_command(lease, "hold"), timeout=10))
    await _wait_for(lease, alive=True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _wait_for(lease, alive=False)


@pytest.mark.asyncio
async def test_closing_stream_early_reclaims_server(runtime, tmp_path):
    shell, _ = runtime
    lease = tmp_path / "stream-close.json"
    stream = shell.execute_cmd_stream(_command(lease, "hold"), timeout=10)
    await asyncio.wait_for(anext(stream), timeout=5)
    await _wait_for(lease, alive=True)
    await stream.aclose()
    await _wait_for(lease, alive=False)


@pytest.mark.asyncio
async def test_failed_background_launch_does_not_leave_server(runtime, tmp_path):
    shell, _ = runtime
    lease = tmp_path / "failed.json"
    result = await shell.execute_cmd_background(_command(lease, "fail"), grace=1)
    assert result.code != 0
    assert lease.exists(), "the failing launcher must have started its child"
    await _wait_for(lease, alive=False)


@pytest.mark.asyncio
async def test_running_background_command_survives_next_tool_and_stops_with_session(runtime, tmp_path):
    shell, registry = runtime
    lease = tmp_path / "background.json"
    result = await shell.execute_cmd_background(_command(lease, "server"), grace=0.1)
    assert result.code == 0
    await _wait_for(lease, alive=True)
    await shell.execute_cmd("echo next-tool")
    registry.reap_completed()
    assert _request(lease) == 200
    assert registry.kill_session_tree("lifecycle_preview") == 1
    await _wait_for(lease, alive=False)


@pytest.mark.asyncio
async def test_explicit_stop_terminates_foreground_parent_and_server(runtime, tmp_path):
    shell, registry = runtime
    lease = tmp_path / "foreground-stop.json"
    task = asyncio.create_task(shell.execute_cmd(_command(lease, "hold"), timeout=10))
    await _wait_for(lease, alive=True)
    assert registry.kill_session_tree("lifecycle_preview") == 1
    result = await asyncio.wait_for(task, timeout=5)
    assert result.data.exit_code != 0
    await _wait_for(lease, alive=False)


@pytest.mark.asyncio
async def test_completed_commands_release_ownership_handles(runtime, tmp_path):
    shell, registry = runtime
    for _ in range(10):
        result = await shell.execute_cmd("echo complete", cwd=str(tmp_path))
        assert result.data.exit_code == 0
    for _ in range(60):
        registry.reap_completed()
        if not registry._owners and not registry._processes:
            break
        await asyncio.sleep(0.05)
    assert not registry._owners
    assert not registry._processes


@pytest.mark.asyncio
async def test_legacy_registration_still_stops_a_real_process(runtime):
    _, registry = runtime
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **options,
    )
    registry.register("lifecycle_preview", process)
    try:
        assert registry.kill_session("lifecycle_preview") == 1
        await asyncio.wait_for(process.wait(), timeout=5)
        assert process.returncode != 0
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


@pytest.mark.asyncio
async def test_cancel_during_background_grace_reclaims_server(runtime, tmp_path):
    shell, _ = runtime
    lease = tmp_path / "background-cancel.json"
    task = asyncio.create_task(shell.execute_cmd_background(_command(lease, "server"), grace=10))
    await _wait_for(lease, alive=True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _wait_for(lease, alive=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("abrupt", [False, True])
async def test_sdk_exit_reclaims_owned_preview_without_session_id(runtime, tmp_path, abrupt):
    lease = tmp_path / "sdk-exit.json"
    mode = "sdk-hold" if abrupt else "sdk-exit"
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    host = subprocess.Popen(
        [sys.executable, str(HELPER), mode, str(lease)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **options,
    )
    try:
        if abrupt:
            for _ in range(400):
                if _request(lease) == 200:
                    break
                if host.poll() is not None:
                    pytest.fail("SDK host exited before its preview was ready")
                await asyncio.sleep(0.05)
            assert _request(lease) == 200
            host.kill()
        await asyncio.wait_for(asyncio.to_thread(host.wait), timeout=30)
        assert lease.exists(), "SDK host should launch a preview before exiting"
        if not abrupt:
            assert host.returncode == 0
        await _wait_for(lease, alive=False)
    finally:
        if host.poll() is None:
            host.kill()
        await asyncio.to_thread(host.wait)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX shell background syntax")
async def test_redirected_shell_background_preview_is_retained(runtime, tmp_path):
    shell, registry = runtime
    lease = tmp_path / "ampersand.json"
    command = _command(lease, "server") + " </dev/null >/dev/null 2>&1 &"
    result = await shell.execute_cmd(command, cwd=str(tmp_path))
    assert result.data.exit_code == 0
    await _wait_for(lease, alive=True)
    await asyncio.sleep(2.5)
    registry.reap_completed()
    assert _request(lease) == 200
    registry.kill_session_tree("lifecycle_preview")
    await _wait_for(lease, alive=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX controlling terminal and process groups")
async def test_owned_commands_do_not_inherit_the_sdk_controlling_terminal(tmp_path):
    helper = Path(__file__).with_name("_terminal_session.py")
    host = await asyncio.create_subprocess_exec(
        sys.executable, str(helper), str(tmp_path), start_new_session=True,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await asyncio.wait_for(host.communicate(), timeout=60)
        assert host.returncode == 0, (output + error).decode("utf-8", errors="replace")
        assert {path.stem for path in tmp_path.glob("*.json")} == {"invoke", "stream", "background"}
    finally:
        if host.returncode is None:
            os.killpg(host.pid, signal.SIGKILL)
        await asyncio.wait_for(host.wait(), timeout=5)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX sessions cannot share process groups")
async def test_a_guardian_in_another_session_cannot_accept_command_siblings():
    guardian = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)", start_new_session=True,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        with pytest.raises(PermissionError):
            await asyncio.create_subprocess_exec(
                sys.executable, "-c", "pass", process_group=guardian.pid,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
    finally:
        if guardian.returncode is None:
            guardian.kill()
        await asyncio.wait_for(guardian.wait(), timeout=5)
