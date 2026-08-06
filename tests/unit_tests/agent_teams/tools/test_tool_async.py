# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async control tools (list / output / cancel) over a fake harness runtime.

A minimal fake harness exposes a real :class:`AsyncToolRuntime`; the tools are
driven exactly as the factory wires them (``parent_agent`` + translator) so the
list rendering, blocking/non-blocking output retrieval, disk-spill read-back,
and cancel paths are verified without a live NativeHarness.
"""
from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.agent_teams.harness.async_tools import AsyncToolRuntime
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.tool_async import (
    AsyncTaskCancelTool,
    AsyncTaskOutputTool,
    AsyncTasksListTool,
)


async def _no_inject(text: str) -> None:
    """A no-op completion sink for tests that ignore injected text."""
    return None


class _FakeHarness:
    """Minimal harness exposing an ``async_tool_runtime`` for the control tools."""

    def __init__(self, runtime: AsyncToolRuntime) -> None:
        self.async_tool_runtime = runtime


def _runtime(**kwargs: Any) -> AsyncToolRuntime:
    return AsyncToolRuntime(inject=_no_inject, **kwargs)


def test_list_tool_renders_tasks():
    """list tool maps records to one dense line each."""
    t = make_translator("cn")

    async def _scenario() -> str:
        runtime = _runtime()

        async def _work() -> str:
            return "ok"

        runtime.launch("a", _work, tool_name="demo", description="da")
        while runtime.registry["a"].status == "running":
            await asyncio.sleep(0.005)
        tool = AsyncTasksListTool(_FakeHarness(runtime), t)
        return tool.map_result(await tool.invoke({}))

    text = asyncio.run(_scenario())
    assert "task_id=a" in text
    assert "demo" in text


def test_list_tool_empty():
    """list tool renders an explicit empty marker."""
    t = make_translator("cn")
    tool = AsyncTasksListTool(_FakeHarness(_runtime()), t)
    out = asyncio.run(tool.invoke({}))
    assert tool.map_result(out) == "No async tasks."


def test_list_tool_has_empty_object_schema():
    """list tool exposes a valid no-argument JSON schema."""
    t = make_translator("cn")
    tool = AsyncTasksListTool(_FakeHarness(_runtime()), t)
    assert tool.card.input_params == {
        "type": "object",
        "properties": {},
    }


def test_output_tool_requires_task_id():
    """Missing task_id fails fast with a field error."""
    t = make_translator("cn")
    tool = AsyncTaskOutputTool(_FakeHarness(_runtime()), t)
    out = asyncio.run(tool.invoke({"task_id": ""}))
    assert out.success is False
    assert "task_id" in out.error


def test_output_tool_unknown_task():
    """An unknown task id returns a not-found error."""
    t = make_translator("cn")
    tool = AsyncTaskOutputTool(_FakeHarness(_runtime()), t)
    out = asyncio.run(tool.invoke({"task_id": "ghost"}))
    assert out.success is False
    assert "not found" in out.error


def test_output_tool_returns_completed_result():
    """Non-blocking fetch of a finished task returns its in-memory result."""
    t = make_translator("cn")

    async def _scenario():
        runtime = _runtime()

        async def _work() -> str:
            return "the-answer"

        runtime.launch("a", _work, tool_name="demo", description="d")
        while runtime.registry["a"].status == "running":
            await asyncio.sleep(0.005)
        tool = AsyncTaskOutputTool(_FakeHarness(runtime), t)
        return await tool.invoke({"task_id": "a"})

    out = asyncio.run(_scenario())
    assert out.success is True
    assert out.data["status"] == "completed"
    assert out.data["result"] == "the-answer"


def test_output_tool_block_waits_for_completion():
    """block=true waits until the task completes, then returns its result."""
    t = make_translator("cn")

    async def _scenario():
        runtime = _runtime()
        gate = asyncio.Event()

        async def _gated() -> str:
            await gate.wait()
            return "late"

        runtime.launch("a", _gated, tool_name="demo", description="d")
        tool = AsyncTaskOutputTool(_FakeHarness(runtime), t)

        async def _release() -> None:
            await asyncio.sleep(0.02)
            gate.set()

        release_task = asyncio.create_task(_release())
        out = await tool.invoke({"task_id": "a", "block": True, "timeout": 2000})
        await release_task
        return out

    out = asyncio.run(_scenario())
    assert out.data["status"] == "completed"
    assert out.data["result"] == "late"


def test_output_tool_reads_spilled_file(tmp_path):
    """When the record has an output_file, the tool reads full text from disk."""
    t = make_translator("cn")

    async def _scenario():
        runtime = _runtime(output_dir_resolver=lambda: tmp_path, spill_threshold=100)

        async def _big() -> str:
            return "Q" * 1000

        runtime.launch("a", _big, tool_name="demo", description="d")
        while runtime.registry["a"].status == "running":
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.005)
        tool = AsyncTaskOutputTool(_FakeHarness(runtime), t)
        return await tool.invoke({"task_id": "a"})

    out = asyncio.run(_scenario())
    assert out.success is True
    assert out.data["result"] == "Q" * 1000


def test_cancel_tool():
    """cancel tool stops a running task; unknown id returns a not-found error."""
    t = make_translator("cn")

    async def _scenario():
        runtime = _runtime()
        started = asyncio.Event()

        async def _slow() -> None:
            started.set()
            await asyncio.sleep(10)

        runtime.launch("a", _slow, tool_name="demo", description="d")
        await started.wait()
        tool = AsyncTaskCancelTool(_FakeHarness(runtime), t)
        ok = await tool.invoke({"task_id": "a"})
        missing = await tool.invoke({"task_id": "ghost"})
        await asyncio.sleep(0.01)
        return runtime, ok, missing

    runtime, ok, missing = asyncio.run(_scenario())
    assert ok.success is True
    assert missing.success is False
    assert runtime.get("a").status == "error"
