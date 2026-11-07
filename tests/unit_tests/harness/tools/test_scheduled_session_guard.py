# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Scheduled execution sessions must not mutate the scheduler store.

The host removes the cron toolset from a cron run, so fabricating a nested
job by writing ``cron_jobs.json`` from the shell/file tools would bypass the
tool layer and be reloaded by the scheduler as a real job.
"""
from __future__ import annotations

import pytest

from openjiuwen.harness.tools import scheduled_session_guard as guard_module
from openjiuwen.harness.tools.scheduled_session_guard import (
    is_cron_execution_session,
    scheduled_session_guard,
    scheduled_store_reference,
)
from openjiuwen.harness.tools.shell.bash._tool import BashTool


@pytest.mark.parametrize("session_id", ["__cron___1a17ecc1396_f27ea66c56a1", "cron_1729_job"])
def test_is_cron_execution_session_true(session_id: str) -> None:
    assert is_cron_execution_session(session_id) is True


@pytest.mark.parametrize("session_id", ["web_1a17eca3ccb_501df43395c6", "heartbeat_1", "", None])
def test_is_cron_execution_session_false(session_id: object) -> None:
    assert is_cron_execution_session(session_id) is False


def test_store_reference_matches_scheduler_files() -> None:
    assert scheduled_store_reference('{"jobs": []} > cron_jobs.json') == "cron_jobs.json"
    assert scheduled_store_reference("cat cron_lifecycle_owners.json") == "cron_lifecycle_owners.json"
    assert scheduled_store_reference("echo hello") is None


def test_guard_denies_store_access_in_cron_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        guard_module,
        "_current_session_id",
        lambda: "__cron___1a17ecc1396_f27ea66c56a1",
    )
    command = (
        "python3 - <<'EOF'\n"
        "from pathlib import Path\n"
        'Path("/root/.jiuwenswarm/agent/home/cron_jobs.json").write_text("{}")\n'
        "EOF"
    )
    reason = scheduled_session_guard(command)
    assert reason is not None
    assert "scheduler store" in reason


def test_guard_allows_cron_task_that_does_not_touch_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        guard_module,
        "_current_session_id",
        lambda: "__cron___1a17ecc1396_f27ea66c56a1",
    )
    assert scheduled_session_guard("date '+%Y-%m-%d %H:%M:%S'; sleep 60") is None


def test_guard_allows_store_reference_in_normal_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        guard_module,
        "_current_session_id",
        lambda: "web_1a17eca3ccb_501df43395c6",
    )
    assert scheduled_session_guard("cat cron_jobs.json") is None


@pytest.mark.asyncio
async def test_bash_tool_rejects_store_write_in_cron_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        guard_module,
        "_current_session_id",
        lambda: "__cron___1a17ecc1396_f27ea66c56a1",
    )
    tool = BashTool(operation=object())  # guard runs before any operation call
    output = await tool.invoke(
        {"command": "cat > /root/.jiuwenswarm/agent/home/cron_jobs.json <<'EOF'\n{}\nEOF"}
    )
    assert output.success is False
    assert "scheduler store" in (output.error or "")
