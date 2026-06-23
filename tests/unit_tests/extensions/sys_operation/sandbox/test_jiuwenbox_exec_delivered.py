# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for jiuwenbox exec delivery detection and exec pipeline."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as jb


def _daemon_unavailable_stderr(sandbox_id: str) -> str:
    return (
        f"sandbox {sandbox_id!r} daemon IPC channel unavailable; "
        "the daemon is not running or its control socket is gone"
    )


def test_parse_daemon_ipc_unavailable_full_message() -> None:
    sandbox_id = "abc-123"
    stderr = _daemon_unavailable_stderr(sandbox_id)
    assert jb._parse_daemon_ipc_unavailable_sandbox_id(stderr) == sandbox_id


def test_parse_daemon_ipc_unavailable_rejects_substring_only() -> None:
    assert jb._parse_daemon_ipc_unavailable_sandbox_id(
        "error: daemon IPC channel unavailable in logs"
    ) is None


def test_parse_daemon_ipc_unavailable_rejects_wrong_suffix() -> None:
    sandbox_id = "abc-123"
    stderr = f"sandbox {sandbox_id!r} daemon IPC channel unavailable; something else"
    assert jb._parse_daemon_ipc_unavailable_sandbox_id(stderr) is None


def test_is_sandbox_exec_delivered_zero_exit() -> None:
    assert jb._is_sandbox_exec_delivered({"exit_code": 0, "stderr": ""}, sandbox_id="x")


def test_is_sandbox_exec_delivered_command_failure() -> None:
    assert jb._is_sandbox_exec_delivered(
        {"exit_code": 1, "stderr": "grep: not found"},
        sandbox_id="x",
    )


def test_is_sandbox_exec_delivered_daemon_unavailable_matching_id() -> None:
    sandbox_id = "sid-1"
    assert not jb._is_sandbox_exec_delivered(
        {"exit_code": 1, "stderr": _daemon_unavailable_stderr(sandbox_id)},
        sandbox_id=sandbox_id,
    )


def test_is_sandbox_exec_delivered_daemon_unavailable_wrong_id() -> None:
    assert jb._is_sandbox_exec_delivered(
        {"exit_code": 1, "stderr": _daemon_unavailable_stderr("other-id")},
        sandbox_id="sid-1",
    )


class _PipelineProbe(jb._JiuwenBoxProviderMixin):
    endpoint = MagicMock()
    config = MagicMock()

    def __init__(self, sandbox_id: str = "sid-1") -> None:
        self._client = None
        self._sandbox_id = sandbox_id
        self._timeout_seconds = 30

    def _launcher_extra_params(self, create: bool = False) -> dict[str, Any]:
        return {}

    def _get_sandbox_id(self) -> str:
        return self._sandbox_id


@pytest.mark.asyncio
async def test_run_exec_pipeline_local_fallback_on_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIUWENBOX_SANDBOX_RECREATE_RETRIES", "0")
    probe = _PipelineProbe()

    def sandbox_op(_sid: str) -> dict[str, Any]:
        raise httpx.ConnectError("connection refused")

    local_op = AsyncMock(return_value={"stdout": "ok", "stderr": "", "exit_code": 0, "local": True})

    result, err = await probe._run_exec_pipeline(
        sandbox_op=sandbox_op,
        local_op=local_op,
        fallback_on_failure=True,
    )
    assert err is None
    assert result["local"] is True
    local_op.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_exec_pipeline_no_fallback_on_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIUWENBOX_SANDBOX_RECREATE_RETRIES", "0")
    probe = _PipelineProbe()

    def sandbox_op(_sid: str) -> dict[str, Any]:
        raise httpx.ConnectError("connection refused")

    local_op = AsyncMock(return_value={"stdout": "ok", "stderr": "", "exit_code": 0, "local": True})

    result, err = await probe._run_exec_pipeline(
        sandbox_op=sandbox_op,
        local_op=local_op,
        fallback_on_failure=False,
    )
    assert result == {}
    assert err is not None
    local_op.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_exec_pipeline_daemon_unavailable_triggers_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIUWENBOX_SANDBOX_RECREATE_RETRIES", "0")
    sandbox_id = "sid-1"
    probe = _PipelineProbe(sandbox_id=sandbox_id)

    def sandbox_op(_sid: str) -> dict[str, Any]:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": _daemon_unavailable_stderr(sandbox_id),
        }

    local_op = AsyncMock(return_value={"stdout": "local", "stderr": "", "exit_code": 0, "local": True})

    result, err = await probe._run_exec_pipeline(
        sandbox_op=sandbox_op,
        local_op=local_op,
        fallback_on_failure=True,
    )
    assert err is None
    assert result["stdout"] == "local"
    local_op.assert_awaited_once()
