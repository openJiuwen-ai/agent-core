# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from unittest.mock import Mock

from openjiuwen.core.sys_operation.shell_process_registry import (
    kill_shell_processes_for_session,
    set_shell_session_id,
    reset_shell_session_id,
)
from openjiuwen.core.sys_operation import shell_process_registry as registry_module


def test_kill_tracked_asyncio_process_for_session(monkeypatch) -> None:
    registry = registry_module.ShellProcessRegistry()
    monkeypatch.setattr(registry_module, "SHELL_PROCESS_REGISTRY", registry)
    terminate = Mock(return_value=True)
    monkeypatch.setattr(registry_module, "_terminate_unowned_process", terminate)
    token = set_shell_session_id("sess_kill")
    proc = Mock(spec=asyncio.subprocess.Process)
    try:
        registry.register("sess_kill", proc)
        assert kill_shell_processes_for_session("sess_kill") == 1
        terminate.assert_called_once_with(proc)
        assert kill_shell_processes_for_session("sess_kill") == 0
    finally:
        reset_shell_session_id(token)
