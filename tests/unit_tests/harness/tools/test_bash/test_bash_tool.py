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
from openjiuwen.core.common.exception.errors import ExecutionError
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
async def test_background_running_process_returns_started_without_output(sys_op) -> None:
    tool = BashTool(sys_op)
    cmd = "ping -n 5 127.0.0.1 > nul" if os.name == "nt" else "sleep 5"
    res = await tool.invoke({"command": cmd, "run_in_background": True})
    assert res.success is True
    assert isinstance(res.data["pid"], int)
    assert res.data["pid"] > 0
    assert res.data["status"] == "started"
    assert "stdout" not in res.data
    assert "stderr" not in res.data
    assert "exit_code" not in res.data
    assert "stdout_log" not in res.data
    assert "persisted_output_path" not in res.data


@pytest.mark.asyncio
async def test_background_early_failure_includes_stderr(sys_op) -> None:
    tool = BashTool(sys_op)
    cmd = "python __openjiuwen_missing_script__.py" if os.name == "nt" else "python3 __openjiuwen_missing_script__.py"
    res = await tool.invoke({"command": cmd, "run_in_background": True})
    assert res.success is False
    assert res.data["status"] == "exited"
    assert res.data["exit_code"] not in (None, 0)
    assert res.data["stderr"] or res.data["stdout"]


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


# ── nul-redirect normalization (Windows reserved-name guard) ──

class TestNulRedirectNormalization:
    """CMD-style ``>nul``/``2>nul`` redirects must NOT be silently rewritten for
    bash/sh — MSYS/Git Bash does not treat ``nul`` as a device and would create a
    real, hard-to-delete ``nul`` file. Instead of rewriting (a half parser that
    mis-handles heredocs, ``[[ ]]`` tests, command substitution, and quoted
    patterns), the command is *rejected* up front via
    ``_reject_cmd_null_redirect_for_bash`` with a clear error directing the caller
    to ``/dev/null`` or ``shell_type='cmd'``. The path normalizer no longer touches
    ``nul`` at all.
    """

    @staticmethod
    def _reject():
        from openjiuwen.core.sys_operation.local import shell_operation as _so
        return _so._reject_cmd_null_redirect_for_bash

    @staticmethod
    def _norm():
        from openjiuwen.core.sys_operation.local import shell_operation as _so
        return _so._normalize_windows_paths_for_bash

    @pytest.mark.parametrize("cmd", [
        "echo hi > nul",
        "echo hi >nul",
        "echo hi 2> nul",
        "echo hi 2>nul",
        "echo hi 1>nul",
        "echo hi &>nul",
        "echo hi >>nul",
        "echo hi >NUL",
        "ping -n 5 127.0.0.1 > nul",
    ])
    def test_cmd_null_redirect_rejected(self, cmd):
        with pytest.raises(ExecutionError):
            self._reject()(cmd)

    @pytest.mark.parametrize("cmd", [
        "echo null",            # 'null' (4 letters) — not the null device
        "echo hi > null",       # 'null' (4 letters) — not the null device
        "echo hi > /dev/null",  # already the bash null device
        "cat nul",              # bare nul as a read argument, not a redirect
        "echo nul",             # bare nul as an echo argument
        "echo a>nulled",        # 'nulled' — nul is not a complete token
        "echo hi >nul.txt",     # 'nul.txt' — filename continuation, not the device
        "echo hi >nul/foo",     # 'nul/foo' — path component, not the device
        "echo hi >nul-foo",     # 'nul-foo' — hyphenated filename, not the device
        "ls",
        "echo hi",
    ])
    def test_non_target_not_rejected(self, cmd):
        self._reject()(cmd)  # must not raise

    @pytest.mark.parametrize("cmd", [
        "grep '>nul' build.log",   # single-quoted literal — also flagged (known tradeoff)
        'grep ">nul" build.log',   # double-quoted literal — also flagged (known tradeoff)
        "echo \\>nul",             # escaped `>` — also flagged (known tradeoff)
    ])
    def test_quoted_or_escaped_nul_also_rejected(self, cmd):
        """Known, accepted limitation: token-based detection cannot distinguish a real
        CMD-null redirect from ``>nul`` inside quotes, after a backslash escape, or in
        heredoc/``[[ ]]`` bodies. These are also rejected — a loud, recoverable error is
        preferred over silently rewriting the command and changing its meaning. The
        BashTool prompt steers generation toward ``/dev/null`` so this is rare.
        """
        with pytest.raises(ExecutionError):
            self._reject()(cmd)

    def test_normalizer_does_not_rewrite_nul(self):
        """The path normalizer must not touch ``nul`` — rewriting was removed in favor
        of up-front rejection."""
        assert self._norm()("echo hi >nul") == "echo hi >nul"
        assert self._norm()("echo hi 2>nul") == "echo hi 2>nul"


# ── env / environment forwarding ──────────────────────────────

class TestBashToolEnvParsing(unittest.TestCase):
    """Unit tests for env parsing — no Runner required."""

    def test_parse_env_alias(self):
        parsed = BashTool._parse_inputs({
            "command": "echo hi",
            "env": {"FOO": "bar", "EMPTY": ""},
        })
        assert parsed.environment == {"FOO": "bar"}

    def test_parse_environment_preferred_over_env(self):
        parsed = BashTool._parse_inputs({
            "command": "echo hi",
            "env": {"A": "1"},
            "environment": {"B": "2"},
        })
        assert parsed.environment == {"B": "2"}

    def test_parse_invalid_env_ignored(self):
        parsed = BashTool._parse_inputs({"command": "echo hi", "env": "not-a-dict"})
        assert parsed.environment is None


@pytest.mark.asyncio
async def test_env_injected_into_subprocess(sys_op) -> None:
    tool = BashTool(sys_op)
    if os.name == "nt":
        command = "echo %OJ_BASH_ENV_PROBE%"
        shell_type = "cmd"
    else:
        command = "echo $OJ_BASH_ENV_PROBE"
        shell_type = "bash"
    res = await tool.invoke({
        "command": command,
        "shell_type": shell_type,
        "env": {"OJ_BASH_ENV_PROBE": "from-env"},
    })
    assert res.success is True
    assert "from-env" in res.data["content"]
