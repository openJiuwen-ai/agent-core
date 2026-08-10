# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the per-agent CWD state helpers in cwd.py.

Focus: ``get_agent_history_base_dir`` — the tool-history base directory
that must prefer the agent workspace and, when none is configured, fall
back to a user-level directory instead of the project CWD (#1490).
"""

import os
import tempfile

from openjiuwen.core.sys_operation.cwd import (
    get_agent_history_base_dir,
    init_cwd,
    set_workspace,
)


def _reset_cwd_state() -> None:
    """Reinitialize the per-context CWD state without a workspace."""
    init_cwd(os.getcwd())


class TestGetAgentHistoryBaseDir:
    def test_returns_workspace_when_set(self):
        workspace = tempfile.mkdtemp()
        try:
            set_workspace(workspace)
            assert get_agent_history_base_dir() == os.path.realpath(workspace)
        finally:
            _reset_cwd_state()

    def test_falls_back_to_user_level_dir_when_workspace_unset(self):
        _reset_cwd_state()
        base = get_agent_history_base_dir()
        assert ".openjiuwen" in base
        assert "agent_history" in base
        assert not base.startswith(os.getcwd()), (
            "history base must NOT be the project CWD (#1490)"
        )
