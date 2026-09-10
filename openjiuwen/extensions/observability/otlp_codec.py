# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Canonical single-span OTLP JSON encoding shared by every local consumer."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from google.protobuf import json_format
from opentelemetry.exporter.otlp.proto.common._internal.trace_encoder import encode_spans
from opentelemetry.sdk.trace import ReadableSpan


_HEX_ID_KEYS = frozenset({"traceId", "spanId", "parentSpanId"})


def _b64_to_hex(value: str) -> str:
    """Convert a protobuf-JSON base64 identifier to lower-case hex."""
    try:
        return binascii.hexlify(base64.b64decode(value)).decode()
    except Exception:
        return value


def _fix_hex_ids(node: Any) -> None:
    """Rewrite every OTLP identifier field in *node* in place."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _HEX_ID_KEYS and isinstance(value, str):
                node[key] = _b64_to_hex(value)
            else:
                _fix_hex_ids(value)
    elif isinstance(node, list):
        for item in node:
            _fix_hex_ids(item)


def _encode_readable_span(span: ReadableSpan) -> bytes:
    request = encode_spans([span])
    payload = json_format.MessageToDict(request, use_integers_for_enums=True)
    _fix_hex_ids(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def encode_span_to_otlp_json(span: ReadableSpan) -> bytes:
    """Encode one ended span as one UTF-8 OTLP ``ExportTraceServiceRequest``.

    The returned request contains exactly one span. Trace, span and parent-span
    identifiers use lower-case hex so the bytes are directly replayable by the
    existing JSONL exporter and consumable by the trajectory data plane.
    """
    return _encode_readable_span(span)


def encode_recording_span_snapshot_to_otlp_json(span: Any) -> bytes:
    """Encode the current state of one recording span without an end time.

    The result deliberately has OTLP JSON shape for the local trajectory data
    plane, but it is not an ended span export. Mutable SDK containers are copied
    before encoding so an asynchronous consumer never observes later mutation.
    """
    snapshot = ReadableSpan(
        name=str(span.name),
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=dict(span.attributes or {}),
        events=tuple(span.events or ()),
        links=tuple(span.links or ()),
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=None,
        instrumentation_scope=getattr(span, "instrumentation_scope", None),
    )
    return _encode_readable_span(snapshot)


__all__ = [
    "encode_recording_span_snapshot_to_otlp_json",
    "encode_span_to_otlp_json",
]
