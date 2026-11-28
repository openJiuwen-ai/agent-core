# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Span state management for tracer_otel extension.

Manages invoke_id → OtelSpanState mappings and buffered incremental data.
Uses instance-level dicts (not contextvars) because tracer handlers run
sequentially within the same async context in a single session/Tracer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from opentelemetry import trace

from openjiuwen.extensions.tracer_otel.semconv import (
    GEN_AI_TOOL_NAME,
    OJ_TRACE_ID,
)

# Incremental-buffer bounds (issue #2320). Capped at the collection stage so a
# long stream never grows an unbounded per-frame list before serialization.
# Thresholds are deliberate defaults; kept hardcoded rather than exposed as
# OtelTracerConfig fields until real capacity requirements are measured.
_MAX_STREAM_FRAMES = 100
_MAX_STREAM_CHUNK_BYTES = 2048
_MAX_STREAM_BUFFER_BYTES = 65536

# Incremental-buffer kind keys (used by _byte_counts / _dropped accounting).
_KIND_ON_INVOKE_DATA = "on_invoke_data"
_KIND_STREAM_INPUTS = "stream_inputs"
_KIND_STREAM_OUTPUTS = "stream_outputs"


def bounded_append(buf: list, kind: str, chunk: dict, byte_counts: dict, dropped: dict) -> None:
    """Bounded append for an incremental buffer.

    Shared by ``OtelWorkflowSpanManager`` and the deferred end-node window so
    both collection paths enforce the same frame / single-chunk / total-byte
    caps. On overflow the frame is dropped and counted, never truncated in
    place.
    """
    if len(buf) >= _MAX_STREAM_FRAMES:
        dropped[kind] = dropped.get(kind, 0) + 1
        return
    try:
        chunk_bytes = len(json.dumps(chunk, default=str, ensure_ascii=False))
    except (TypeError, ValueError):
        chunk_bytes = len(str(chunk))
    if chunk_bytes > _MAX_STREAM_CHUNK_BYTES:
        dropped[kind] = dropped.get(kind, 0) + 1
        return
    if byte_counts[kind] + chunk_bytes > _MAX_STREAM_BUFFER_BYTES:
        dropped[kind] = dropped.get(kind, 0) + 1
        return
    buf.append(chunk)
    byte_counts[kind] += chunk_bytes


@dataclass
class OtelSpanState:
    """Wraps an OTel span with its context token, invoke_id, and cached start_time."""

    span: trace.Span
    context_token: object
    invoke_id: str
    start_time: datetime | None = None  # cached start_time for elapsed calculation
    # Reference to the inputs dict from on_pre_invoke. Used by on_invoke /
    # on_call_done to re-serialize OJ_WORKFLOW_INPUTS after transform callbacks
    # (e.g. resolve_global_vars_transform) have mutated the inputs in place.
    # Holds a reference, not a copy, so mutations are visible.
    inputs: Any | None = None
    # Track which workflow this span belongs to for cross-workflow isolation.
    workflow_id: str | None = None
    # Marks tool spans emitted by OtelRail's tool hooks, as opposed to the
    # legacy session-tracer decorator's tool event. Used by
    # OtelAgentSpanManager.has_open_tool_span; deliberately an internal
    # field, not an exported attribute — gen_ai.tool.call.id is only
    # written when the model supplied an id, but the dedupe must also
    # cover id-less tool calls (bug #2046).
    from_tool_rail: bool = False
    # P0 perf: cache is_recording() result at construction time.
    # Non-recording spans (the vast majority under low sample rates) skip
    # all attribute serialisation and stream buffering.
    recorded: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        self.recorded = self.span.is_recording()


class OtelAgentSpanManager:
    """Manage invoke_id → OtelSpanState for agent handlers.

    Used to establish parent-child relationships: when creating a child
    span, look up the parent span's invoke_id and use its OTel context
    as parent context.
    """

    def __init__(self):
        self._spans: dict[str, OtelSpanState] = {}

    def push(self, invoke_id: str, state: OtelSpanState) -> None:
        self._spans[invoke_id] = state

    def pop(self, invoke_id: str) -> OtelSpanState | None:
        return self._spans.pop(invoke_id, None)

    def get(self, invoke_id: str) -> OtelSpanState | None:
        return self._spans.get(invoke_id)

    def has_open_tool_span(self, tool_name: str, trace_id: str = "") -> bool:
        """Whether an OtelRail-emitted tool span is still open.

        Rail emission is identified by the ``from_tool_rail`` span-state
        flag (set in OtelAgentHandler.on_plugin_start) — not by attribute
        presence, because ``gen_ai.tool.call.id`` is only written when the
        model supplied an id while the dedupe must also cover id-less
        calls. Matching is by ``gen_ai.tool.name`` attribute, not span
        name — display naming differs across release branches while the
        attributes do not. Scoping by trace id keeps concurrent agents in
        different traces from matching each other. Used to drop the
        decorator's duplicate tool event for the same physical call
        (bug #2046). ``getattr`` defensively: non-recording spans expose
        neither ``name`` nor ``attributes``.
        """
        for state in list(self._spans.values()):
            if not state.from_tool_rail:
                continue
            attributes = getattr(state.span, "attributes", None) or {}
            if str(attributes.get(GEN_AI_TOOL_NAME, "")) != tool_name:
                continue
            if trace_id and str(attributes.get(OJ_TRACE_ID, "")) != trace_id:
                continue
            return True
        return False


class OtelWorkflowSpanManager:
    """Manage invoke_id → OtelSpanState for workflow handlers plus
    buffered incremental data.

    Incremental data (on_invoke_data, stream_inputs, stream_outputs) is
    buffered as lists and flushed as single OTel span events on
    on_call_done, avoiding many small individual events.
    """

    def __init__(self):
        self._spans: dict[str, OtelSpanState] = {}
        # Buffered incremental data: invoke_id → list
        self._on_invoke_data: dict[str, list[dict]] = {}
        self._stream_inputs: dict[str, list] = {}
        self._stream_outputs: dict[str, list] = {}
        # Bounded-buffer accounting (issue #2320): invoke_id → kind → value
        self._byte_counts: dict[str, dict[str, int]] = {}
        self._dropped: dict[str, dict[str, int]] = {}

    def push(self, invoke_id: str, state: OtelSpanState) -> None:
        self._spans[invoke_id] = state
        self._on_invoke_data[invoke_id] = []
        self._stream_inputs[invoke_id] = []
        self._stream_outputs[invoke_id] = []
        self._byte_counts[invoke_id] = {
            _KIND_ON_INVOKE_DATA: 0,
            _KIND_STREAM_INPUTS: 0,
            _KIND_STREAM_OUTPUTS: 0,
        }
        self._dropped[invoke_id] = {
            _KIND_ON_INVOKE_DATA: 0,
            _KIND_STREAM_INPUTS: 0,
            _KIND_STREAM_OUTPUTS: 0,
        }

    def pop(self, invoke_id: str) -> OtelSpanState | None:
        self._on_invoke_data.pop(invoke_id, None)
        self._stream_inputs.pop(invoke_id, None)
        self._stream_outputs.pop(invoke_id, None)
        self._byte_counts.pop(invoke_id, None)
        self._dropped.pop(invoke_id, None)
        return self._spans.pop(invoke_id, None)

    def get(self, invoke_id: str) -> OtelSpanState | None:
        return self._spans.get(invoke_id)

    def has_spans(self) -> bool:
        """Whether any span state is currently retained."""
        return bool(self._spans)

    def discard_all(self) -> list[OtelSpanState]:
        """Remove and return every span state and buffered payload.

        Used when an execution-scoped handler is evicted (issue #2430 review):
        the caller ends/detaches the returned spans so their attributes stop
        being retained by the handler.
        """
        states = list(self._spans.values())
        self._spans.clear()
        self._on_invoke_data.clear()
        self._stream_inputs.clear()
        self._stream_outputs.clear()
        self._byte_counts.clear()
        self._dropped.clear()
        return states

    # --- Incremental data buffers ---

    def _try_append(self, invoke_id: str, kind: str, buf: list, chunk: dict) -> None:
        """Bounded append for incremental buffers (delegates to shared helper)."""
        counts = self._byte_counts.get(invoke_id)
        if counts is None:
            return
        bounded_append(buf, kind, chunk, counts, self._dropped[invoke_id])

    def merge_dropped_counts(self, invoke_id: str, dropped: dict) -> None:
        """Merge deferred-window dropped counts into this span's dropped counts."""
        target = self._dropped.get(invoke_id)
        if target is None:
            return
        for kind, count in dropped.items():
            if count:
                target[kind] = target.get(kind, 0) + count

    def get_dropped_counts(self, invoke_id: str) -> dict[str, int]:
        """Per-kind dropped-frame counts, or an empty dict when none were dropped."""
        dropped = self._dropped.get(invoke_id)
        if not dropped:
            return {}
        return {kind: count for kind, count in dropped.items() if count > 0}

    def append_on_invoke_data(self, invoke_id: str, data: dict) -> None:
        state = self._spans.get(invoke_id)
        if state is None or not state.recorded:
            return
        buf = self._on_invoke_data.get(invoke_id)
        if buf is not None:
            self._try_append(invoke_id, _KIND_ON_INVOKE_DATA, buf, data)

    def get_on_invoke_data(self, invoke_id: str) -> list[dict]:
        return self._on_invoke_data.get(invoke_id, [])

    def append_stream_input(self, invoke_id: str, chunk: dict) -> None:
        state = self._spans.get(invoke_id)
        if state is None or not state.recorded:
            return
        buf = self._stream_inputs.get(invoke_id)
        if buf is not None:
            self._try_append(invoke_id, _KIND_STREAM_INPUTS, buf, chunk)

    def get_stream_inputs(self, invoke_id: str) -> list:
        return self._stream_inputs.get(invoke_id, [])

    def append_stream_output(self, invoke_id: str, chunk: dict) -> None:
        state = self._spans.get(invoke_id)
        if state is None or not state.recorded:
            return
        buf = self._stream_outputs.get(invoke_id)
        if buf is not None:
            self._try_append(invoke_id, _KIND_STREAM_OUTPUTS, buf, chunk)

    def get_stream_outputs(self, invoke_id: str) -> list:
        return self._stream_outputs.get(invoke_id, [])
