# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for PowerShellTool."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "darwin", reason="PowerShell tests are not supported on macOS")

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.sys_operation.cwd import set_workspace
from openjiuwen.harness.tools import PowerShellTool


def _mock_result(*, stdout: str = "", stderr: str = "", exit_code: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        code=StatusCode.SUCCESS.code,
        message="",
        data=SimpleNamespace(stdout=stdout, stderr=stderr, exit_code=exit_code),
    )


@pytest.mark.asyncio
async def test_invoke_forces_powershell_shell_type() -> None:
    shell = MagicMock()
    shell.execute_cmd = AsyncMock(return_value=_mock_result(stdout="ok\n"))

    sys_op = MagicMock()
    sys_op.work_dir = None
    sys_op.shell.return_value = shell

    tool = PowerShellTool(sys_op, language="en")
    res = await tool.invoke({"command": "Write-Output ok"})

    assert res.success is True
    shell.execute_cmd.assert_awaited_once()
    assert shell.execute_cmd.await_args.kwargs["shell_type"] == "powershell"


@pytest.mark.asyncio
async def test_read_only_blocks_write_commands() -> None:
    sys_op = MagicMock()
    sys_op.work_dir = None

    tool = PowerShellTool(sys_op, permission_mode="read_only")
    res = await tool.invoke({"command": "Set-Content test.txt hi"})

    assert res.success is False
    assert "Read-only" in res.error


@pytest.mark.asyncio
async def test_injection_pattern_blocked() -> None:
    sys_op = MagicMock()
    sys_op.work_dir = None

    tool = PowerShellTool(sys_op)
    res = await tool.invoke({"command": "Invoke-Expression \"Get-ChildItem\""})

    assert res.success is False
    assert "injection" in res.error.lower()


# ── history path construction ─────────────────────────────────

class TestPowerShellToolHistoryPath(unittest.TestCase):
    """Unit tests for _build_history_path — no Runner required."""

    def _make_session(self, session_id: str) -> MagicMock:
        mock = MagicMock()
        mock.get_session_id.return_value = session_id
        return mock

    def test_path_contains_agent_id_and_session_id(self):
        """History path embeds both agent_id and session_id."""
        session = self._make_session("sess_abc")
        tool = PowerShellTool(MagicMock(), agent_id="agent_xyz")
        path = tool._build_history_path(session)
        assert "agent_xyz" in path
        assert "sess_abc" in path
        session.get_session_id.assert_called_once()

    def test_default_agent_id_used_when_none(self):
        """agent_id=None falls back to 'default'."""
        session = self._make_session("s1")
        tool = PowerShellTool(MagicMock(), agent_id=None)
        path = tool._build_history_path(session)
        assert "default" in path

    def test_workspace_path_is_base_dir(self):
        """Workspace ContextVar is used as the base directory."""
        session = self._make_session("s1")
        workspace = tempfile.mkdtemp()
        try:
            set_workspace(workspace)
            tool = PowerShellTool(MagicMock(), agent_id="a")
            path = tool._build_history_path(session)
            assert path.startswith(workspace)
            assert ".agent_history" in path
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_filename_pattern(self):
        """Filename follows file_ops_{agent_id}_{session_id}.json pattern."""
        session = self._make_session("sess123")
        tool = PowerShellTool(MagicMock(), agent_id="myagent")
        path = tool._build_history_path(session)
        filename = os.path.basename(path)
        assert filename == "file_ops_myagent_sess123.json"
