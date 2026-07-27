# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Codex Python SDK member runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.external.cli_agent.codex.runtime import (
    CodexSdkRuntime,
    _tool_result,
)


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


class _HandleSequenceThread(_FakeThread):
    def __init__(self, thread_id: str, handles):
        super().__init__(thread_id, [])
        self._handles = list(handles)

    async def turn(self, prompt: str):
        self.prompts.append(prompt)
        handle = self._handles.pop(0)
        self.handles.append(handle)
        return handle


class _NotificationThenBlockingTurnHandle(_BlockingTurnHandle):
    async def stream(self):
        self.streaming.set()
        yield _notification("item/agentMessage/delta", delta="started")
        await self.block.wait()


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
    def __init__(self, *, config, thread, resume_error: Exception | None = None):
        self.config = config
        self.thread = thread
        self.resume_error = resume_error
        self.start_calls: list[dict] = []
        self.resume_calls: list[tuple[str, dict]] = []
        self.close_count = 0

    async def thread_start(self, **options):
        self.start_calls.append(options)
        return self.thread

    async def thread_resume(self, thread_id: str, **options):
        self.resume_calls.append((thread_id, options))
        if self.resume_error is not None:
            raise self.resume_error
        return self.thread

    async def close(self):
        self.close_count += 1


class _FakeMemberSession:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.pre_run_count = 0
        self.commit_count = 0
        self.post_run_count = 0

    async def pre_run(self):
        self.pre_run_count += 1

    def get_state(self, key=None):
        return self.state if key is None else self.state.get(key)

    def update_state(self, data):
        self.state.update(data)

    async def commit(self):
        self.commit_count += 1

    async def post_run(self):
        self.post_run_count += 1
        await self.commit()


class _FakeTeamSession:
    def __init__(self, member_session):
        self.member_session = member_session
        self.created: list[tuple[str, bool]] = []

    def create_agent_session(self, *, agent_id, share_stream_writer):
        self.created.append((agent_id, share_stream_writer))
        return self.member_session


def _saved_state(thread_id: str):
    return {
        "external_runtime": {
            "backend": "codex",
            "external_session_id": thread_id,
        }
    }


def _runtime(
    *,
    thread,
    thread_id=None,
    resume_error=None,
    member_state=None,
    turn_idle_timeout_s=180.0,
    turn_idle_retries=1,
):
    client = _FakeAsyncCodex(config=None, thread=thread, resume_error=resume_error)
    sdk = SimpleNamespace(AsyncCodex=lambda *, config: client)
    member_session = _FakeMemberSession(
        member_state if member_state is not None else (_saved_state(thread_id) if thread_id else None)
    )
    team_session = _FakeTeamSession(member_session)
    runtime = CodexSdkRuntime(
        member_name="developer",
        member_agent_id="team_developer",
        sdk=sdk,
        config=SimpleNamespace(name="config"),
        thread_options={
            "ephemeral": False,
            "cwd": "/workspace",
            "developer_instructions": "role prompt",
        },
        resume_external_backend=thread_id is not None,
        turn_idle_timeout_s=turn_idle_timeout_s,
        turn_idle_retries=turn_idle_retries,
    )
    runtime._test_team_session = team_session
    runtime._test_member_session = member_session
    return runtime, client


async def _start(runtime):
    await runtime.start(team_session=runtime._test_team_session)


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

    await _start(runtime)
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


@pytest.mark.parametrize(
    "result",
    [
        pytest.param([], id="empty-list"),
        pytest.param({}, id="empty-dict"),
        pytest.param(0, id="zero"),
        pytest.param("", id="empty-string"),
        pytest.param(False, id="false"),
    ],
)
@pytest.mark.level0
def test_codex_sdk_runtime_preserves_falsy_mcp_tool_results(result):
    item = SimpleNamespace(
        type="mcpToolCall",
        result=result,
        error={"message": "must not replace a valid falsy result"},
    )

    actual = _tool_result(item)

    assert actual == result
    assert type(actual) is type(result)


@pytest.mark.level0
def test_codex_sdk_runtime_uses_mcp_error_when_result_is_none():
    error = {"message": "tool failed"}
    item = SimpleNamespace(type="mcpToolCall", result=None, error=error)

    assert _tool_result(item) == error


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_resumes_saved_thread_id_without_ephemeral():
    thread = _FakeThread("thread-saved", [[]])
    runtime, client = _runtime(thread=thread, thread_id="thread-saved")

    await _start(runtime)

    assert client.start_calls == []
    assert client.resume_calls == [
        (
            "thread-saved",
            {"cwd": "/workspace", "developer_instructions": "role prompt"},
        )
    ]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_resume_failure_never_starts_replacement_thread():
    thread = _FakeThread("thread-saved", [[]])
    runtime, client = _runtime(
        thread=thread,
        thread_id="thread-saved",
        resume_error=RuntimeError("thread missing"),
    )

    with pytest.raises(RuntimeError, match="strict resume forbids"):
        await _start(runtime)

    assert client.resume_calls == [
        (
            "thread-saved",
            {"cwd": "/workspace", "developer_instructions": "role prompt"},
        )
    ]
    assert client.start_calls == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_rejects_unexpected_resumed_thread_id():
    thread = _FakeThread("thread-other", [[]])
    runtime, client = _runtime(thread=thread, thread_id="thread-saved")

    with pytest.raises(RuntimeError, match="resumed unexpected thread"):
        await _start(runtime)

    assert runtime._thread is None
    assert runtime.session_id == "thread-saved"
    assert client.start_calls == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_checkpoints_new_thread_id_once():
    thread = _FakeThread("thread-new", [[], []])
    runtime, _ = _runtime(thread=thread)

    await _start(runtime)
    await _start(runtime)
    _ = [chunk async for chunk in runtime._drive({"query": "first"})]

    assert runtime._test_member_session.state == _saved_state("thread-new")
    assert runtime._test_member_session.commit_count == 1
    assert runtime._test_team_session.created == [("team_developer", False)]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_does_not_rewrite_restored_thread_id():
    thread = _FakeThread("thread-saved", [[]])
    runtime, _ = _runtime(thread=thread, thread_id="thread-saved")

    await _start(runtime)

    assert runtime._test_member_session.commit_count == 0


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_strict_resume_rejects_missing_member_checkpoint():
    thread = _FakeThread("thread-new", [[]])
    runtime, client = _runtime(thread=thread)
    runtime._resume_external_backend = True

    with pytest.raises(RuntimeError, match="strict resume forbids"):
        await _start(runtime)

    assert client.start_calls == []
    assert client.resume_calls == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_ignores_other_backend_checkpoint_on_strict_resume():
    thread = _FakeThread("thread-new", [[]])
    runtime, _ = _runtime(
        thread=thread,
        member_state={
            "external_runtime": {
                "backend": "claude",
                "external_session_id": "claude-session",
            }
        },
    )
    runtime._resume_external_backend = True

    with pytest.raises(RuntimeError, match="strict resume forbids"):
        await _start(runtime)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_steers_and_interrupts_active_turn():
    thread = _FakeThread("thread-developer", [[]])
    runtime, client = _runtime(thread=thread)
    await _start(runtime)
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
    await _start(runtime)

    with pytest.raises(RuntimeError, match="codex SDK turn failed"):
        async for _ in runtime._drive({"query": "fail"}):
            pass


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_retries_silent_turn_on_same_thread():
    stalled = _BlockingTurnHandle()
    recovered = _FakeTurnHandle(
        [
            _notification("item/agentMessage/delta", delta="recovered"),
            _notification("turn/completed", turn=SimpleNamespace(status="completed")),
        ]
    )
    thread = _HandleSequenceThread("thread-developer", [stalled, recovered])
    runtime, client = _runtime(
        thread=thread,
        turn_idle_timeout_s=0.01,
        turn_idle_retries=1,
    )
    await _start(runtime)

    chunks = await asyncio.wait_for(_collect_drive(runtime, "same prompt"), timeout=1.0)

    assert [chunk.payload["content"] for chunk in chunks] == ["recovered"]
    assert thread.prompts == ["same prompt", "same prompt"]
    assert stalled.interrupt_count == 1
    assert len(client.start_calls) == 1
    assert client.resume_calls == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_does_not_replay_turn_after_any_notification():
    stalled = _NotificationThenBlockingTurnHandle()
    unused = _FakeTurnHandle([])
    thread = _HandleSequenceThread("thread-developer", [stalled, unused])
    runtime, _ = _runtime(
        thread=thread,
        turn_idle_timeout_s=0.01,
        turn_idle_retries=1,
    )
    await _start(runtime)
    chunks = []

    with pytest.raises(RuntimeError, match="produced no turn events"):
        async for chunk in runtime._drive({"query": "do not replay"}):
            chunks.append(chunk)

    assert [chunk.payload["content"] for chunk in chunks] == ["started"]
    assert thread.prompts == ["do not replay"]
    assert stalled.interrupt_count == 1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_bounds_silent_turn_retries():
    first = _BlockingTurnHandle()
    second = _BlockingTurnHandle()
    thread = _HandleSequenceThread("thread-developer", [first, second])
    runtime, _ = _runtime(
        thread=thread,
        turn_idle_timeout_s=0.01,
        turn_idle_retries=1,
    )
    await _start(runtime)

    with pytest.raises(RuntimeError, match="produced no turn events"):
        async for _ in runtime._drive({"query": "bounded"}):
            pass

    assert thread.prompts == ["bounded", "bounded"]
    assert first.interrupt_count == 1
    assert second.interrupt_count == 1


async def _collect_drive(runtime, query):
    return [chunk async for chunk in runtime._drive({"query": query})]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_queues_follow_up_for_same_thread():
    thread = _FakeThread("thread-developer", [[], []])
    runtime, _ = _runtime(thread=thread)
    await _start(runtime)
    await runtime.follow_up("next")

    chunks = [chunk async for chunk in runtime._drive({"query": "first"})]

    assert chunks == []
    assert thread.prompts == ["first", "next"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_stop_does_not_wait_for_stuck_stream():
    thread = _BlockingThread("thread-developer")
    runtime, client = _runtime(thread=thread)
    await _start(runtime)
    await runtime.send("long task")
    await thread.handle.streaming.wait()

    await asyncio.wait_for(runtime.stop(), timeout=1.0)

    assert thread.handle.interrupt_count == 1
    assert client.close_count == 1
    assert runtime._test_member_session.post_run_count == 1

    await runtime.stop()
    assert runtime._test_member_session.post_run_count == 1
