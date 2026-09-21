# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Turn boundaries taken from the Claude CLI's delivery receipts."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from openjiuwen.harness_providers.claudecode.lifecycle import (
    SETTLED_SUBTYPE,
    LifecycleTap,
    TurnCycleTracker,
)
from tests.test_logger import logger


class _ScriptedTransport:
    """Replay a recorded frame script, releasing frames on demand."""

    def __init__(self, frames: list[Any]) -> None:
        self.frames = frames
        self.written: list[str] = []
        self.closed = False
        self.ended = False

    async def connect(self) -> None:
        return None

    async def write(self, data: str) -> None:
        self.written.append(data)

    async def read_messages(self):
        for frame in self.frames:
            if callable(frame):
                frame = await frame()
                if frame is None:
                    continue
            yield frame

    async def close(self) -> None:
        self.closed = True

    async def end_input(self) -> None:
        self.ended = True

    def is_ready(self) -> bool:
        return True


def _user(message_id: str, text: str = "go") -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": text},
            "parent_tool_use_id": None,
            "uuid": message_id,
        }
    )


def _receipt(message_id: str, state: str) -> dict[str, Any]:
    return {"type": "command_lifecycle", "command_uuid": message_id, "state": state}


def _result(subtype: str = "success", **extra: Any) -> dict[str, Any]:
    frame = {"type": "result", "subtype": subtype, "is_error": subtype != "success"}
    frame.update(extra)
    return frame


async def _drive(tap: LifecycleTap, *, writes: list[str]) -> list[dict[str, Any]]:
    """Write the outbound frames, then read the stream to its end."""
    for data in writes:
        await tap.write(data)
    return [frame async for frame in tap.read_messages()]


def _settled(frames: list[dict[str, Any]]) -> list[int]:
    return [index for index, frame in enumerate(frames) if frame.get("subtype") == SETTLED_SUBTYPE]


@pytest.mark.asyncio
async def test_a_folded_steer_settles_on_the_single_result() -> None:
    # The CLI was busy: both messages are answered by one cycle.
    transport = _ScriptedTransport(
        [
            _receipt("m1", "queued"),
            _receipt("m1", "started"),
            _receipt("m2", "queued"),
            {"type": "assistant", "user_message_uuid": "m1"},
            _result(num_turns=2),
            _receipt("m1", "completed"),
            _receipt("m2", "completed"),
        ]
    )
    tracker = TurnCycleTracker(ack_timeout_s=5.0)
    tracker.begin_turn("turn-1")
    tap = LifecycleTap(transport, tracker)
    frames = await _drive(tap, writes=[_user("m1"), _user("m2")])
    logger.info("folded frames: %s", [frame.get("type") for frame in frames])
    # The turn ends after the last receipt, not at the result before it.
    assert _settled(frames) == [len(frames) - 1]
    assert frames[-1]["turn_id"] == "turn-1" and frames[-1]["results"] == 1
    assert transport.written == [_user("m1"), _user("m2")]


@pytest.mark.asyncio
async def test_a_steer_answered_as_a_new_cycle_keeps_the_turn_open() -> None:
    # The CLI was idle: the steered message draws a result of its own, and
    # everything it produces still belongs to this turn.
    transport = _ScriptedTransport(
        [
            _receipt("m1", "queued"),
            _receipt("m1", "started"),
            _result(),
            _receipt("m1", "completed"),
            _receipt("m2", "queued"),
            _receipt("m2", "started"),
            {"type": "assistant", "user_message_uuid": "m2"},
            _result(),
            _receipt("m2", "completed"),
        ]
    )
    tracker = TurnCycleTracker(ack_timeout_s=5.0)
    tracker.begin_turn("turn-1")
    tap = LifecycleTap(transport, tracker)
    frames = await _drive(tap, writes=[_user("m1"), _user("m2")])
    settled = _settled(frames)
    assert settled == [len(frames) - 1]
    assert frames[settled[0]]["results"] == 2
    # The second cycle's assistant message reached the consumer before the end.
    assert any(frame.get("type") == "assistant" for frame in frames[: settled[0]])


@pytest.mark.asyncio
async def test_an_interrupted_cycle_settles_without_its_receipts() -> None:
    transport = _ScriptedTransport(
        [
            _receipt("m1", "queued"),
            _receipt("m1", "started"),
            _result(subtype="error_during_execution", terminal_reason="aborted_tools"),
        ]
    )
    tracker = TurnCycleTracker(ack_timeout_s=5.0)
    tracker.begin_turn("turn-1")
    tap = LifecycleTap(transport, tracker)
    frames = await _drive(tap, writes=[_user("m1")])
    assert _settled(frames) == [len(frames) - 1]
    reported = tracker.drain_diagnostics()
    assert len(reported) == 1 and "m1" in reported[0]
    assert tracker.drain_diagnostics() == ()


@pytest.mark.asyncio
async def test_a_cli_without_receipts_settles_on_the_first_result() -> None:
    transport = _ScriptedTransport([{"type": "assistant"}, _result(), {"type": "assistant"}])
    tracker = TurnCycleTracker(ack_timeout_s=5.0)
    tracker.begin_turn("turn-1")
    tap = LifecycleTap(transport, tracker)
    frames = await _drive(tap, writes=[_user("m1")])
    # Settled right after the result, with the trailing frame still forwarded.
    assert _settled(frames) == [2]
    assert not tracker.lifecycle_seen


@pytest.mark.asyncio
async def test_a_message_the_cli_never_acknowledges_times_out() -> None:
    released = asyncio.Event()

    async def _blocked() -> None:
        await released.wait()
        return None

    transport = _ScriptedTransport(
        [
            _receipt("m1", "queued"),
            _receipt("m1", "started"),
            _result(),
            _receipt("m1", "completed"),
            _blocked,
        ]
    )
    tracker = TurnCycleTracker(ack_timeout_s=0.05)
    tracker.begin_turn("turn-1")
    tap = LifecycleTap(transport, tracker)
    await tap.write(_user("m1"))
    frames: list[dict[str, Any]] = []
    async for frame in tap.read_messages():
        frames.append(frame)
        if frame.get("type") == "result":
            # Steer a message the CLI will never acknowledge.
            await tap.write(_user("m2"))
        if frame.get("subtype") == SETTLED_SUBTYPE:
            break
    released.set()
    assert frames[-1]["subtype"] == SETTLED_SUBTYPE
    reported = tracker.drain_diagnostics()
    assert len(reported) == 1 and "m2" in reported[0]


@pytest.mark.asyncio
async def test_a_transport_failure_reaches_the_consumer() -> None:
    async def _explode() -> None:
        raise RuntimeError("transport died")

    transport = _ScriptedTransport([{"type": "assistant"}, _explode])
    tracker = TurnCycleTracker(ack_timeout_s=5.0)
    tracker.begin_turn("turn-1")
    tap = LifecycleTap(transport, tracker)
    with pytest.raises(RuntimeError, match="transport died"):
        await _drive(tap, writes=[_user("m1")])


@pytest.mark.asyncio
async def test_frames_between_turns_are_forwarded_untouched() -> None:
    transport = _ScriptedTransport([_result(), _receipt("m1", "completed")])
    tracker = TurnCycleTracker(ack_timeout_s=5.0)
    tap = LifecycleTap(transport, tracker)
    # No turn is being tracked: a stray receipt must not fabricate an ending.
    frames = await _drive(tap, writes=[_user("m1")])
    assert _settled(frames) == []
    assert [frame["type"] for frame in frames] == ["result", "command_lifecycle"]


@pytest.mark.asyncio
async def test_the_tap_delegates_the_rest_of_the_transport() -> None:
    transport = _ScriptedTransport([])
    tap = LifecycleTap(transport, TurnCycleTracker(ack_timeout_s=5.0))
    await tap.connect()
    assert tap.is_ready() and tap.inner is transport
    await tap.end_input()
    await tap.close()
    assert transport.ended and transport.closed


def test_sub_agent_messages_are_not_the_turn_s_own() -> None:
    tracker = TurnCycleTracker(ack_timeout_s=5.0)
    tracker.begin_turn("turn-1")
    tracker.note_outbound(
        json.dumps({"type": "user", "message": {"role": "user", "content": "x"}, "parent_tool_use_id": "tool-1", "uuid": "sub"})
    )
    tracker.note_outbound("not json at all")
    tracker.note_inbound(_receipt("m1", "queued"))
    # Only the result is outstanding, so the first one settles the turn.
    assert tracker.note_inbound(_result()) is not None
