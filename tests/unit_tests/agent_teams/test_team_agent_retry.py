# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the forward-layer transient retry in ``StreamController``.

Retry now lives on the output-forwarding path: ``_forward_outputs`` inspects
each chunk with ``_handle_retry``. A retryable ``task_failed`` frame (code
181001, within the attempt budget) swallows the rest of the failed round and
re-drives it via ``harness.send(_RETRY_QUERY)`` — a follow-up round whose chunks
arrive on the same ``outputs()`` stream once it starts. An exhausted /
non-retryable failure is *forwarded* to the consumer (the single-supervisor
model removed the old raise-based exhaustion). These tests drive
``_forward_outputs`` over a fake runtime so the swallow / re-drive / forward
contract is exercised without a real DeepAgent.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable

import pytest

from openjiuwen.agent_teams.agent import stream_controller as stream_controller_module
from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.team import TeamRole


def _make_failed_chunk(code: int, message: str) -> SimpleNamespace:
    """Build a fake ControllerOutputChunk with a TASK_FAILED payload."""
    return SimpleNamespace(
        payload=SimpleNamespace(
            type="task_failed",
            data=[SimpleNamespace(text=f"[{code}] {message}")],
            metadata={"task_id": "t1"},
        ),
    )


def _make_failed_chunk_raw(text: str) -> SimpleNamespace:
    """Build a fake TASK_FAILED chunk with arbitrary text (no [code] prefix)."""
    return SimpleNamespace(
        payload=SimpleNamespace(
            type="task_failed",
            data=[SimpleNamespace(text=text)],
            metadata={"task_id": "t1"},
        ),
    )


def _make_answer_chunk(text: str) -> SimpleNamespace:
    """Build a fake normal answer chunk."""
    return SimpleNamespace(
        type="answer",
        payload={"output": text, "result_type": "answer"},
    )


class _RetryRuntime:
    """Duck-typed runtime whose ``outputs`` yields a controllable chunk list."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks
        self.sent: list[tuple[Any, bool]] = []
        self.state = HarnessState.IDLE

    def set_chunks(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def outputs(self) -> AsyncIterator[Any]:
        async def _gen() -> AsyncIterator[Any]:
            for chunk in self._chunks:
                yield chunk

        return _gen()

    async def on_state_changed(self, callback: Callable[..., Any]) -> None:
        return None

    async def on_round(self, callback: Callable[..., Any]) -> None:
        return None

    async def send(self, content: Any, *, immediate: bool = False) -> Any:
        self.sent.append((content, immediate))
        return None

    async def abort(self, *, immediate: bool = False) -> None:
        return None

    def has_pending_interrupt(self) -> bool:
        return False

    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool:
        return False


def _make_controller(runtime: _RetryRuntime) -> StreamController:
    """Wire a StreamController over a fake runtime with a live stream_queue."""
    blueprint = SimpleNamespace(member_name="stub", role=TeamRole.LEADER)

    async def _noop(_: Any) -> None:
        return None

    sc = StreamController(
        blueprint_getter=lambda: blueprint,
        state=TeamAgentState(),
        resources=PrivateAgentResources(harness=runtime),
        status_updater=_noop,
        execution_updater=_noop,
    )
    sc.stream_queue = asyncio.Queue()
    return sc


async def _drain_queue(queue: asyncio.Queue) -> list[Any]:
    out: list[Any] = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


@pytest.mark.asyncio
@pytest.mark.level0
async def test_retryable_failure_swallows_round_and_redrives(monkeypatch: pytest.MonkeyPatch) -> None:
    """A retryable task_failed is swallowed (not forwarded) and re-drives the round.

    Trailing frames after the failure stay swallowed for the rest of the round —
    they belong to the doomed attempt and must not reach the consumer.
    """
    warnings: list[tuple] = []
    monkeypatch.setattr(
        stream_controller_module.team_logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    runtime = _RetryRuntime(
        [
            _make_failed_chunk(181001, "model call failed, reason: timeout"),
            _make_answer_chunk("trailing-1"),
            _make_answer_chunk("trailing-2"),
        ],
    )
    sc = _make_controller(runtime)

    await sc._forward_outputs()

    # Failure + trailing frames all swallowed; one retry re-drive sent.
    assert await _drain_queue(sc.stream_queue) == []
    assert runtime.sent == [(stream_controller_module._RETRY_QUERY, False)]
    assert sc._retry_attempt == 1
    assert sc._swallow_failed_round is True
    assert len(warnings) == 1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_retry_round_forwards_after_started_resets_swallow(monkeypatch: pytest.MonkeyPatch) -> None:
    """The re-driven round's chunks forward once a ``started`` event clears the latch.

    Models the real flow: the retry ``send`` starts a follow-up round; its
    ``started`` round event resets ``_swallow_failed_round`` so the round's
    chunks reach the consumer again.
    """
    monkeypatch.setattr(stream_controller_module.team_logger, "warning", lambda *a, **k: None)

    runtime = _RetryRuntime([_make_failed_chunk(181001, "timeout")])
    sc = _make_controller(runtime)

    await sc._forward_outputs()  # swallow latched, retry sent
    assert sc._swallow_failed_round is True

    await sc._map_round("started")  # the retry round begins → latch cleared
    assert sc._swallow_failed_round is False

    runtime.set_chunks([_make_answer_chunk("final")])
    await sc._forward_outputs()  # the retry round's answer now forwards

    chunks = await _drain_queue(sc.stream_queue)
    assert len(chunks) == 1
    assert chunks[0].payload["output"] == "final"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_exhausted_retries_forward_failure_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past the attempt budget the task_failed chunk is forwarded, not raised."""
    errors: list[tuple] = []
    monkeypatch.setattr(stream_controller_module.team_logger, "warning", lambda *a, **k: None)
    monkeypatch.setattr(
        stream_controller_module.team_logger,
        "error",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    failed = _make_failed_chunk(181001, "still timing out")
    runtime = _RetryRuntime([failed])
    sc = _make_controller(runtime)
    sc._retry_attempt = stream_controller_module._MAX_RETRY_ATTEMPTS  # budget already spent

    await sc._forward_outputs()  # must not raise

    forwarded = await _drain_queue(sc.stream_queue)
    assert forwarded == [failed]  # the failure reaches the consumer
    assert runtime.sent == []  # no further re-drive
    assert len(errors) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_non_retryable_code_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-retryable code is forwarded to the consumer (no swallow, no re-drive)."""
    monkeypatch.setattr(stream_controller_module.team_logger, "error", lambda *a, **k: None)

    failed = _make_failed_chunk(182012, "tool execution error, card=X, reason=Y")
    runtime = _RetryRuntime([failed])
    sc = _make_controller(runtime)

    await sc._forward_outputs()

    assert await _drain_queue(sc.stream_queue) == [failed]
    assert runtime.sent == []
    assert sc._swallow_failed_round is False


@pytest.mark.asyncio
@pytest.mark.level1
async def test_missing_code_prefix_is_non_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure with no ``[code]`` prefix is non-retryable and forwarded."""
    monkeypatch.setattr(stream_controller_module.team_logger, "error", lambda *a, **k: None)

    failed = _make_failed_chunk_raw("unexpected error without code")
    runtime = _RetryRuntime([failed])
    sc = _make_controller(runtime)

    await sc._forward_outputs()

    assert await _drain_queue(sc.stream_queue) == [failed]
    assert runtime.sent == []


def test_detect_task_failed_parses_code_and_text() -> None:
    chunk = _make_failed_chunk(181001, "model call failed, reason: timeout")
    result = stream_controller_module._detect_task_failed(chunk)
    assert result is not None
    code, text = result
    assert code == 181001
    assert text == "[181001] model call failed, reason: timeout"


def test_detect_task_failed_returns_none_for_normal_chunk() -> None:
    chunk = _make_answer_chunk("hello")
    assert stream_controller_module._detect_task_failed(chunk) is None


def test_detect_task_failed_none_code_when_no_prefix() -> None:
    chunk = _make_failed_chunk_raw("no prefix here")
    result = stream_controller_module._detect_task_failed(chunk)
    assert result is not None
    code, text = result
    assert code is None
    assert text == "no prefix here"


def test_detect_task_failed_handles_empty_data() -> None:
    chunk = SimpleNamespace(
        payload=SimpleNamespace(type="task_failed", data=[], metadata={}),
    )
    result = stream_controller_module._detect_task_failed(chunk)
    assert result == (None, "")
