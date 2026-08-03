# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Opt-in tracing for individual stream chunks.

Every chunk crossing a stream is handled three times — queued by the
producer, dequeued by the queue, consumed by the writer manager — and each
step used to emit its own DEBUG line. A single short reply produces roughly a
hundred chunks, so turning DEBUG on to investigate anything else buried the
log under several hundred lines that carry no information beyond "a chunk
moved": measured on one ``你好`` turn, 300 of 459 lines were these three
messages.

Per-chunk tracing is therefore off by default and the stream reports one
summary line per stream instead. Set ``OPENJIUWEN_LOG_STREAM_CHUNKS=1`` to get
the per-chunk lines back while debugging stream plumbing itself.
"""

import os
from typing import Any

from openjiuwen.core.common.logging import session_logger, LogEventType

# Environment switch, read per call so it can be flipped on a running process.
_STREAM_CHUNK_LOG_ENV_VAR = "OPENJIUWEN_LOG_STREAM_CHUNKS"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def is_chunk_logging_enabled() -> bool:
    """Return whether per-chunk stream tracing is switched on.

    Returns:
        True when ``OPENJIUWEN_LOG_STREAM_CHUNKS`` holds a true-ish value.
    """
    return os.getenv(_STREAM_CHUNK_LOG_ENV_VAR, "0").strip().lower() in _TRUE_VALUES


def log_stream_chunk(message: str, **metadata: Any) -> None:
    """Trace one chunk-level stream event, unless the switch is off.

    Args:
        message: Log message describing the step the chunk reached.
        **metadata: Structured fields attached to the stream-chunk event.
    """
    if not is_chunk_logging_enabled():
        return
    session_logger.debug(
        message,
        event_type=LogEventType.SESSION_STREAM_CHUNK,
        metadata=metadata,
    )


def log_stream_summary(message: str, **metadata: Any) -> None:
    """Report a whole stream's chunk accounting in one line.

    Emitted once per stream regardless of the per-chunk switch, so the volume
    a stream carried stays visible at DEBUG without the per-chunk noise.

    Args:
        message: Log message describing which stream finished.
        **metadata: Structured fields, typically the chunk counters.
    """
    session_logger.debug(
        message,
        event_type=LogEventType.SESSION_STREAM_CHUNK,
        metadata=metadata,
    )


__all__ = ["is_chunk_logging_enabled", "log_stream_chunk", "log_stream_summary"]
