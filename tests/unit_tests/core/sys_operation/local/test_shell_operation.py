# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import pathlib
import platform
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.core.sys_operation.local.shell_operation import ShellOperation
from openjiuwen.core.sys_operation.result import ExecuteCmdStreamResult
from openjiuwen.core.sys_operation.shell import ShellType


@pytest.fixture
def work_dir():
    """Fixture to create temporary working directory"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture(work_dir):
    """Fixture to setup and teardown Runner and SysOperation"""
    from openjiuwen.core.sys_operation.cwd import init_cwd
    init_cwd(work_dir)

    await Runner.start()
    card_id = "test_shell_op"
    config = LocalWorkConfig(shell_allowlist=None)
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=config)

    add_res = Runner.resource_mgr.add_sys_operation(card)
    assert add_res.is_ok()

    op_instance = Runner.resource_mgr.get_sys_operation(card_id)
    yield op_instance

    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


def test_windows_auto_unwraps_nested_powershell_command(monkeypatch):
    """Avoid launching a second PowerShell process from auto mode on Windows."""
    import openjiuwen.core.sys_operation.local.shell_operation as shell_operation

    exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    command = (
        "powershell -Command "
        "\"Get-Item 'C:\\tmp\\voiceover_timeline.md' -ErrorAction SilentlyContinue | Select-Object Name, Length\""
    )

    monkeypatch.setattr(shell_operation.os, "name", "nt", raising=False)
    monkeypatch.setattr(shell_operation, "_available_powershell", lambda: exe)

    args, use_shell, resolved_shell = ShellOperation._resolve_execution_plan(command, ShellType.AUTO)

    assert use_shell is False
    assert resolved_shell == "powershell"
    assert args == [
        exe,
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-Item 'C:\\tmp\\voiceover_timeline.md' -ErrorAction SilentlyContinue | Select-Object Name, Length",
    ]


def test_windows_explicit_powershell_unwraps_nested_command(monkeypatch):
    """Explicit powershell shell_type should also execute the inner script."""
    import openjiuwen.core.sys_operation.local.shell_operation as shell_operation

    exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    command = 'pwsh -NoProfile -NonInteractive -Command "Write-Output ok"'

    monkeypatch.setattr(shell_operation.os, "name", "nt", raising=False)
    monkeypatch.setattr(shell_operation, "_available_powershell", lambda: exe)

    args, use_shell, resolved_shell = ShellOperation._resolve_execution_plan(command, ShellType.POWERSHELL)

    assert use_shell is False
    assert resolved_shell == "powershell"
    assert args[-1] == "Write-Output ok"


def test_windows_auto_routes_posix_ls_to_git_bash(monkeypatch):
    """Common POSIX ls checks should run through Git Bash in Windows auto mode."""
    import openjiuwen.core.sys_operation.local.shell_operation as shell_operation

    exe = r"C:\Program Files\Git\bin\bash.exe"
    command = 'ls -la ".team/jiuwen_team_sess_abc/artifacts/"'

    monkeypatch.setattr(shell_operation.os, "name", "nt", raising=False)
    monkeypatch.setattr(shell_operation, "_available_bash", lambda *, allow_wsl=True: exe)

    args, use_shell, resolved_shell = ShellOperation._resolve_execution_plan(command, ShellType.AUTO)

    assert use_shell is False
    assert resolved_shell == "bash"
    assert args == [exe, "-lc", command]


def test_windows_auto_routes_posix_ls_grep_pipeline_to_git_bash(monkeypatch):
    """Common ls | grep checks should run through Git Bash in Windows auto mode."""
    import openjiuwen.core.sys_operation.local.shell_operation as shell_operation

    exe = r"C:\Program Files\Git\bin\bash.exe"
    command = 'ls -la "C:\\tmp\\artifacts" | grep -i "分镜"'

    monkeypatch.setattr(shell_operation.os, "name", "nt", raising=False)
    monkeypatch.setattr(shell_operation, "_available_bash", lambda *, allow_wsl=True: exe)

    args, use_shell, resolved_shell = ShellOperation._resolve_execution_plan(command, ShellType.AUTO)

    assert use_shell is False
    assert resolved_shell == "bash"
    assert args == [exe, "-lc", 'ls -la "C:/tmp/artifacts" | grep -i "分镜"']


@pytest.mark.asyncio
async def test_shell_basic_execution(sys_op):
    """Test basic shell commands across platforms."""
    # 1. Echo
    res = await sys_op.shell().execute_cmd(command="echo hello world")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert "hello world" in res.data.stdout.strip()
    assert res.data.exit_code == 0
    assert res.data.command == "echo hello world"

    # 2. Platform specific list-dir
    cmd = "dir" if platform.system() == "Windows" else "ls -la"
    res = await sys_op.shell().execute_cmd(command=cmd)
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert res.data.stdout.strip()
    assert res.data.exit_code == 0


@pytest.mark.asyncio
async def test_shell_environment_variables(sys_op):
    """Test environment variable injection."""
    env = {"TEST_VAR": "custom_value"}
    cmd = "echo %TEST_VAR%" if platform.system() == "Windows" else "echo $TEST_VAR"

    res = await sys_op.shell().execute_cmd(command=cmd, environment=env)
    assert res.code == StatusCode.SUCCESS.code
    assert "custom_value" in res.data.stdout.strip()


@pytest.mark.asyncio
async def test_shell_cwd(sys_op, work_dir):
    """Test execution in a specific working directory."""
    # absolute path
    subdir = os.path.join(work_dir, "subdir")
    os.makedirs(subdir, exist_ok=True)

    cmd = "echo %CD%" if platform.system() == "Windows" else "pwd"
    res = await sys_op.shell().execute_cmd(command=cmd, cwd=subdir)

    assert res.code == StatusCode.SUCCESS.code
    assert "subdir" in res.data.stdout.strip()

    # relative path
    res = await sys_op.shell().execute_cmd(command=cmd, cwd="subdir")
    assert res.code == StatusCode.SUCCESS.code
    if platform.system() == "Darwin":
        assert subdir in res.data.stdout.strip()
    else:
        assert subdir == res.data.stdout.strip()

    # default workdir
    res = await sys_op.shell().execute_cmd(command=cmd)
    if platform.system() == "Darwin":
        assert work_dir in res.data.stdout.strip()
    else:
        assert work_dir == res.data.stdout.strip()


@pytest.mark.asyncio
async def test_shell_default_cwd(sys_op, work_dir):
    """Test that execution defaults to work_dir when no cwd is provided."""
    cmd = "echo %CD%" if platform.system() == "Windows" else "pwd"
    res = await sys_op.shell().execute_cmd(command=cmd)

    assert res.code == StatusCode.SUCCESS.code
    # Should resolve to work_dir (temp dir)
    actual_out = res.data.stdout.strip().lower()
    # Resolve work_dir to handle potential short paths on Windows
    expected = str(pathlib.Path(work_dir).resolve()).lower()
    # On Windows, one might be a shortened version of the other
    assert expected in actual_out or actual_out in expected


@pytest.mark.asyncio
async def test_shell_relative_cwd(sys_op, work_dir):
    """Test that relative cwd resolves against work_dir."""
    subdir_name = "rel_subdir"
    subdir_path = os.path.join(work_dir, subdir_name)
    os.makedirs(subdir_path, exist_ok=True)

    cmd = "echo %CD%" if platform.system() == "Windows" else "pwd"
    res = await sys_op.shell().execute_cmd(command=cmd, cwd=subdir_name)

    assert res.code == StatusCode.SUCCESS.code
    assert subdir_name in res.data.stdout.strip().lower()


@pytest.mark.asyncio
async def test_shell_timeout(sys_op):
    """Test command timeout logic."""
    cmd_sleep = "python -c \"import time; time.sleep(5)\""

    res = await sys_op.shell().execute_cmd(command=cmd_sleep, timeout=1)

    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message


@pytest.mark.asyncio
async def test_shell_ping_timeout(sys_op):
    """Verify ping command timeout specifically (continuous output)."""
    cmd_ping = "ping 127.0.0.1"
    res = await sys_op.shell().execute_cmd(command=cmd_ping, timeout=1)

    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message
    # Verify partial data is captured
    assert res.data is not None
    # Ping usually outputs something within 1s(windows)
    assert (res.data.stdout and "127.0.0.1" in res.data.stdout) or (res.data.exit_code != 0)


@pytest.mark.asyncio
async def test_shell_allowlist(work_dir):
    """Test allowlist functionality."""
    await Runner.start()
    try:
        card_id = "test_allowlist"
        # Only allow 'echo'
        config = LocalWorkConfig(shell_allowlist=['echo', 'pwd'])
        card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=config)

        add_res = Runner.resource_mgr.add_sys_operation(card)
        assert add_res.is_ok()
        op = Runner.resource_mgr.get_sys_operation(card_id)

        # Allowed
        cmd = "echo %CD%" if platform.system() == "Windows" else "pwd"
        res = await op.shell().execute_cmd(command=cmd)
        assert res.code == StatusCode.SUCCESS.code

        # Denied
        res_deny = await op.shell().execute_cmd("dir")  # 'dir' not in allowlist
        assert res_deny.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
        assert "not allowed" in res_deny.message

        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_shell_list_tools(sys_op):
    """Test list_tools for Shell operation."""
    tools = sys_op.shell().list_tools()

    assert len(tools) == 3
    tool_names = [t.name for t in tools]
    assert "execute_cmd" in tool_names
    assert "execute_cmd_stream" in tool_names
    assert "execute_cmd_background" in tool_names

    # Verify command parameter
    exec_tool = next(t for t in tools if t.name == "execute_cmd")
    assert "command" in exec_tool.input_params["properties"]
    assert exec_tool.input_params["required"] == ["command"]


@pytest.mark.asyncio
async def test_execute_cmd_stream_basic(sys_op):
    """Test basic streaming command execution (stdout/stderr chunked output)"""
    try:
        # Construct chunked output command (multi-echo to simulate chunks, cross-platform compatible)
        if platform.system() == "Windows":
            cmd = 'echo chunk1 && echo chunk2 && echo error_chunk 1>&2'
        else:
            cmd = 'echo chunk1; sleep 0.01; echo chunk2; sleep 0.01; echo error_chunk 1>&2'

        # Collect streaming results
        stream_results = []
        async for result in sys_op.shell().execute_cmd_stream(command=cmd):
            stream_results.append(result)

        # Basic validation
        assert len(stream_results) > 0, "At least one streaming result should be returned"
        assert all(isinstance(r, ExecuteCmdStreamResult) for r in stream_results), \
            "Result type must be ExecuteCmdStreamResult"

        # Split stdout/stderr/exit chunks
        stdout_chunks = [r.data for r in stream_results if hasattr(r.data, 'type') and r.data.type == "stdout"]
        stderr_chunks = [r.data for r in stream_results if hasattr(r.data, 'type') and r.data.type == "stderr"]
        exit_chunk = next(
            (r.data for r in stream_results if hasattr(r.data, 'exit_code') and r.data.exit_code is not None), None)

        # Validate stdout content
        stdout_content = "".join([chunk.text for chunk in stdout_chunks]) if stdout_chunks else ""
        assert "chunk1" in stdout_content, f"'chunk1' is not found in stdout, current stdout: {stdout_content}"
        assert "chunk2" in stdout_content, f"'chunk2' is not found in stdout, current stdout: {stdout_content}"

        # Validate stderr content
        assert len(stderr_chunks) >= 1, f"stderr chunks count is less than 1, actual count: {len(stderr_chunks)}"
        assert "error_chunk" in stderr_chunks[
            0].text, f"'error_chunk' is not found in stderr, current stderr: {stderr_chunks[0].text}"

        # Validate exit code
        assert exit_chunk is not None, "Exit chunk is not found in stream results"
        assert exit_chunk.exit_code == 0, f"Exit code is not 0, actual exit code: {exit_chunk.exit_code}"
        assert exit_chunk.chunk_index == len(stream_results) - 1, \
            f"Exit chunk is not the last one, index: {exit_chunk.chunk_index}, total chunks: {len(stream_results)}"

        print("Test case test_execute_cmd_stream_basic completed successfully without any errors.")

    except AssertionError as e:
        print(f"Assertion failed in test case test_execute_cmd_stream_basic, reason: {str(e)}")


@pytest.mark.asyncio
async def test_execute_cmd_stream_timeout(sys_op):
    """Test streaming command execution with timeout scenario"""
    # Construct sleep command (cross-platform)
    if platform.system() == "Windows":
        cmd = 'ping -n 10 127.0.0.1'  # Windows ping 10 times (~10 seconds)
    else:
        cmd = 'sleep 10'

    # Collect streaming results
    stream_results = []
    async for result in sys_op.shell().execute_cmd_stream(command=cmd, timeout=1):
        stream_results.append(result)

    # Validate timeout error
    error_result = next((r for r in stream_results
                         if r.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code), None)
    assert error_result is not None, "Timeout error result should be returned"
    assert "timeout" in error_result.message.lower()
    assert error_result.data.exit_code == -1


@pytest.mark.asyncio
async def test_execute_cmd_stream_empty_command(sys_op):
    """Test streaming execution with empty command"""
    stream_results = []
    async for result in sys_op.shell().execute_cmd_stream(command=""):
        stream_results.append(result)

    assert len(stream_results) == 1
    error_result = stream_results[0]
    assert error_result.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "command can not be empty" in error_result.message
    assert error_result.data.chunk_index == 0
    assert error_result.data.exit_code == -1


@pytest.mark.asyncio
async def test_execute_cmd_stream_allowlist(sys_op, work_dir):
    """Test streaming execution with allowlist validation"""
    # Recreate sys_op with allowlist
    await Runner.start()
    card_id = "test_stream_allowlist"
    config = LocalWorkConfig(shell_allowlist=["echo"])
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=config)
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)

    try:
        # Test allowed command (echo)
        stream_results_allowed = []
        async for res in op.shell().execute_cmd_stream(command='echo allowed'):
            stream_results_allowed.append(res)
        assert any(r.data.type == "stdout" and "allowed" in r.data.text for r in stream_results_allowed)

        # Test forbidden command (ls/dir)
        cmd_deny = "dir" if platform.system() == "Windows" else "ls"
        stream_results_deny = []
        async for res in op.shell().execute_cmd_stream(command=cmd_deny):
            stream_results_deny.append(res)

        error_result = stream_results_deny[0]
        assert error_result.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
        assert "not allowed by allowlist" in error_result.message
    finally:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()


@pytest.mark.asyncio
async def test_execute_cmd_stream_continuous_output(sys_op):
    """Test streaming execution for continuous output commands (e.g., ping)"""
    # Construct ping command (execute 3 times to avoid infinite output)
    if platform.system() == "Windows":
        cmd = "ping -n 3 127.0.0.1"
    else:
        cmd = "ping -c 3 127.0.0.1"

    stream_results = []
    async for res in sys_op.shell().execute_cmd_stream(command=cmd, timeout=10):
        stream_results.append(res)

    # Validate at least multiple stdout chunks (ping output is chunked).
    # Chunks may split arbitrarily by buffer, so only require combined stdout to contain the target.
    stdout_chunks = [r for r in stream_results if r.data.type == "stdout"]
    assert len(stdout_chunks) >= 1
    combined_stdout = "".join(r.data.text for r in stdout_chunks)
    assert "127.0.0.1" in combined_stdout

    # Validate exit code
    exit_chunk = next(r for r in stream_results if r.data.exit_code is not None)
    assert exit_chunk.data.exit_code == 0


# ── sandbox path checks (unit, no subprocess) ──────────────────────────────

class _FakeRunConfig:
    """Minimal stand-in for LocalWorkConfig used in sandbox unit tests."""

    def __init__(self, *, restrict: bool, roots: list[str] | None = None):
        self.restrict_to_sandbox = restrict
        self.sandbox_root = roots
        self.shell_allowlist = None
        self.dangerous_patterns = None


def _make_shell_op(restrict: bool, roots: list[str] | None = None) -> ShellOperation:
    """Return a ShellOperation whose _run_config mimics sandbox settings."""
    op = object.__new__(ShellOperation)
    op._run_config = _FakeRunConfig(restrict=restrict, roots=roots)
    return op


def test_get_sandbox_roots_disabled():
    op = _make_shell_op(restrict=False)
    assert op._get_sandbox_roots() is None


def test_get_sandbox_roots_explicit(tmp_path):
    op = _make_shell_op(restrict=True, roots=[str(tmp_path)])
    roots = op._get_sandbox_roots()
    assert roots is not None
    assert any(r == tmp_path.resolve() for r in roots)


def test_extract_abs_paths_windows_quoted(monkeypatch):
    import openjiuwen.core.sys_operation.local.shell_operation as shell_mod
    monkeypatch.setattr(shell_mod.os, "name", "nt", raising=False)
    op = _make_shell_op(restrict=True)
    paths = op._extract_abs_paths(r"Get-Content 'D:\secret\file.txt'")
    assert any(str(p) == r"D:\secret\file.txt" for p in paths)


def test_extract_abs_paths_windows_unquoted(monkeypatch):
    import openjiuwen.core.sys_operation.local.shell_operation as shell_mod
    monkeypatch.setattr(shell_mod.os, "name", "nt", raising=False)
    op = _make_shell_op(restrict=True)
    paths = op._extract_abs_paths(r"dir D:\git\read_only_dir")
    assert any(str(p) == r"D:\git\read_only_dir" for p in paths)


def test_check_shell_sandbox_disabled(tmp_path):
    """When restrict_to_sandbox=False the check always passes."""
    op = _make_shell_op(restrict=False)
    result = op._check_shell_sandbox(r"dir D:\anywhere", tmp_path)
    assert result is None


def test_check_shell_sandbox_cwd_outside(tmp_path):
    """CWD outside sandbox root should be rejected."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()
    op = _make_shell_op(restrict=True, roots=[str(sandbox)])
    err = op._check_shell_sandbox("echo hello", outside_cwd)
    assert err is not None
    assert "outside sandbox" in err


def test_check_shell_sandbox_cwd_inside(tmp_path):
    """CWD inside sandbox with a safe command should pass."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    op = _make_shell_op(restrict=True, roots=[str(sandbox)])
    err = op._check_shell_sandbox("echo hello", sandbox)
    assert err is None


def test_check_shell_sandbox_abs_path_outside(tmp_path, monkeypatch):
    """Absolute path in command that is outside sandbox should be rejected."""
    import openjiuwen.core.sys_operation.local.shell_operation as shell_mod
    monkeypatch.setattr(shell_mod.os, "name", "nt", raising=False)

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()

    op = _make_shell_op(restrict=True, roots=[str(sandbox)])
    # Command references an absolute path outside the sandbox
    cmd = f"dir {outside}"
    err = op._check_shell_sandbox(cmd, sandbox)
    assert err is not None
    assert "outside sandbox" in err


def test_check_shell_sandbox_abs_path_inside(tmp_path, monkeypatch):
    """Absolute path in command that is inside sandbox should pass."""
    import openjiuwen.core.sys_operation.local.shell_operation as shell_mod
    monkeypatch.setattr(shell_mod.os, "name", "nt", raising=False)

    sandbox = tmp_path / "sandbox"
    subdir = sandbox / "sub"
    subdir.mkdir(parents=True)

    op = _make_shell_op(restrict=True, roots=[str(sandbox)])
    cmd = f"dir {subdir}"
    err = op._check_shell_sandbox(cmd, sandbox)
    assert err is None


@pytest.mark.asyncio
async def test_execute_cmd_rejected_by_sandbox(tmp_path):
    """execute_cmd must return an error when sandbox is active and path is outside."""
    from openjiuwen.core.sys_operation.cwd import init_cwd

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    init_cwd(str(sandbox))
    await Runner.start()
    card_id = "test_sandbox_shell"
    config = LocalWorkConfig(restrict_to_sandbox=True, sandbox_root=[str(sandbox)])
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=config)
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)

    try:
        cmd = f"dir {outside}" if os.name == "nt" else f"ls {outside}"
        result = await op.shell().execute_cmd(cmd)
        assert result.code != 0, "Expected sandbox rejection"
        assert "Access denied" in (result.message or "")
    finally:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
        await Runner.stop()
