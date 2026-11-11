# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end tests for the yuanrong shell-execution paths.

Gated on ``RUN_YUANRONG_TEST=1``.
"""
from __future__ import annotations

import os
import uuid
from typing import AsyncIterator, Optional

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import ContainerScope, PreDeployLauncherConfig, SandboxIsolationConfig
from openjiuwen.core.sys_operation.result import ExecuteCmdStreamResult
from openjiuwen.core.sys_operation.sandbox.gateway.gateway_client import SandboxGatewayClient

requires_yuanrong = pytest.mark.skipif(
    os.environ.get("RUN_YUANRONG_TEST") != "1",
    reason="Requires running YuanRong cluster",
)


def _stdout_tail(stdout: Optional[str], n: int = 1) -> str:
    """YuanRong execute may prepend banner lines; use trailing non-empty lines as payload."""
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-n:]) if n > 1 else lines[-1]


def _base_url() -> str:
    return os.environ.get("YUANRONG_TEST_BASE_URL", "http://127.0.0.1:8080")


def _executor() -> str:
    value = os.environ.get("YUANRONG_TEST_EXECUTOR", "default").strip().lower()
    return value if value in {"default", "docker"} else "default"


def _extra_params() -> dict:
    params: dict = {"executor": _executor()}
    if _executor() == "docker":
        image = os.environ.get("YUANRONG_TEST_IMAGE")
        if image:
            params["image"] = image
    return params


async def _remove_sys_operation_with_sandbox_release(sys_operation_id: str) -> None:
    sys_op = Runner.resource_mgr.get_sys_operation(sys_operation_id)
    if sys_op is not None and sys_op.isolation_key_template:
        try:
            await SandboxGatewayClient.release(sys_op.isolation_key_template, on_stop="delete")
        except Exception as exc:
            if "not found" not in str(exc).lower():
                raise
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=sys_operation_id)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture() -> AsyncIterator[SysOperation]:
    await Runner.start()
    card_id = f"yuanrong_shell_op_{uuid.uuid4().hex[:8]}"
    card = SysOperationCard(
        id=card_id,
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(
                container_scope=ContainerScope.CUSTOM,
                custom_id=card_id,
            ),
            launcher_config=PreDeployLauncherConfig(
                base_url=_base_url(),
                sandbox_type="yuanrong",
                idle_ttl_seconds=600,
                extra_params=_extra_params(),
            ),
            timeout_seconds=30,
        ),
    )
    add_res = Runner.resource_mgr.add_sys_operation(card)
    assert add_res.is_ok()
    try:
        yield Runner.resource_mgr.get_sys_operation(card_id)
    finally:
        await _remove_sys_operation_with_sandbox_release(card_id)
        await Runner.stop()


@pytest.mark.asyncio
@requires_yuanrong
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
@requires_yuanrong
async def test_shell_environment_variables(sys_op):
    env = {"TEST_VAR": "custom_value"}
    res = await sys_op.shell().execute_cmd(command="echo $TEST_VAR", environment=env)
    assert res.code == StatusCode.SUCCESS.code
    assert "custom_value" in res.data.stdout.strip()


@pytest.mark.asyncio
@requires_yuanrong
async def test_shell_cwd(sys_op):
    await sys_op.shell().execute_cmd(command="mkdir -p /tmp/yuanrong_shell_cwd/subdir")
    res = await sys_op.shell().execute_cmd(command="pwd", cwd="/tmp/yuanrong_shell_cwd/subdir")
    assert res.code == StatusCode.SUCCESS.code
    assert _stdout_tail(res.data.stdout) == "/tmp/yuanrong_shell_cwd/subdir"


@pytest.mark.asyncio
@requires_yuanrong
async def test_shell_timeout(sys_op):
    res = await sys_op.shell().execute_cmd(command="python3 -c \"import time; time.sleep(5)\"", timeout=1)
    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timeout" in res.message.lower()


@pytest.mark.asyncio
@requires_yuanrong
async def test_shell_list_tools(sys_op):
    tools = sys_op.shell().list_tools()
    assert len(tools) == 3
    tool_names = [tool.name for tool in tools]
    assert "execute_cmd" in tool_names
    assert "execute_cmd_stream" in tool_names
    assert "execute_cmd_background" in tool_names


@pytest.mark.asyncio
@requires_yuanrong
async def test_execute_cmd_stream_basic(sys_op):
    cmd = "echo chunk1; echo chunk2; echo error_chunk 1>&2"
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


@pytest.mark.asyncio
@requires_yuanrong
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
@requires_yuanrong
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
@requires_yuanrong
async def test_execute_cmd_empty_command(sys_op):
    res = await sys_op.shell().execute_cmd(command="   ")
    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "command can not be empty" in res.message
