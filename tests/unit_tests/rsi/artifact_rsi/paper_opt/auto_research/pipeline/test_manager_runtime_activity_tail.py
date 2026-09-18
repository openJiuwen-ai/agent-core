"""Covers ManagerRuntime._start_activity_tail/_stop_activity_tail -- the
lifecycle glue that must never leak a background task, regardless of how
adapter.ainvoke(...) ends (success, exception, or outer cancellation). See
pipeline/manager.py::_execute_contract and pipeline/stage_activity.py.
"""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.manager import ManagerRuntime


def _bare_runtime(on_stage) -> ManagerRuntime:
    """A ManagerRuntime with only `on_stage` set, bypassing __init__ (which
    would otherwise build a full subagent registry) -- these two methods
    don't touch anything else on the instance."""
    runtime = object.__new__(ManagerRuntime)
    runtime.on_stage = on_stage
    return runtime


@pytest.mark.asyncio
async def test_start_activity_tail_returns_none_when_no_on_stage():
    runtime = _bare_runtime(None)
    task = runtime._start_activity_tail("code_implementation", "run-1", 1, 1)
    assert task is None
    await runtime._stop_activity_tail(task)  # must not raise on None


@pytest.mark.asyncio
async def test_start_activity_tail_creates_a_task_and_stop_cancels_it(tmp_path, monkeypatch):
    import openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.stage_activity as stage_activity_mod

    trace_path = tmp_path / "agent_trace.jsonl"
    monkeypatch.setattr(stage_activity_mod, "agent_trace_path", lambda *a, **k: trace_path)

    seen: list[tuple[str, str | None]] = []

    async def on_stage(module: str, note: str | None = None) -> None:
        seen.append((module, note))

    runtime = _bare_runtime(on_stage)
    task = runtime._start_activity_tail("code_implementation", "run-1", 1, 1)
    assert isinstance(task, asyncio.Task)
    assert not task.done()

    await runtime._stop_activity_tail(task)

    assert task.done()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_activity_tail_note_flows_through_on_stage(tmp_path, monkeypatch):
    import json

    import openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.stage_activity as stage_activity_mod

    trace_path = tmp_path / "agent_trace.jsonl"
    trace_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(stage_activity_mod, "agent_trace_path", lambda *a, **k: trace_path)

    seen: list[tuple[str, str | None]] = []
    got_note = asyncio.Event()

    async def on_stage(module: str, note: str | None = None) -> None:
        seen.append((module, note))
        got_note.set()

    runtime = _bare_runtime(on_stage)
    task = runtime._start_activity_tail("code_implementation", "run-1", 1, 1)
    try:
        trace_path.write_text(
            json.dumps({"event": "tool_call_start", "tool_name": "bash", "arguments": {"command": "ls"}}) + "\n",
            encoding="utf-8",
        )
        # _start_activity_tail doesn't expose a poll_seconds override (no
        # production need for one), so this waits out the real default
        # interval (stage_activity._POLL_SECONDS) rather than patching it --
        # patching wouldn't work anyway since it's bound as a default arg
        # value at tail_activity's definition time, not read live per call.
        await asyncio.wait_for(got_note.wait(), timeout=stage_activity_mod._POLL_SECONDS + 2.0)
        assert seen == [("code_implementation", "最近动作：执行命令 `ls`")]
    finally:
        await runtime._stop_activity_tail(task)


@pytest.mark.asyncio
async def test_stop_activity_tail_is_safe_to_call_on_already_finished_task():
    async def _noop() -> None:
        return None

    task = asyncio.create_task(_noop())
    await task
    # Must not raise even though the task already completed on its own.
    await ManagerRuntime._stop_activity_tail(task)
