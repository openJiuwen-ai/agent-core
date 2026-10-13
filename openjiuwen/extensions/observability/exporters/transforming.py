# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exporter-side span transformation adapters.

The collection layer records only backend-neutral canonical attributes.
Adapters built on :class:`TransformingSpanExporter` derive backend-specific
projections (e.g. ``langfuse.*``) on the export path, after the span has
ended, so the derived fields never consume collection-time ``SpanLimits``
quota and never mutate the original span.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from openjiuwen.core.common.logging import logger

#: A read-only span transform: returns a new span, never mutates the input.
SpanTransform = Callable[[ReadableSpan], ReadableSpan]


def clone_readable_span(
    span: ReadableSpan,
    attributes: Mapping[str, Any],
) -> ReadableSpan:
    """Return a full copy of ``span`` with ``attributes`` replacing its own.

    Identity is preserved verbatim: trace/span id, parent context, resource,
    events, links, kind, status, timestamps and instrumentation scope all
    reference the original values. Only the attribute mapping is replaced.
    """
    return ReadableSpan(
        name=span.name,
        context=span.get_span_context(),
        parent=span.parent,
        resource=span.resource,
        attributes=dict(attributes),
        events=span.events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class TransformingSpanExporter(SpanExporter):
    """Wrap one exporter behind a per-span read-only transformation.

    Each ended span is copied and handed to ``transform``; the wrapped
    exporter only ever sees the projected copies. A transform failure is
    never allowed to drop telemetry: the failure is logged as a warning and
    the original, untransformed span is exported instead.

    Only the wrapped exporter is affected — nothing else in the processor
    chain sees the projected spans.
    """

    def __init__(self, exporter: SpanExporter, transform: SpanTransform) -> None:
        self._exporter = exporter
        self._transform = transform

    @property
    def exporter(self) -> SpanExporter:
        """Return the wrapped exporter."""
        return self._exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export one transformed copy per span, falling back to the original."""
        projected: list[ReadableSpan] = []
        for span in spans or ():
            try:
                projected.append(self._transform(span))
            except Exception as exc:
                logger.warning(
                    "otel: span transform {} failed for span {} - exporting original: {}",
                    getattr(self._transform, "__name__", type(self._transform).__name__),
                    getattr(span, "name", "<unknown>"),
                    exc,
                )
                projected.append(span)
        return self._exporter.export(projected)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Delegate to the wrapped exporter."""
        return self._exporter.force_flush(timeout_millis=timeout_millis)

    def shutdown(self) -> None:
        """Delegate to the wrapped exporter."""
        self._exporter.shutdown()


__all__ = [
    "SpanTransform",
    "TransformingSpanExporter",
    "clone_readable_span",
]
