# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration tests for the enhanced BashTool."""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.sys_operation.cwd import set_workspace
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
    assert "hello" in res.data["content"]
    assert res.error is None


@pytest.mark.asyncio
async def test_exit_1_is_error(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo fail && exit 1"})
    assert res.success is False
    assert res.data["content"].startswith("Exit code")


# ── semantic exit codes ───────────────────────────────────────

@pytest.mark.asyncio
async def test_grep_no_match_is_not_error(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo hello | grep nonexistent_pattern_xyz"})
    # grep exits 1 on no match: treated as success, empty merged output.
    assert res.success is True
    assert res.data["content"] == ""


@pytest.mark.asyncio
async def test_grep_match_success(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo hello | grep hello"})
    assert res.success is True
    assert "hello" in res.data["content"]


# ── silent command produces empty content ─────────────────────

@pytest.mark.asyncio
async def test_silent_command_empty_content(sys_op) -> None:
    tool = BashTool(sys_op)
    workspace = tempfile.mkdtemp()
    try:
        res = await tool.invoke({"command": f"mkdir -p {workspace}/sub"})
        assert res.success is True
        assert res.data["content"] == ""
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# ── destructive warning ──────────────────────────────────────

@pytest.mark.asyncio
async def test_destructive_warning_present(sys_op) -> None:
    tool = BashTool(sys_op)
    # Run the amend inside a throwaway repo via workdir so it can never rewrite
    # the real repository HEAD; we only assert the destructive warning surfaces.
    repo = tempfile.mkdtemp()
    try:
        for setup in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@e2e.local"],
            ["git", "config", "user.name", "t"],
            ["git", "commit", "--allow-empty", "-q", "-m", "init"],
        ):
            subprocess.run(
                setup, cwd=repo, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        # git commit --amend triggers a destructive warning, now prepended to content.
        res = await tool.invoke({"command": "git commit --amend -m test", "workdir": repo})
        assert "rewrite" in res.data["content"].lower()
    finally:
        shutil.rmtree(repo, ignore_errors=True)


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
    # large output is persisted and surfaced as a <persisted-output> preview.
    assert "<persisted-output>" in res.data["content"]
    assert "Output too large" in res.data["content"]


@pytest.mark.asyncio
async def test_small_output_not_persisted(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": "echo hello"})
    assert res.success is True
    assert "<persisted-output>" not in res.data["content"]
    assert "hello" in res.data["content"]


# ── timeout surfaces collected output ─────────────────────────

@pytest.mark.asyncio
async def test_timeout_returns_collected_output(sys_op) -> None:
    tool = BashTool(sys_op)
    # echo runs first, then sleep blows the 1s timeout: the kill must not drop
    # the output already collected before it.
    res = await tool.invoke({"command": "echo partial; sleep 5", "timeout": 1})
    assert res.success is False
    assert "partial" in res.data["content"]


# ── empty command ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_command(sys_op) -> None:
    tool = BashTool(sys_op)
    res = await tool.invoke({"command": ""})
    assert res.success is False
    assert "empty" in res.error


# ── history path construction ─────────────────────────────────

class TestBashToolHistoryPath(unittest.TestCase):
    """Unit tests for _build_history_path — no Runner required."""

    def _make_session(self, session_id: str, agent_id: str | None = None) -> MagicMock:
        mock = MagicMock()
        mock.get_session_id.return_value = session_id
        mock.agent_id.return_value = agent_id
        return mock

    def test_path_contains_agent_id_and_session_id(self):
        """History path embeds both agent_id and session_id."""
        session = self._make_session("sess_abc", agent_id="agent_xyz")
        tool = BashTool(MagicMock())
        path = tool._build_history_path(session)
        assert "agent_xyz" in path
        assert "sess_abc" in path
        session.get_session_id.assert_called_once()

    def test_default_agent_id_used_when_none(self):
        """session.agent_id() returning None falls back to 'default'."""
        session = self._make_session("s1", agent_id=None)
        tool = BashTool(MagicMock())
        path = tool._build_history_path(session)
        assert "default" in path

    def test_workspace_path_is_base_dir(self):
        """Workspace ContextVar is used as the base directory."""
        session = self._make_session("s1", agent_id="a")
        workspace = tempfile.mkdtemp()
        try:
            set_workspace(workspace)
            tool = BashTool(MagicMock())
            path = tool._build_history_path(session)
            assert path.startswith(os.path.realpath(workspace))
            assert ".agent_history" in path
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_filename_pattern(self):
        """Filename follows file_ops_{agent_id}_{session_id}.json pattern."""
        session = self._make_session("sess123", agent_id="myagent")
        tool = BashTool(MagicMock())
        path = tool._build_history_path(session)
        filename = os.path.basename(path)
        assert filename == "file_ops_myagent_sess123.json"
