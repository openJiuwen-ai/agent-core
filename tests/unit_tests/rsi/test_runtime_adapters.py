# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for RSI-owned adapters over upstream Core and Harness APIs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.rsi.harness_rsi.evaluator import runtime_adapters
from openjiuwen.rsi.harness_rsi.evaluator.runtime_adapters import (
    RSIBashTool,
    run_agent_with_empty_response_recovery,
)


@pytest.mark.asyncio
async def test_rsi_bash_pipefail_preserves_pipeline_producer_status() -> None:
    shell = MagicMock()
    shell.execute_cmd = AsyncMock(
        return_value=SimpleNamespace(
            code=StatusCode.SUCCESS.code,
            message="",
            data=SimpleNamespace(exit_code=0, stdout="ok\n", stderr=""),
        )
    )
    operation = MagicMock()
    operation.shell.return_value = shell

    result = await RSIBashTool(operation, pipefail=True).invoke({"command": "python -m pytest -q | tail -30"})

    assert result.success is True
    args, kwargs = shell.execute_cmd.await_args
    assert args[0] == "set -o pipefail; python -m pytest -q | tail -30"
    assert kwargs["shell_type"] == "bash"


@pytest.mark.asyncio
async def test_empty_response_recovery_is_owned_by_rsi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_run_agent(agent, inputs, session):
        calls.append({"agent": agent, "inputs": inputs, "session": session})
        if len(calls) == 1:
            return {"output": "", "result_type": "answer"}
        return {"output": "recovered", "result_type": "answer"}

    monkeypatch.setattr(runtime_adapters.Runner, "run_agent", fake_run_agent)

    result = await run_agent_with_empty_response_recovery(
        object(),
        {"query": "solve"},
        session="case-1",
    )

    assert result["output"] == "recovered"
    assert len(calls) == 2
    assert calls[0]["inputs"] == {"query": "solve"}
    assert "[RECOVERY]" in calls[1]["inputs"]["query"]
