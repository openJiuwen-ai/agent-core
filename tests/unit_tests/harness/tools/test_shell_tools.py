# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.tools.bash import BashTool


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card_id = "test_shell_tools_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=[]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest_asyncio.fixture(name="sys_op_sandboxed")
async def sys_op_sandboxed_fixture():
    """SysOperation with work_dir set; yields (op, workspace_path)."""
    await Runner.start()
    workspace = tempfile.mkdtemp()
    card_id = "test_shell_sandboxed_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        work_dir=workspace
    ))
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op, workspace
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    shutil.rmtree(workspace, ignore_errors=True)
    await Runner.stop()


@pytest.fixture
def tmp_workspace():
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path, ignore_errors=True)


# ────────────────────────────────────────────────────────────
# Original tests
# ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bash_tool(sys_op):
    bash_tool = BashTool(sys_op)

    bash_res = await bash_tool.invoke({"command": "echo 你好"})
    assert bash_res.success is True
    assert bash_res.data["exit_code"] == 0
    assert bash_res.data["stderr"] == ""
    assert "你好" in bash_res.data["stdout"]
    assert bash_res.error is None


@pytest.mark.asyncio
async def test_bash_tool_ls_chinese_filename(sys_op, tmp_workspace):
    import pathlib
    bash_tool = BashTool(sys_op)

    test_dir = pathlib.Path(tmp_workspace) / "test_chinese_files"
    test_dir.mkdir(exist_ok=True)

    try:
        (test_dir / "测试文件.txt").write_text("test content", encoding="utf-8")
        (test_dir / "中文文件 - 副本.txt").write_text("test content", encoding="utf-8")

        ls_res = await bash_tool.invoke({"command": f"ls -la \"{test_dir}\""})
        assert ls_res.success is True
        assert ls_res.data["exit_code"] == 0
        assert "测试文件.txt" in ls_res.data["stdout"]
        assert "中文文件 - 副本.txt" in ls_res.data["stdout"]
        assert ls_res.error is None
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_bash_tool_fail_command(sys_op):
    bash_tool = BashTool(sys_op)

    fail_res = await bash_tool.invoke({"command": "echo fail && exit 1"})
    assert fail_res.success is False
    assert fail_res.data["exit_code"] == 1


@pytest.mark.asyncio
async def test_bash_tool_allowlist(sys_op):
    card_id = "test_shell_allowlist_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=["echo"]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    restricted_op = Runner.resource_mgr.get_sys_operation(card_id)
    bash_tool = BashTool(restricted_op)

    ok_res = await bash_tool.invoke({"command": "echo ok"})
    assert ok_res.success is True

    blocked_res = await bash_tool.invoke({"command": "whoami"})
    assert blocked_res.success is False
    assert blocked_res.error is not None
    assert blocked_res.data is None

    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)


# ────────────────────────────────────────────────────────────
# Dangerous command interception
# ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("command,label", [
    ("rm -rf /tmp/foo", "rm -rf"),
    ("shutdown -h now", "shutdown"),
    ("reboot", "reboot"),
    ("diskpart", "diskpart"),
    ("mkfs.ext4 /dev/sda", "mkfs"),
    ("reg delete HKLM\\Software\\Test", "reg delete"),
    ("Remove-Item C:\\foo -Recurse -Force", "Remove-Item -Recurse -Force"),
])
async def test_dangerous_command_blocked(sys_op, command, label):
    bash_tool = BashTool(sys_op)
    res = await bash_tool.invoke({"command": command})
    assert res.success is False
    assert res.data is None
    assert "safety" in res.error
    assert label in res.error


# ────────────────────────────────────────────────────────────
# Workdir sandbox
# ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_workdir_valid_absolute_subdir(sys_op_sandboxed):
    op, workspace = sys_op_sandboxed
    subdir = os.path.join(workspace, "sub")
    os.makedirs(subdir)
    bash_tool = BashTool(op)

    cmd = "cd" if os.name == "nt" else "pwd"
    res = await bash_tool.invoke({"command": cmd, "workdir": subdir})
    assert res.success is True


@pytest.mark.asyncio
async def test_workdir_valid_relative_subdir(sys_op_sandboxed):
    op, workspace = sys_op_sandboxed
    os.makedirs(os.path.join(workspace, "sub"))
    bash_tool = BashTool(op)

    cmd = "cd" if os.name == "nt" else "pwd"
    res = await bash_tool.invoke({"command": cmd, "workdir": "sub"})
    assert res.success is True


@pytest.mark.asyncio
async def test_workdir_escape_blocked(sys_op_sandboxed):
    op, workspace = sys_op_sandboxed
    bash_tool = BashTool(op)

    escape_path = os.path.join(workspace, "..", "..", "etc")
    res = await bash_tool.invoke({"command": "echo hi", "workdir": escape_path})
    assert res.success is False
    assert "sandbox" in res.error
    assert res.data is None


@pytest.mark.asyncio
async def test_workdir_explicit_workspace_constructor(sys_op, tmp_workspace):
    """workspace passed to BashTool() takes effect as sandbox root."""
    bash_tool = BashTool(sys_op, workspace=tmp_workspace)

    escape_path = os.path.join(tmp_workspace, "..", "..", "etc")
    res = await bash_tool.invoke({"command": "echo hi", "workdir": escape_path})
    assert res.success is False
    assert "sandbox" in res.error


# ────────────────────────────────────────────────────────────
# Background execution
# ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_background_returns_pid(sys_op):
    bash_tool = BashTool(sys_op)
    cmd = "ping -n 5 127.0.0.1 > nul" if os.name == "nt" else "sleep 5"
    res = await bash_tool.invoke({"command": cmd, "background": True})
    assert res.success is True
    assert res.data["status"] == "started"
    assert isinstance(res.data["pid"], int)
    assert res.data["pid"] > 0


@pytest.mark.asyncio
async def test_background_fast_fail_detected(sys_op):
    bash_tool = BashTool(sys_op)
    # exit 1 terminates immediately with non-zero, should be caught within grace period
    res = await bash_tool.invoke({"command": "exit 1", "background": True})
    assert res.success is False
    assert res.error is not None
    assert res.data is None


# ────────────────────────────────────────────────────────────
# Output truncation
# ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_output_truncated_when_over_limit(sys_op):
    bash_tool = BashTool(sys_op)
    # python must be available since we're running in a Python test environment
    py = "python" if os.name == "nt" else "python3"
    res = await bash_tool.invoke({
        "command": f'{py} -c "print(\'x\' * 500)"',
        "max_output_chars": 250,
    })
    assert res.success is True
    assert "lines omitted" in res.data["stdout"]


@pytest.mark.asyncio
async def test_output_not_truncated_within_limit(sys_op):
    bash_tool = BashTool(sys_op)
    res = await bash_tool.invoke({"command": "echo hello", "max_output_chars": 8000})
    assert res.success is True
    assert "[truncated]" not in res.data["stdout"]


@pytest.mark.asyncio
async def test_max_output_chars_clamped_to_minimum(sys_op):
    """max_output_chars below 200 should be silently clamped to 200."""
    bash_tool = BashTool(sys_op)
    res = await bash_tool.invoke({"command": "echo hi", "max_output_chars": 1})
    assert res.success is True
    # should not crash; output is short so no truncation
    assert "hi" in res.data["stdout"]
