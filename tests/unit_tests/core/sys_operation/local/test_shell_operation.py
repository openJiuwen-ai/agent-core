# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import pathlib
import platform
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig


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
    assert subdir == res.data.stdout.strip()

    # default workdir
    res = await sys_op.shell().execute_cmd(command=cmd)
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
    assert "timed out" in res.message


@pytest.mark.asyncio
async def test_shell_ping_timeout(sys_op):
    """Verify ping command timeout specifically (continuous output)."""
    if platform.system() == "Windows":
        cmd_ping = "ping 127.0.0.1"
    else:
        cmd_ping = "ping 127.0.0.1"

    res = await sys_op.shell().execute_cmd(command=cmd_ping, timeout=1)

    assert res.code == StatusCode.SYS_OPERATION_SHELL_EXECUTION_ERROR.code
    assert "timed out" in res.message
    # Verify partial data is captured
    assert res.data is not None
    assert res.data.stdout  # Ping usually outputs something within 1s
    assert "127.0.0.1" in res.data.stdout


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
