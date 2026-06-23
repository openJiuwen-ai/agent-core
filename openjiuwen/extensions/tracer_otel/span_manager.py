# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Span state management for tracer_otel extension.

Manages invoke_id → OtelSpanState mappings and buffered incremental data.
Uses instance-level dicts (not contextvars) because tracer handlers run
sequentially within the same async context in a single session/Tracer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from opentelemetry import trace


@dataclass
class OtelSpanState:
    """Wraps an OTel span with its context token, invoke_id, and cached start_time."""

    span: trace.Span
    context_token: object
    invoke_id: str
    start_time: datetime | None = None  # cached start_time for elapsed calculation


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

    def push(self, invoke_id: str, state: OtelSpanState) -> None:
        self._spans[invoke_id] = state
        self._on_invoke_data[invoke_id] = []
        self._stream_inputs[invoke_id] = []
        self._stream_outputs[invoke_id] = []

    def pop(self, invoke_id: str) -> OtelSpanState | None:
        self._on_invoke_data.pop(invoke_id, None)
        self._stream_inputs.pop(invoke_id, None)
        self._stream_outputs.pop(invoke_id, None)
        return self._spans.pop(invoke_id, None)

    def get(self, invoke_id: str) -> OtelSpanState | None:
        return self._spans.get(invoke_id)

    # --- Incremental data buffers ---

    def append_on_invoke_data(self, invoke_id: str, data: dict) -> None:
        buf = self._on_invoke_data.get(invoke_id)
        if buf is not None:
            buf.append(data)

    def get_on_invoke_data(self, invoke_id: str) -> list[dict]:
        return self._on_invoke_data.get(invoke_id, [])

    def append_stream_input(self, invoke_id: str, chunk: dict) -> None:
        buf = self._stream_inputs.get(invoke_id)
        if buf is not None:
            buf.append(chunk)

    def get_stream_inputs(self, invoke_id: str) -> list:
        return self._stream_inputs.get(invoke_id, [])

    def append_stream_output(self, invoke_id: str, chunk: dict) -> None:
        buf = self._stream_outputs.get(invoke_id)
        if buf is not None:
            buf.append(chunk)

    def get_stream_outputs(self, invoke_id: str) -> list:
        return self._stream_outputs.get(invoke_id, [])
