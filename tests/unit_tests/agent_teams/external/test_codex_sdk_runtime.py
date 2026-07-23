# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Codex Python SDK member runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.external.cli_agent.codex.runtime import CodexSdkRuntime


def _notification(method: str, **payload):
    return SimpleNamespace(method=method, payload=SimpleNamespace(**payload))


def _item_notification(method: str, item):
    return _notification(method, item=SimpleNamespace(root=item))


class _FakeTurnHandle:
    def __init__(self, notifications):
        self.notifications = notifications
        self.steered: list[str] = []
        self.interrupt_count = 0

    async def stream(self):
        for notification in self.notifications:
            yield notification

    async def steer(self, content: str):
        self.steered.append(content)

    async def interrupt(self):
        self.interrupt_count += 1


class _FakeThread:
    def __init__(self, thread_id: str, turns):
        self.id = thread_id
        self._turns = list(turns)
        self.prompts: list[str] = []
        self.handles: list[_FakeTurnHandle] = []

    async def turn(self, prompt: str):
        self.prompts.append(prompt)
        handle = _FakeTurnHandle(self._turns.pop(0))
        self.handles.append(handle)
        return handle


class _BlockingTurnHandle(_FakeTurnHandle):
    def __init__(self):
        super().__init__([])
        self.streaming = asyncio.Event()
        self.block = asyncio.Event()

    async def stream(self):
        self.streaming.set()
        await self.block.wait()
        if False:  # pragma: no cover - make this an async generator
            yield None


class _BlockingThread(_FakeThread):
    def __init__(self, thread_id: str):
        super().__init__(thread_id, [])
        self.handle = _BlockingTurnHandle()

    async def turn(self, prompt: str):
        self.prompts.append(prompt)
        self.handles.append(self.handle)
        return self.handle


class _FakeRpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message


class _RejectingSteerHandle(_FakeTurnHandle):
    def __init__(self, error: Exception):
        super().__init__([])
        self.error = error

    async def steer(self, content: str):
        self.steered.append(content)
        raise self.error


class _FakeAsyncCodex:
    def __init__(self, *, config, thread):
        self.config = config
        self.thread = thread
        self.start_calls: list[dict] = []
        self.resume_calls: list[tuple[str, dict]] = []
        self.close_count = 0

    async def thread_start(self, **options):
        self.start_calls.append(options)
        return self.thread

    async def thread_resume(self, thread_id: str, **options):
        self.resume_calls.append((thread_id, options))
        return self.thread

    async def close(self):
        self.close_count += 1


def _runtime(*, thread, thread_id=None, on_thread_id=None):
    client = _FakeAsyncCodex(config=None, thread=thread)
    sdk = SimpleNamespace(AsyncCodex=lambda *, config: client)
    runtime = CodexSdkRuntime(
        member_name="developer",
        sdk=sdk,
        config=SimpleNamespace(name="config"),
        thread_options={
            "ephemeral": False,
            "cwd": "/workspace",
            "developer_instructions": "role prompt",
        },
        thread_id=thread_id,
        on_thread_id=on_thread_id,
    )
    return runtime, client


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_reuses_thread_and_maps_stream_events():
    tool = SimpleNamespace(
        type="mcpToolCall",
        id="tool-1",
        server="openjiuwen-team",
        tool="send_message",
        arguments={"recipient": "leader"},
        result={"ok": True},
        error=None,
    )
    first_turn = [
        _notification("item/reasoning/textDelta", delta="thinking"),
        _item_notification("item/started", tool),
        _item_notification("item/completed", tool),
        _notification("item/agentMessage/delta", delta="done"),
        _notification("turn/completed", turn=SimpleNamespace(status="completed")),
    ]
    second_turn = [
        _notification("item/agentMessage/delta", delta="continued"),
        _notification("turn/completed", turn=SimpleNamespace(status="completed")),
    ]
    thread = _FakeThread("thread-developer", [first_turn, second_turn])
    runtime, client = _runtime(thread=thread)

    await runtime.start()
    first = [chunk async for chunk in runtime._drive({"query": "first"})]
    second = [chunk async for chunk in runtime._drive({"query": "second"})]

    assert runtime.session_id == "thread-developer"
    assert client.start_calls == [
        {
            "ephemeral": False,
            "cwd": "/workspace",
            "developer_instructions": "role prompt",
        }
    ]
    assert thread.prompts == ["first", "second"]
    assert [chunk.type for chunk in first] == [
        "llm_reasoning",
        "tool_call",
        "tool_result",
        "llm_output",
    ]
    assert first[1].payload["tool_name"] == "openjiuwen-team.send_message"
    assert first[3].payload["content"] == "done"
    assert second[0].payload["content"] == "continued"

    await runtime.aclose()
    await runtime.aclose()
    assert client.close_count == 1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_resumes_saved_thread_id_without_ephemeral():
    thread = _FakeThread("thread-saved", [[]])
    runtime, client = _runtime(thread=thread, thread_id="thread-saved")

    await runtime.start()

    assert client.start_calls == []
    assert client.resume_calls == [
        (
            "thread-saved",
            {"cwd": "/workspace", "developer_instructions": "role prompt"},
        )
    ]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_reports_new_thread_id_once():
    reported: list[str] = []

    async def _report(thread_id: str) -> None:
        reported.append(thread_id)

    thread = _FakeThread("thread-new", [[], []])
    runtime, _ = _runtime(thread=thread, on_thread_id=_report)

    await runtime.start()
    await runtime.start()
    _ = [chunk async for chunk in runtime._drive({"query": "first"})]

    assert reported == ["thread-new"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_does_not_rewrite_restored_thread_id():
    reported: list[str] = []

    async def _report(thread_id: str) -> None:
        reported.append(thread_id)

    thread = _FakeThread("thread-saved", [[]])
    runtime, _ = _runtime(
        thread=thread,
        thread_id="thread-saved",
        on_thread_id=_report,
    )

    await runtime.start()

    assert reported == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_steers_and_interrupts_active_turn():
    thread = _FakeThread("thread-developer", [[]])
    runtime, client = _runtime(thread=thread)
    await runtime.start()
    handle = _FakeTurnHandle([])
    runtime._active_turn = handle

    await runtime.steer("new priority")
    await runtime._abort_turn()

    assert handle.steered == ["new priority"]
    assert handle.interrupt_count == 1
    await runtime.aclose()
    assert client.close_count == 1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_queues_steer_when_server_turn_already_ended():
    thread = _FakeThread("thread-developer", [[]])
    runtime, _ = _runtime(thread=thread)
    handle = _RejectingSteerHandle(_FakeRpcError(-32600, "no active turn to steer"))
    runtime._active_turn = handle

    await runtime.steer("deliver on next turn")

    assert handle.steered == ["deliver on next turn"]
    assert runtime._active_turn is None
    assert runtime._pending == ["deliver on next turn"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_does_not_queue_unrelated_steer_errors():
    thread = _FakeThread("thread-developer", [[]])
    runtime, _ = _runtime(thread=thread)
    error = _FakeRpcError(-32600, "invalid steer input")
    handle = _RejectingSteerHandle(error)
    runtime._active_turn = handle

    with pytest.raises(_FakeRpcError, match="invalid steer input"):
        await runtime.steer("bad input")

    assert runtime._active_turn is handle
    assert runtime._pending == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_reports_non_retryable_sdk_errors():
    error = SimpleNamespace(message="boom")
    turn = [_notification("error", error=error, will_retry=False)]
    thread = _FakeThread("thread-developer", [turn])
    runtime, _ = _runtime(thread=thread)

    with pytest.raises(RuntimeError, match="codex SDK turn failed"):
        async for _ in runtime._drive({"query": "fail"}):
            pass


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_queues_follow_up_for_same_thread():
    thread = _FakeThread("thread-developer", [[], []])
    runtime, _ = _runtime(thread=thread)
    await runtime.follow_up("next")

    chunks = [chunk async for chunk in runtime._drive({"query": "first"})]

    assert chunks == []
    assert thread.prompts == ["first", "next"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_stop_does_not_wait_for_stuck_stream():
    thread = _BlockingThread("thread-developer")
    runtime, client = _runtime(thread=thread)
    await runtime.start()
    await runtime.send("long task")
    await thread.handle.streaming.wait()

    await asyncio.wait_for(runtime.stop(), timeout=1.0)

    assert thread.handle.interrupt_count == 1
    assert client.close_count == 1
