"""Covers the live "what just happened" hint derived from a module attempt's
own agent_trace.jsonl -- the pure text-formatting helpers, and the tailer
loop's incremental-read/cancellation behavior. See pipeline/stage_activity.py.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.stage_activity import (
    _describe_bash,
    _describe_event,
    _extract_bash_command,
    _extract_path,
    tail_activity,
)

# -- _extract_bash_command ---------------------------------------------------


def test_extract_bash_command_from_plain_dict():
    assert _extract_bash_command({"command": "pytest tests/"}) == "pytest tests/"


def test_extract_bash_command_from_json_encoded_string():
    assert _extract_bash_command(json.dumps({"command": "ls -la"})) == "ls -la"


def test_extract_bash_command_from_truncated_wrapper():
    wrapper = {"text": json.dumps({"command": "grep -rl foo ."}), "truncated": True}
    assert _extract_bash_command(wrapper) == "grep -rl foo ."


@pytest.mark.parametrize("bad_input", [None, 42, "not json", {"no_command_here": 1}])
def test_extract_bash_command_returns_none_on_garbage(bad_input):
    assert _extract_bash_command(bad_input) is None


# -- _extract_path -------------------------------------------------------


def test_extract_path_from_path_key():
    assert _extract_path({"path": "docs/x.md"}) == "docs/x.md"


def test_extract_path_from_file_path_key():
    assert _extract_path({"file_path": "a/b.py"}) == "a/b.py"


def test_extract_path_from_json_string():
    assert _extract_path(json.dumps({"path": "a.py"})) == "a.py"


def test_extract_path_returns_none_when_missing():
    assert _extract_path({"other": 1}) is None


# -- _describe_bash ------------------------------------------------------


def test_describe_bash_recognizes_known_skill_pattern():
    seen: dict[str, int] = {}
    assert _describe_bash("python ts-write/scripts/write.py", seen) == "最近动作：撰写论文正文"


def test_describe_bash_counts_repeated_skill_visits():
    seen: dict[str, int] = {}
    _describe_bash("python ts-review/scripts/review.py", seen)
    result = _describe_bash("python ts-review/scripts/review.py", seen)
    assert result == "最近动作：审校论文（第 2 次）"


def test_describe_bash_recognizes_test_commands():
    seen: dict[str, int] = {}
    assert _describe_bash("pytest tests/unit_tests/foo.py", seen) == "最近动作：运行验证测试"


def test_describe_bash_falls_back_to_generic_and_clips_long_commands():
    seen: dict[str, int] = {}
    command = "echo " + "x" * 100
    result = _describe_bash(command, seen)
    assert result.startswith("最近动作：执行命令 `echo ")
    assert result.endswith("…`")
    assert len(result) < len(command)


# -- _describe_event -------------------------------------------------------


def test_describe_event_tool_call_error():
    event = {"event": "tool_call_error", "tool_name": "bash"}
    assert _describe_event(event, {}) == "最近动作：上一步失败，尝试修正"


def test_describe_event_model_call_start():
    event = {"event": "model_call_start"}
    assert _describe_event(event, {}) == "最近动作：等待模型响应中"


def test_describe_event_bash_tool_call_start():
    event = {
        "event": "tool_call_start",
        "tool_name": "bash",
        "arguments": {"command": "ls"},
    }
    assert _describe_event(event, {}) == "最近动作：执行命令 `ls`"


def test_describe_event_read_tool_with_path():
    event = {
        "event": "tool_call_start",
        "tool_name": "openjiuwen_ref_read_file",
        "arguments": {"path": "docs/x.md"},
    }
    assert _describe_event(event, {}) == "最近动作：读取 `docs/x.md`"


def test_describe_event_write_tool_without_path():
    event = {"event": "tool_call_start", "tool_name": "write_file", "arguments": {}}
    assert _describe_event(event, {}) == "最近动作：编写文件"


def test_describe_event_unknown_tool_falls_back_to_generic_call():
    event = {"event": "tool_call_start", "tool_name": "some_custom_tool", "arguments": {}}
    assert _describe_event(event, {}) == "最近动作：调用 some_custom_tool"


def test_describe_event_ignores_irrelevant_events():
    assert _describe_event({"event": "tool_call_end", "tool_name": "bash"}, {}) is None
    assert _describe_event({"event": "trace_end"}, {}) is None


# -- tail_activity ---------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_activity_pushes_note_for_new_lines_and_tracks_offset(tmp_path, monkeypatch):
    trace_path = tmp_path / "agent_trace.jsonl"
    trace_path.write_text("", encoding="utf-8")

    import openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.stage_activity as mod

    monkeypatch.setattr(mod, "agent_trace_path", lambda *a, **k: trace_path)

    notes: list[str] = []

    async def on_note(note: str) -> None:
        notes.append(note)

    task = asyncio.create_task(
        tail_activity(
            run_id="r1",
            module="code_implementation",
            round_index=1,
            attempt=1,
            on_note=on_note,
            poll_seconds=0.01,
        )
    )
    try:
        await asyncio.sleep(0.05)
        assert notes == []  # nothing written yet

        trace_path.write_text(
            json.dumps({"event": "tool_call_start", "tool_name": "bash", "arguments": {"command": "ls"}}) + "\n",
            encoding="utf-8",
        )
        await asyncio.sleep(0.05)
        assert notes == ["最近动作：执行命令 `ls`"]

        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"event": "tool_call_start", "tool_name": "bash", "arguments": {"command": "pwd"}}) + "\n"
            )
        await asyncio.sleep(0.05)
        assert notes == ["最近动作：执行命令 `ls`", "最近动作：执行命令 `pwd`"]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_tail_activity_tolerates_missing_file(monkeypatch, tmp_path):
    import openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.stage_activity as mod

    missing = tmp_path / "does-not-exist" / "agent_trace.jsonl"
    monkeypatch.setattr(mod, "agent_trace_path", lambda *a, **k: missing)

    calls: list[str] = []

    async def on_note(note: str) -> None:
        calls.append(note)

    task = asyncio.create_task(
        tail_activity(
            run_id="r1",
            module="code_implementation",
            round_index=1,
            attempt=1,
            on_note=on_note,
            poll_seconds=0.01,
        )
    )
    try:
        await asyncio.sleep(0.05)
        assert calls == []
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_tail_activity_stops_cleanly_on_cancel():
    async def on_note(note: str) -> None:
        pass

    task = asyncio.create_task(
        tail_activity(
            run_id="r1",
            module="code_implementation",
            round_index=1,
            attempt=1,
            on_note=on_note,
            poll_seconds=0.01,
        )
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done()
