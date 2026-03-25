# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation.result import ExecuteCmdStreamResult


@pytest.mark.asyncio
async def test_shell_basic_execution(local_op):
    """Test basic shell commands through sandbox routing with local fake providers."""
    res = await local_op.shell().execute_cmd(command="echo hello world")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert "hello world" in res.data.stdout.strip()
    assert res.data.exit_code == 0
    assert res.data.command == "echo hello world"

    res = await local_op.shell().execute_cmd(command="ls -la")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert "file1.txt" in res.data.stdout
    assert res.data.exit_code == 0


@pytest.mark.asyncio
async def test_shell_environment_variables(local_op):
    """Test environment variable injection for shell execution."""
    env = {"TEST_VAR": "custom_value"}
    res = await local_op.shell().execute_cmd(command="echo $TEST_VAR", environment=env)

    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert "custom_value" in res.data.stdout.strip()


@pytest.mark.asyncio
async def test_shell_cwd(local_op):
    """Test explicit cwd routing for shell execution."""
    res = await local_op.shell().execute_cmd(command="pwd", cwd="/tmp/subdir")

    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert res.data.cwd == "/tmp/subdir"
    assert "/tmp/subdir" in res.data.stdout.strip()


@pytest.mark.asyncio
async def test_shell_timeout(local_op):
    """Test command timeout behavior."""
    res = await local_op.shell().execute_cmd(command='python -c "import time; time.sleep(5)"', timeout=1)

    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message.lower()
    assert res.data is not None
    assert res.data.exit_code == -1
    assert res.data is not None
    assert res.data.exit_code == -1


@pytest.mark.asyncio
async def test_shell_ping_timeout(local_op):
    """Test timeout behavior for a continuous-output style command."""
    res = await local_op.shell().execute_cmd(command="ping 127.0.0.1", timeout=1)

    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message.lower()
    assert res.data is not None
    assert "127.0.0.1" in res.data.stdout
    assert res.data.exit_code == -1


@pytest.mark.asyncio
async def test_shell_list_tools(local_op):
    """Test tool metadata exposure for shell operation."""
    tools = local_op.shell().list_tools()

    assert len(tools) == 2
    tool_names = [tool.name for tool in tools]
    assert "execute_cmd" in tool_names
    assert "execute_cmd_stream" in tool_names

    exec_tool = next(tool for tool in tools if tool.name == "execute_cmd")
    assert "command" in exec_tool.input_params["properties"]
    assert exec_tool.input_params["required"] == ["command"]


@pytest.mark.asyncio
async def test_execute_cmd_stream_basic(local_op):
    """Test stdout/stderr chunking and final exit chunk for shell stream execution."""
    cmd = "echo chunk1; echo chunk2; echo error_chunk 1>&2"
    stream_results = []
    async for result in local_op.shell().execute_cmd_stream(command=cmd):
        stream_results.append(result)

    assert len(stream_results) > 0
    assert all(isinstance(result, ExecuteCmdStreamResult) for result in stream_results)

    stdout_chunks = [result.data for result in stream_results if
                     result.data.type == "stdout" and result.data.exit_code is None]
    stderr_chunks = [result.data for result in stream_results if result.data.type == "stderr"]
    exit_chunk = next((result.data for result in stream_results if result.data.exit_code is not None), None)

    stdout_content = "".join(chunk.text for chunk in stdout_chunks)
    assert "chunk1" in stdout_content
    assert "chunk2" in stdout_content
    assert len(stderr_chunks) >= 1
    assert "error_chunk" in stderr_chunks[0].text
    assert exit_chunk is not None
    assert exit_chunk.exit_code == 0
    assert exit_chunk.chunk_index == len(stream_results) - 1


@pytest.mark.asyncio
async def test_execute_cmd_stream_timeout(local_op):
    """Test timeout errors returned by shell stream execution."""
    stream_results = []
    async for result in local_op.shell().execute_cmd_stream(command="sleep 10", timeout=1):
        stream_results.append(result)

    assert len(stream_results) == 1
    error_result = stream_results[0]
    assert error_result.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in error_result.message.lower()
    assert error_result.data.exit_code == -1


@pytest.mark.asyncio
async def test_execute_cmd_stream_empty_command(local_op):
    """Test validation error for empty shell stream command."""
    stream_results = []
    async for result in local_op.shell().execute_cmd_stream(command=""):
        stream_results.append(result)

    assert len(stream_results) == 1
    error_result = stream_results[0]
    assert error_result.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "command can not be empty" in error_result.message
    assert error_result.data.chunk_index == 0
    assert error_result.data.exit_code == -1


@pytest.mark.asyncio
async def test_execute_cmd_stream_continuous_output(local_op):
    """Test continuous-output style command streaming."""
    stream_results = []
    async for result in local_op.shell().execute_cmd_stream(command="ping -c 3 127.0.0.1", timeout=10):
        stream_results.append(result)

    stdout_chunks = [result for result in stream_results if
                     result.data.type == "stdout" and result.data.exit_code is None]
    assert len(stdout_chunks) >= 1
    combined_stdout = "".join(result.data.text for result in stdout_chunks)
    assert "127.0.0.1" in combined_stdout

    exit_chunk = next(result for result in stream_results if result.data.exit_code is not None)
    assert exit_chunk.data.exit_code == 0
