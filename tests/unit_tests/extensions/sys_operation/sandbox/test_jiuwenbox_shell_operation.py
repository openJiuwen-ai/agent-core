# coding: utf-8
from __future__ import annotations

import os
from typing import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.result import ExecuteCmdStreamResult


LONG_RUNNING_COMMAND = ["/usr/bin/python3", "-c", "import time; time.sleep(3600)"]


def _normalize_endpoint(endpoint: str) -> str:
    return endpoint if "://" in endpoint else f"http://{endpoint}"


@pytest.fixture
def server_endpoint() -> str:
    return os.environ.get("JIUWENBOX_TEST_SERVER", "127.0.0.1:8321")


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture(server_endpoint, monkeypatch) -> AsyncIterator[SysOperation]:
    base_url = _normalize_endpoint(server_endpoint)
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        create_resp = client.post("/api/v1/sandboxes", json={"command": LONG_RUNNING_COMMAND})
        assert create_resp.status_code == 201, create_resp.text
        sandbox_id = create_resp.json()["id"]

        monkeypatch.setenv("JIUWENBOX_SANDBOX_ID", sandbox_id)
        await Runner.start()
        card_id = f"jiuwenbox_shell_op_{uuid4().hex[:8]}"
        card = SysOperationCard(
            id=card_id,
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(
                    container_scope=ContainerScope.CUSTOM,
                    custom_id=sandbox_id,
                ),
                launcher_config=PreDeployLauncherConfig(
                    base_url=base_url,
                    sandbox_type="jiuwenbox",
                    idle_ttl_seconds=600,
                ),
                timeout_seconds=30,
            ),
        )

        add_res = Runner.resource_mgr.add_sys_operation(card)
        assert add_res.is_ok()
        try:
            yield Runner.resource_mgr.get_sys_operation(card_id)
        finally:
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
            await Runner.stop()
            client.delete(f"/api/v1/sandboxes/{sandbox_id}")
            monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_basic_execution(sys_op):
    res = await sys_op.shell().execute_cmd(command="echo hello world")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert "hello world" in res.data.stdout.strip()
    assert res.data.exit_code == 0
    assert res.data.command == "echo hello world"

    res = await sys_op.shell().execute_cmd(command="ls -la /")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data is not None
    assert res.data.stdout.strip()
    assert res.data.exit_code == 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_environment_variables(sys_op):
    env = {"TEST_VAR": "custom_value"}
    res = await sys_op.shell().execute_cmd(command="echo $TEST_VAR", environment=env)
    assert res.code == StatusCode.SUCCESS.code
    assert "custom_value" in res.data.stdout.strip()


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_cwd(sys_op):
    await sys_op.shell().execute_cmd(command="mkdir -p /tmp/jiuwenbox_shell_cwd/subdir")
    res = await sys_op.shell().execute_cmd(command="pwd", cwd="/tmp/jiuwenbox_shell_cwd/subdir")
    assert res.code == StatusCode.SUCCESS.code
    assert res.data.stdout.strip() == "/tmp/jiuwenbox_shell_cwd/subdir"


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_timeout(sys_op):
    res = await sys_op.shell().execute_cmd(command="python3 -c \"import time; time.sleep(5)\"", timeout=1)
    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message.lower()


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
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
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_shell_list_tools(sys_op):
    tools = sys_op.shell().list_tools()
    assert len(tools) == 3
    tool_names = [tool.name for tool in tools]
    assert "execute_cmd" in tool_names
    assert "execute_cmd_stream" in tool_names
    assert "execute_cmd_background" in tool_names

    exec_tool = next(tool for tool in tools if tool.name == "execute_cmd")
    assert "command" in exec_tool.input_params["properties"]
    assert exec_tool.input_params["required"] == ["command"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_execute_cmd_stream_basic(sys_op):
    cmd = "echo chunk1; sleep 0.01; echo chunk2; sleep 0.01; echo error_chunk 1>&2"
    stream_results = []
    async for result in sys_op.shell().execute_cmd_stream(command=cmd):
        stream_results.append(result)

    assert len(stream_results) > 0
    assert all(isinstance(result, ExecuteCmdStreamResult) for result in stream_results)

    stdout_chunks = [result.data for result in stream_results if result.data.type == "stdout"]
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
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_execute_cmd_stream_timeout(sys_op):
    stream_results = []
    async for result in sys_op.shell().execute_cmd_stream(command="sleep 10", timeout=1):
        stream_results.append(result)

    error_result = next(
        (result for result in stream_results if result.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code),
        None,
    )
    assert error_result is not None
    assert "timeout" in error_result.message.lower()
    assert error_result.data.exit_code == -1


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
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
@pytest.mark.skipif(os.environ.get("RUN_JIUWENBOX_TEST") != "1", reason="Requires running Jiuwenbox sandbox")
async def test_execute_cmd_stream_continuous_output(sys_op):
    cmd = "for i in 1 2 3; do echo 127.0.0.1; sleep 0.1; done"
    stream_results = []
    async for res in sys_op.shell().execute_cmd_stream(command=cmd, timeout=10):
        stream_results.append(res)

    stdout_chunks = [result for result in stream_results if result.data.type == "stdout"]
    assert len(stdout_chunks) >= 1
    combined_stdout = "".join(result.data.text for result in stdout_chunks)
    assert "127.0.0.1" in combined_stdout

    exit_chunk = next(result for result in stream_results if result.data.exit_code is not None)
    assert exit_chunk.data.exit_code == 0
