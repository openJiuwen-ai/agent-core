# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import uuid

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.result import ExecuteCmdStreamResult
from tests.unit_tests.extensions.sys_operation.sandbox.conftest import _require_real_aio_sandbox


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    _require_real_aio_sandbox()
    await Runner.start()
    card_id = f"aio_shell_op_{uuid.uuid4().hex[:8]}"
    card = SysOperationCard(
        id=card_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.SYSTEM),
            launcher_config=PreDeployLauncherConfig(
                base_url="http://localhost:8080",
                sandbox_type="aio",
                idle_ttl_seconds=600,
            ),
            timeout_seconds=30,
        ),
    )

    add_res = Runner.resource_mgr.add_sys_operation(card)
    assert add_res.is_ok()

    op_instance = Runner.resource_mgr.get_sys_operation(card_id)
    yield op_instance

    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest.mark.asyncio
async def test_shell_basic_execution(sys_op):
    res = await sys_op.shell().execute_cmd(command="echo hello world")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert "hello world" in res.data.stdout.strip()
    assert res.data.exit_code == 0
    assert res.data.command == "echo hello world"

    res = await sys_op.shell().execute_cmd(command="ls -la")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert res.data.stdout.strip()
    assert res.data.exit_code == 0


@pytest.mark.asyncio
async def test_shell_environment_variables(sys_op):
    env = {"TEST_VAR": "custom_value"}
    res = await sys_op.shell().execute_cmd(command="echo $TEST_VAR", environment=env)
    assert res.code == StatusCode.SUCCESS.code
    assert "custom_value" in res.data.stdout.strip()


@pytest.mark.asyncio
async def test_shell_cwd(sys_op):
    await sys_op.shell().execute_cmd(command="mkdir -p /tmp/aio_shell_cwd/subdir")
    res = await sys_op.shell().execute_cmd(command="pwd", cwd="/tmp/aio_shell_cwd/subdir")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.stdout.strip() == "/tmp/aio_shell_cwd/subdir"


@pytest.mark.asyncio
async def test_shell_timeout(sys_op):
    res = await sys_op.shell().execute_cmd(command="python -c \"import time; time.sleep(5)\"", timeout=1)
    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message.lower()


@pytest.mark.asyncio
async def test_shell_ping_timeout(sys_op):
    res = await sys_op.shell().execute_cmd(
        command="for i in 1 2 3 4 5 6 7 8 9 10; do echo 127.0.0.1; sleep 1; done",
        timeout=1,
    )
    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message.lower()
    assert res.data is not None
    assert "127.0.0.1" in res.data.stdout


@pytest.mark.asyncio
async def test_shell_list_tools(sys_op):
    tools = sys_op.shell().list_tools()
    assert len(tools) == 2
    tool_names = [t.name for t in tools]
    assert "execute_cmd" in tool_names
    assert "execute_cmd_stream" in tool_names

    exec_tool = next(t for t in tools if t.name == "execute_cmd")
    assert "command" in exec_tool.input_params["properties"]
    assert exec_tool.input_params["required"] == ["command"]


@pytest.mark.asyncio
async def test_execute_cmd_stream_basic(sys_op):
    cmd = "echo chunk1; sleep 0.01; echo chunk2; sleep 0.01; echo error_chunk 1>&2"
    stream_results = []
    async for result in sys_op.shell().execute_cmd_stream(command=cmd):
        stream_results.append(result)

    assert len(stream_results) > 0
    assert all(isinstance(r, ExecuteCmdStreamResult) for r in stream_results)

    stdout_chunks = [r.data for r in stream_results if r.data.type == "stdout"]
    stderr_chunks = [r.data for r in stream_results if r.data.type == "stderr"]
    exit_chunk = next((r.data for r in stream_results if r.data.exit_code is not None), None)

    stdout_content = "".join(chunk.text for chunk in stdout_chunks)
    assert "chunk1" in stdout_content
    assert "chunk2" in stdout_content
    assert len(stderr_chunks) >= 1
    assert "error_chunk" in stderr_chunks[0].text
    assert exit_chunk is not None
    assert exit_chunk.exit_code == 0
    assert exit_chunk.chunk_index == len(stream_results) - 1


@pytest.mark.asyncio
async def test_execute_cmd_stream_timeout(sys_op):
    stream_results = []
    async for result in sys_op.shell().execute_cmd_stream(command="sleep 10", timeout=1):
        stream_results.append(result)

    error_result = next(
        (r for r in stream_results if r.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code),
        None,
    )
    assert error_result is not None
    assert "timeout" in error_result.message.lower()
    assert error_result.data.exit_code == -1


@pytest.mark.asyncio
async def test_execute_cmd_stream_empty_command(sys_op):
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
async def test_execute_cmd_stream_continuous_output(sys_op):
    cmd = "for i in 1 2 3; do echo 127.0.0.1; sleep 0.1; done"
    stream_results = []
    async for res in sys_op.shell().execute_cmd_stream(command=cmd, timeout=10):
        stream_results.append(res)

    stdout_chunks = [r for r in stream_results if r.data.type == "stdout"]
    assert len(stdout_chunks) >= 1
    combined_stdout = "".join(r.data.text for r in stdout_chunks)
    assert "127.0.0.1" in combined_stdout

    exit_chunk = next(r for r in stream_results if r.data.exit_code is not None)
    assert exit_chunk.data.exit_code == 0
