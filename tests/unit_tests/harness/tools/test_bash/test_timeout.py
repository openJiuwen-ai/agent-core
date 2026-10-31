# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression coverage for Bash invocation and subprocess timeout budgets."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import ToolOutput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ability_manager as am_mod
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.local.utils import AsyncProcessHandler, OperationUtils
from openjiuwen.harness.tools.shell.bash._tool import BashTool


@pytest_asyncio.fixture
async def bash_tool(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[BashTool]:
    """Isolate runtime state and shell policy from timeout regression tests."""
    monkeypatch.setenv("OPENJIUWEN_BASH_STRICT", "0")
    monkeypatch.delenv("BASH_TOOL_MAX_TIMEOUT_SECONDS", raising=False)
    await Runner.start()
    operation = SysOperation(SysOperationCard(
        id="bash-timeout-regression",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(shell_allowlist=None, restrict_to_sandbox=False),
    ))
    try:
        yield BashTool(operation)
    finally:
        await Runner.stop()


def _python_command(code: str) -> str:
    """Quote a Python command for the platform's default shell."""
    args = [sys.executable, "-u", "-c", code]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


async def _execute(
        monkeypatch: pytest.MonkeyPatch,
        tool: BashTool,
        inputs: dict[str, Any],
        *,
        registered: bool,
) -> ToolOutput:
    """Exercise either AbilityManager tool lookup path with the real Bash tool."""
    manager = AbilityManager(owner_id="bash-timeout-regression")
    if registered:
        manager.add(tool.card)
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda **_kwargs: tool)
    call = ToolCall(
        id="bash-timeout-call",
        type="function",
        name=tool.card.name,
        arguments=json.dumps(inputs),
    )
    result, _ = await asyncio.wait_for(manager._execute_single_tool_call(call, session=None), timeout=5)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [True, False])
@pytest.mark.parametrize(
    ("timeout_input", "expected"),
    [
        ({}, 300),
        ({"timeout": 900}, 900),
        ({"timeout": 5000}, 3600),
        ({"timeout": 0}, 1),
        ({"timeout": "invalid"}, 300),
    ],
)
async def test_timeout_reaches_subprocess_without_outer_or_shell_truncation(
        monkeypatch: pytest.MonkeyPatch,
        bash_tool: BashTool,
        tmp_path: Path,
        registered: bool,
        timeout_input: dict[str, Any],
        expected: int,
) -> None:
    """The normalized input bypasses both the default call deadline and old shell cap."""
    monkeypatch.setattr(am_mod, "DEFAULT_TOOL_CALL_TIMEOUT", 0)
    monkeypatch.setenv("JW_EXECUTE_CMD_MAX_TIMEOUT", "1")
    observed: list[int] = []
    create_handler = OperationUtils.create_handler

    def record_handler(**kwargs: Any) -> AsyncProcessHandler:
        observed.append(kwargs["timeout"])
        return create_handler(**kwargs)

    monkeypatch.setattr(OperationUtils, "create_handler", record_handler)
    inputs = {"command": "echo completed", "workdir": str(tmp_path), **timeout_input}
    result = await _execute(monkeypatch, bash_tool, inputs, registered=registered)

    assert result.success, result.error
    assert "completed" in result.data["content"]
    assert observed == [expected]
    assert bash_tool.get_call_timeout(inputs, default_timeout=0) == expected + 30


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [True, False])
async def test_command_can_outlive_previous_shell_cap(
        monkeypatch: pytest.MonkeyPatch,
        bash_tool: BashTool,
        tmp_path: Path,
        registered: bool,
) -> None:
    """A command exceeding the former configured cap completes within its input budget."""
    monkeypatch.setattr(am_mod, "DEFAULT_TOOL_CALL_TIMEOUT", 0.05)
    monkeypatch.setenv("JW_EXECUTE_CMD_MAX_TIMEOUT", "1")
    inputs = {
        "command": _python_command("import time, sys; time.sleep(1.2); sys.stdout.write('completed')"),
        "workdir": str(tmp_path),
        "timeout": 3,
    }
    result = await _execute(monkeypatch, bash_tool, inputs, registered=registered)

    assert result.success, result.error
    assert "completed" in result.data["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [True, False])
async def test_command_deadline_returns_partial_output_and_reaps_process(
        monkeypatch: pytest.MonkeyPatch,
        bash_tool: BashTool,
        tmp_path: Path,
        registered: bool,
) -> None:
    """The shell owns timeout reporting and cleanup instead of outer cancellation."""
    monkeypatch.setattr(am_mod, "DEFAULT_TOOL_CALL_TIMEOUT", 0.05)
    processes: list[asyncio.subprocess.Process] = []
    shell = bash_tool._operation.shell()
    create_subprocess = shell._create_subprocess

    async def record_process(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await create_subprocess(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(shell, "_create_subprocess", record_process)
    inputs = {
        "command": _python_command(
            "import sys, time; sys.stdout.write('partial\\n'); sys.stdout.flush(); time.sleep(30)",
        ),
        "workdir": str(tmp_path),
        "timeout": 1,
    }
    result = await _execute(monkeypatch, bash_tool, inputs, registered=registered)

    assert not result.success
    assert "execution timeout after 1 seconds" in result.error
    assert "partial" in result.data["content"]
    assert len(processes) == 1
    assert processes[0].returncode is not None


def test_background_launch_keeps_default_invocation_budget(bash_tool: BashTool) -> None:
    """The foreground execution deadline does not apply to background launch."""
    assert bash_tool.get_call_timeout(
        {"command": "echo completed", "run_in_background": True, "timeout": 900},
        default_timeout=42,
    ) == 42
