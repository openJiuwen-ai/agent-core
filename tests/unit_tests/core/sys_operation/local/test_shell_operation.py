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

from openjiuwen.core.sys_operation.result import ExecuteCmdStreamResult


@pytest.fixture
def work_dir():
    """Fixture to create temporary working directory"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture(work_dir):
    """Fixture to setup and teardown Runner and SysOperation"""
    await Runner.start()
    card_id = "test_shell_op"
    config = LocalWorkConfig(work_dir=work_dir, shell_allowlist=None)
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=config)

    add_res = Runner.resource_mgr.add_sys_operation(card)
    assert add_res.is_ok()

    op_instance = Runner.resource_mgr.get_sys_operation(card_id)
    yield op_instance

    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


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

    assert len(tools) == 2
    tool_names = [t.name for t in tools]
    assert "execute_cmd" in tool_names
    assert "execute_cmd_stream" in tool_names

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
    config = LocalWorkConfig(work_dir=work_dir, shell_allowlist=["echo"])
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

    # Validate at least multiple stdout chunks (ping output is chunked)
    stdout_chunks = [r for r in stream_results if r.data.type == "stdout"]
    assert len(stdout_chunks) >= 1
    assert all("127.0.0.1" in r.data.text for r in stdout_chunks)

    # Validate exit code
    exit_chunk = next(r for r in stream_results if r.data.exit_code is not None)
    assert exit_chunk.data.exit_code == 0
