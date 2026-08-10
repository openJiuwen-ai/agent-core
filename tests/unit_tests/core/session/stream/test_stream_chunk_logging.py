# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for opt-in per-chunk stream tracing."""

import pytest

from openjiuwen.core.session.stream import chunk_logging
from openjiuwen.core.session.stream.chunk_logging import (
    is_chunk_logging_enabled,
    log_stream_chunk,
    log_stream_summary,
)


class _RecordingLogger:
    """Stand-in capturing what would have been written to the log."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: str, **kwargs) -> None:
        self.messages.append(msg)


@pytest.fixture(name="recorder")
def _recorder(monkeypatch: pytest.MonkeyPatch) -> _RecordingLogger:
    logger = _RecordingLogger()
    monkeypatch.setattr(chunk_logging, "session_logger", logger)
    return logger


def test_chunk_logging_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-chunk tracing must stay off unless it is asked for."""
    monkeypatch.delenv("OPENJIUWEN_LOG_STREAM_CHUNKS", raising=False)

    assert is_chunk_logging_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_switch_accepts_the_documented_true_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """The env switch takes the same true-ish spellings as the rest of the codebase."""
    monkeypatch.setenv("OPENJIUWEN_LOG_STREAM_CHUNKS", value)

    assert is_chunk_logging_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_switch_rejects_everything_else(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Anything that is not an accepted true value leaves tracing off."""
    monkeypatch.setenv("OPENJIUWEN_LOG_STREAM_CHUNKS", value)

    assert is_chunk_logging_enabled() is False


def test_chunk_log_is_suppressed_when_switched_off(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _RecordingLogger,
) -> None:
    """A disabled switch must drop the line, not merely lower its level."""
    monkeypatch.delenv("OPENJIUWEN_LOG_STREAM_CHUNKS", raising=False)

    log_stream_chunk("Stream data received", data_type="str")

    assert recorder.messages == []


def test_chunk_log_is_emitted_when_switched_on(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _RecordingLogger,
) -> None:
    """Turning the switch on restores the per-chunk line for debugging."""
    monkeypatch.setenv("OPENJIUWEN_LOG_STREAM_CHUNKS", "1")

    log_stream_chunk("Stream data received", data_type="str")

    assert recorder.messages == ["Stream data received"]


def test_summary_ignores_the_switch(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _RecordingLogger,
) -> None:
    """One line per stream stays visible; only the per-chunk flood is gated."""
    monkeypatch.delenv("OPENJIUWEN_LOG_STREAM_CHUNKS", raising=False)

    log_stream_summary("StreamQueue closed", sent_count=3, received_count=3)

    assert recorder.messages == ["StreamQueue closed"]


@pytest.mark.asyncio
async def test_queue_reports_its_chunk_counts_on_close(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _RecordingLogger,
) -> None:
    """Closing a queue accounts for the traffic it carried.

    This is what replaces the per-chunk lines: the volume stays observable at
    DEBUG without one line per chunk per stage.
    """
    monkeypatch.delenv("OPENJIUWEN_LOG_STREAM_CHUNKS", raising=False)
    from openjiuwen.core.session.stream.emitter import AsyncStreamQueue

    queue = AsyncStreamQueue()
    for item in ("a", "b", "c"):
        await queue.send(item)
    for _ in range(2):
        await queue.receive(timeout=1)
    await queue.close(timeout=1)

    assert recorder.messages == ["StreamQueue closed"]
    assert queue._sent_count == 3
    assert queue._received_count == 2
