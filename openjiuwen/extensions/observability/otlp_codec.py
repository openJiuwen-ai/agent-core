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

from openjiuwen.extensions.observability.content_addressing import (
    AddressedSequence,
    addressable_attributes,
    build_sequence,
    sequence_reference,
)
from openjiuwen.extensions.observability.gen_ai_semconv import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_TOOL_DEFINITIONS,
)


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


# The attributes a conversation restates on every call. Each is a sequence:
# an array is its own, and a scalar is a sequence of one, so the storage path
# never branches on which shape an attribute happens to carry.
ADDRESSED_ATTRIBUTE_KEYS = frozenset({
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_TOOL_DEFINITIONS,
})


def encode_span_with_addressed_sequences(
    span: ReadableSpan,
) -> tuple[bytes, tuple[AddressedSequence, ...]]:
    """Encode one span with its restated attributes replaced by references.

    The exporter path keeps receiving the complete span; this is the storage
    path, which has no obligation to repeat what it already holds. The encode
    already builds a decoded document, so addressing costs one pass over its
    attributes rather than a second parse downstream.

    Args:
        span: The frozen span to encode.

    Returns:
        The OTLP JSON carrying references, and every sequence it referenced.
        A reader needs those sequences to rebuild the span.
    """
    request = encode_spans([span])
    payload = json_format.MessageToDict(request, use_integers_for_enums=True)
    _fix_hex_ids(payload)
    sequences: list[AddressedSequence] = []
    for attribute in addressable_attributes(payload, ADDRESSED_ATTRIBUTE_KEYS):
        value = attribute.get("value")
        if not isinstance(value, dict):
            continue
        stated = value.get("stringValue")
        if not isinstance(stated, str):
            continue
        sequence = build_sequence(str(attribute.get("key") or ""), stated)
        if sequence is None:
            continue
        sequences.append(sequence)
        value["stringValue"] = sequence_reference(sequence.seq_hash, sequence.depth)
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return encoded, tuple(sequences)


def snapshot_readable_span(span: Any) -> ReadableSpan:
    """Freeze the current state of one recording span into a ReadableSpan.

    Mutable SDK containers are copied so an asynchronous consumer never observes
    later mutation. This is roughly two orders of magnitude cheaper than encoding
    the span, which lets a caller on a latency-sensitive thread hand the frozen
    span downstream and let the consumer pay for encoding on its own thread.
    """
    return ReadableSpan(
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


def encode_recording_span_snapshot_to_otlp_json(span: Any) -> bytes:
    """Encode the current state of one recording span without an end time.

    The result deliberately has OTLP JSON shape for the local trajectory data
    plane, but it is not an ended span export.
    """
    return _encode_readable_span(snapshot_readable_span(span))


__all__ = [
    "encode_recording_span_snapshot_to_otlp_json",
    "encode_span_to_otlp_json",
    "snapshot_readable_span",
]
