# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration tests for the enhanced BashTool."""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.tools import BashTool


# ── fixtures ──────────────────────────────────────────────────



@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card_id = "test_bash_tool_op"
    card = SysOperationCard(
        id=card_id, mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(shell_allowlist=[]),
    )
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest_asyncio.fixture(name="sys_op_sandboxed")
async def sys_op_sandboxed_fixture():
    await Runner.start()
    workspace = tempfile.mkdtemp()
    card_id = "test_bash_tool_sandboxed_op"
    card = SysOperationCard(
        id=card_id, mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(),
    )
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op, workspace
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    shutil.rmtree(workspace, ignore_errors=True)
    await Runner.stop()


# ── basic execution ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_echo(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo hello"})
    assert res.success is True
    assert "hello" in res.data["stdout"]
    assert res.data["exit_code"] == 0
    assert res.error is None


@pytest.mark.asyncio
async def test_exit_1_is_error(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo fail && exit 1"})
    assert res.success is False
    assert res.data["exit_code"] == 1


# ── semantic exit codes ───────────────────────────────────────

@pytest.mark.asyncio
async def test_grep_no_match_is_not_error(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo hello | grep nonexistent_pattern_xyz"})
    assert res.success is True
    assert res.data["exit_code"] == 1
    assert res.data["return_code_interpretation"] == "No matches found"


@pytest.mark.asyncio
async def test_grep_match_success(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo hello | grep hello"})
    assert res.success is True
    assert res.data["exit_code"] == 0
    assert "hello" in res.data["stdout"]


# ── silent command detection ──────────────────────────────────

@pytest.mark.asyncio
async def test_silent_flag(sys_op) -> None:
    tool = BashTool(sys_op)
    workspace = tempfile.mkdtemp()
    try:
        res = await tool.invoke({"command": f"mkdir -p {workspace}/sub"})
        assert res.success is True
        assert res.data["no_output_expected"] is True
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# ── destructive warning ──────────────────────────────────────

@pytest.mark.asyncio
async def test_destructive_warning_present(sys_op) -> None:
    tool = BashTool(sys_op)
    # git commit --amend on a non-git dir will fail, but the warning should still be in data
    res = await tool.invoke({"command": "git commit --amend -m test"})
    assert res.data["destructive_warning"] is not None
    assert "rewrite" in res.data["destructive_warning"].lower()


# ── injection blocked ────────────────────────────────────────

@pytest.mark.asyncio
async def test_injection_backtick_blocked(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo `whoami`"})
    assert res.success is False
    assert "injection" in res.error.lower()


@pytest.mark.asyncio
async def test_injection_dollar_paren_blocked(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo $(id)"})
    assert res.success is False


# ── workspace sandbox ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_workdir_nonexistent_dir_fails(sys_op_sandboxed) -> None:
    """BashTool no longer enforces sandbox; non-existent workdir simply fails at shell level."""
    op, workspace = sys_op_sandboxed
    tool = BashTool(op)
    missing = os.path.join(workspace, "definitely_does_not_exist_xyz")
    res = await tool.invoke({"command": "echo hi", "workdir": missing})
    assert res.success is False
    assert res.error is not None


# ── output truncation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_smart_truncation(sys_op) -> None:
    tool = BashTool(sys_op)
    py = "python" if os.name == "nt" else "python3"
    res = await tool.invoke({
        "command": f'{py} -c "print(\'x\' * 500)"',
        "max_output_chars": 250,
    })
    assert res.success is True
    assert "lines omitted" in res.data["stdout"]


@pytest.mark.asyncio
async def test_no_truncation_within_limit(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo hello", "max_output_chars": 8000})
    assert res.success is True
    assert "omitted" not in res.data["stdout"]


# ── background execution ─────────────────────────────────────

@pytest.mark.asyncio
async def test_background_pid(sys_op) -> None:
    tool = BashTool(sys_op)
    cmd = "ping -n 5 127.0.0.1 > nul" if os.name == "nt" else "sleep 5"
    res = await tool.invoke({"command": cmd, "run_in_background": True})
    assert res.success is True
    assert isinstance(res.data["pid"], int)
    assert res.data["pid"] > 0


# ── description parameter ────────────────────────────────────

@pytest.mark.asyncio
async def test_description_accepted(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo ok", "description": "Check connectivity"})
    assert res.success is True


# ── permission modes ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_only_mode_allows_read(sys_op) -> None:
    tool = BashTool(sys_op, permission_mode="read_only")
    res = await tool.invoke({"command": "ls -la"})
    assert res.success is True


@pytest.mark.asyncio
async def test_read_only_mode_blocks_write(sys_op) -> None:
    tool = BashTool(sys_op, permission_mode="read_only")
    res = await tool.invoke({"command": "touch /tmp/test_file"})
    assert res.success is False
    assert "Read-only" in res.error


@pytest.mark.asyncio
async def test_accept_edits_mode_allows_file_ops(sys_op) -> None:
    tool = BashTool(sys_op, permission_mode="accept_edits")
    workspace = tempfile.mkdtemp()
    try:
        res = await tool.invoke({"command": f"mkdir -p {workspace}/sub"})
        assert res.success is True
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.mark.asyncio
async def test_deny_patterns(sys_op) -> None:
    tool = BashTool(sys_op, deny_patterns=[r"\bsudo\b"])
    res = await tool.invoke({"command": "sudo echo hi"})
    assert res.success is False
    assert "denied" in res.error.lower()


@pytest.mark.asyncio
async def test_allow_patterns_override(sys_op) -> None:
    tool = BashTool(sys_op, permission_mode="read_only", allow_patterns=[r"^echo\s.*&&\s*mkdir"])
    # mkdir is not read-only, but allow_pattern overrides read_only mode
    res = await tool.invoke({"command": "echo ok && mkdir -p /tmp/_test_perm_override"})
    assert res.success is True
    assert res.data is not None
    assert "Read-only" not in (res.error or "")


# ── large output persistence ──────────────────────────────────

@pytest.mark.asyncio
async def test_large_output_persisted(sys_op) -> None:
    tool = BashTool(sys_op)
    py = "python" if os.name == "nt" else "python3"
    res = await tool.invoke({
        "command": f'{py} -c "print(\'x\' * 50000)"',
        "max_output_chars": 1000,
    })
    assert res.success is True
    assert res.data["persisted_output_path"] is not None
    assert res.data["persisted_output_size"] > 0
    # verify the file exists and has content
    assert os.path.isfile(res.data["persisted_output_path"])


@pytest.mark.asyncio
async def test_small_output_not_persisted(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo hello"})
    assert res.success is True
    assert res.data["persisted_output_path"] is None
    assert res.data["persisted_output_size"] is None


# ── empty command ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_command(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": ""})
    assert res.success is False
    assert "empty" in res.error
