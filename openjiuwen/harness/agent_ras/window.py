# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Sliding-window counters and stable call hashing for detectors (single-Agent).

Shared algorithm utilities (100% reused from develop branch):
  - ``SlidingWindowCounter``: time-bucketed event counting for error-rate /
    frequency sampling. Avoids the boundary spikes of fixed windows.
  - ``stable_call_hash``: order-independent hash of a tool call so that
    ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` collide, preventing repeat
    detection from missing reordered-argument calls.
  - ``stable_result_hash``: order-independent hash of a tool result.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any


class SlidingWindowCounter:
    """Count events within a trailing time window.

    Keeps event timestamps in a deque and evicts those older than
    ``window_seconds`` before the reference time on every operation. Callers
    pass the current timestamp explicitly so the counter stays deterministic
    and testable (no internal clock).
    """

    def __init__(self, window_seconds: float) -> None:
        """Initialize the counter.

        Args:
            window_seconds: Trailing window width in seconds.
        """
        self._window = window_seconds
        self._events: deque[float] = deque()

    def add(self, ts: float) -> int:
        """Record an event at ``ts`` and return the in-window count.

        Args:
            ts: Event timestamp in seconds.

        Returns:
            Number of events within the trailing window, including this one.
        """
        self._events.append(ts)
        self._evict(ts)
        return len(self._events)

    def reset(self) -> None:
        """Drop all recorded events."""
        self._events.clear()

    def _evict(self, ts: float) -> None:
        """Evict events older than the trailing window relative to ``ts``."""
        cutoff = ts - self._window
        while self._events and self._events[0] < cutoff:
            self._events.popleft()


def stable_call_hash(tool_name: str, tool_args: dict[str, Any] | None) -> str:
    """Hash a tool call so argument order never affects the result.

    ``json.dumps(sort_keys=True)`` sorts keys recursively, so nested dicts are
    order-independent too. ``default=str`` tolerates non-serializable argument
    values without raising.

    Args:
        tool_name: The tool identifier.
        tool_args: The tool arguments, or None.

    Returns:
        A hex digest of the tool name plus its recursively key-sorted args.
    """
    payload = json.dumps(
        {"tool": tool_name, "args": tool_args or {}},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_result_hash(result: Any) -> str:
    """Hash a tool result into a stable digest for loop detection.

    Lets repeat detection distinguish "same call, same result" (a real loop)
    from "same call, different result" (legitimate iteration). Prefers a
    Pydantic ``model_dump`` (e.g. ToolOutput), then JSON with sorted keys, and
    falls back to ``str`` so unusual objects never raise. ``default=str``
    tolerates non-serializable values.

    Args:
        result: The tool execution result, or None.

    Returns:
        A hex digest, or "none" when result is None.
    """
    if result is None:
        return "none"
    if isinstance(result, str):
        payload = result
    elif hasattr(result, "model_dump"):
        try:
            payload = json.dumps(result.model_dump(), sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            payload = str(result)
    else:
        try:
            payload = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            payload = str(result)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
