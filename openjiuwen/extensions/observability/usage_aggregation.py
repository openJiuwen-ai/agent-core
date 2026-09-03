# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Trace-keyed rollup of LLM/tool facts for root-span stamping."""

from __future__ import annotations

import threading

_ACCUMULATOR: UsageAccumulator | None = None
_LOCK = threading.RLock()


class UsageAccumulator:
    def __init__(self) -> None:
        self._data: dict[int, dict[str, float]] = {}
        self._lock = threading.RLock()

    def _entry(self, trace_id: int) -> dict[str, float]:
        return self._data.setdefault(
            trace_id,
            {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "tool_calls": 0, "tool_errors": 0},
        )

    def accumulate_llm(self, trace_id: int, *, prompt: int, completion: int, cost: float) -> None:
        with self._lock:
            e = self._entry(trace_id)
            e["prompt_tokens"] += prompt
            e["completion_tokens"] += completion
            e["cost"] += cost

    def accumulate_tool(self, trace_id: int, *, is_error: bool) -> None:
        with self._lock:
            e = self._entry(trace_id)
            e["tool_calls"] += 1
            if is_error:
                e["tool_errors"] += 1

    def snapshot(self, trace_id: int) -> dict[str, float]:
        with self._lock:
            return dict(self._data.get(trace_id, {}))

    def clear(self, trace_id: int) -> None:
        with self._lock:
            self._data.pop(trace_id, None)


def get_accumulator() -> UsageAccumulator:
    global _ACCUMULATOR
    with _LOCK:
        if _ACCUMULATOR is None:
            _ACCUMULATOR = UsageAccumulator()
        return _ACCUMULATOR


def drain_rollup(trace_id: int | None) -> dict[str, float]:
    """Snapshot and clear one trace's rollup, returning the snapshot.

    A single accessor for the finalize paths (single-agent run root and team
    root) so neither can forget the ``clear`` — an omission would otherwise
    leak one accumulator entry per trace for the life of the process.

    Returns an empty dict when the trace accumulated nothing (or ``trace_id``
    is None).
    """
    if trace_id is None:
        return {}
    accumulator = get_accumulator()
    snapshot = accumulator.snapshot(trace_id)
    if snapshot:
        accumulator.clear(trace_id)
    return snapshot
