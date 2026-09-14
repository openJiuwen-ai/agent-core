# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Backend-shaped views derived from the standard GenAI attributes at export.

Instrumentation writes one shape: the OpenTelemetry GenAI semantic
conventions (``gen_ai.input.messages`` / ``gen_ai.output.messages`` /
``gen_ai.system_instructions``), each a single structured value. Backends that
read a different shape are served here instead, by deriving their view from
those attributes on the way out.

Two reasons this belongs at export rather than on the live span:

* A span's attribute count is capped by ``SpanLimits`` and OTel evicts FIFO, so
  a per-message expansion written while recording pushes the request's own
  identity attributes out of the span. Derived at export, the expansion costs
  the span nothing -- there is no limit on the exported payload.
* Instrumentation stays backend-agnostic. Adding a backend is a projection
  here, not another branch in the callback handler.

``SpanProcessor`` cannot host this: ``on_end`` receives a ``ReadableSpan``
whose ``attributes`` is a read-only property, and ``on_start`` runs before the
messages exist.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from openjiuwen.core.common.logging import logger
from openjiuwen.extensions.observability.semconv import (
    GEN_AI_COMPLETION,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_PROMPT,
    GEN_AI_SYSTEM_INSTRUCTIONS,
)

_LANGFUSE_BACKEND = "langfuse"


def _decode(value: Any) -> Any:
    """Decode one structured attribute, tolerating already-decoded values."""

    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _parts_text(parts: Any) -> str:
    """Join the text of one message's structured parts."""

    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        content = part.get("content")
        if isinstance(content, str) and content:
            texts.append(content)
    return "\n".join(texts)


def _message_text(message: Mapping[str, Any]) -> str:
    """Read one message's text, whichever structured shape it arrived in."""

    content = message.get("content")
    if isinstance(content, str):
        return content
    return _parts_text(message.get("parts"))


def _langfuse_prompt_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Expand the standard input attributes into Langfuse's indexed prompt keys.

    Indices are zero-based and contiguous, which is what Langfuse's OTLP
    mapper expects; the system instructions lead, matching the order the model
    actually received.
    """

    derived: dict[str, Any] = {}
    index = 0

    system_parts = _decode(attributes.get(GEN_AI_SYSTEM_INSTRUCTIONS))
    system_text = _parts_text(system_parts)
    if system_text:
        derived[f"{GEN_AI_PROMPT}.{index}.role"] = "system"
        derived[f"{GEN_AI_PROMPT}.{index}.content"] = system_text
        index += 1

    input_messages = _decode(attributes.get(GEN_AI_INPUT_MESSAGES))
    if isinstance(input_messages, list):
        for message in input_messages:
            if not isinstance(message, Mapping):
                continue
            derived[f"{GEN_AI_PROMPT}.{index}.role"] = str(message.get("role") or "user")
            derived[f"{GEN_AI_PROMPT}.{index}.content"] = _message_text(message)
            tool_calls = message.get("tool_calls")
            if tool_calls:
                derived[f"{GEN_AI_PROMPT}.{index}.tool_calls"] = json.dumps(
                    tool_calls,
                    ensure_ascii=False,
                    default=str,
                )
            index += 1
    return derived


def _langfuse_completion_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Expand the standard output attribute into Langfuse's completion keys."""

    output_messages = _decode(attributes.get(GEN_AI_OUTPUT_MESSAGES))
    if not isinstance(output_messages, list) or not output_messages:
        return {}
    first = output_messages[0]
    if not isinstance(first, Mapping):
        return {}
    derived: dict[str, Any] = {
        f"{GEN_AI_COMPLETION}.0.role": str(first.get("role") or "assistant"),
        f"{GEN_AI_COMPLETION}.0.content": _message_text(first),
    }
    if str(first.get("role") or "") == "reasoning":
        derived[f"{GEN_AI_COMPLETION}.0.is_reasoning"] = True
    return derived


def project_span_for_langfuse(span: ReadableSpan) -> ReadableSpan:
    """Return the span with Langfuse's indexed prompt/completion keys added.

    The standard attributes are kept as they are; the derived keys are added
    beside them, so one exported span serves both readers.

    Args:
        span: The finished span as recorded.

    Returns:
        A span carrying the derived keys, or the original when there was
        nothing to derive.
    """

    attributes = span.attributes or {}
    derived = _langfuse_prompt_attributes(attributes)
    derived.update(_langfuse_completion_attributes(attributes))
    if not derived:
        return span

    return ReadableSpan(
        name=span.name,
        context=span.get_span_context(),
        parent=span.parent,
        resource=span.resource,
        attributes={**attributes, **derived},
        events=span.events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class BackendProjectingSpanExporter(SpanExporter):
    """Wrap one exporter and hand it the shape its backend reads."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Project each span, then delegate; a failed projection sends as-is."""

        projected: list[ReadableSpan] = []
        for span in spans:
            try:
                projected.append(project_span_for_langfuse(span))
            except Exception as exc:  # noqa: BLE001 - export must not be lost
                logger.warning(
                    "[BackendProjection] langfuse projection skipped for span %s: %s",
                    getattr(span, "name", "<unknown>"),
                    exc,
                )
                projected.append(span)
        return self._inner.export(projected)

    def shutdown(self) -> None:
        """Shut the wrapped exporter down."""

        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush the wrapped exporter."""

        return self._inner.force_flush(timeout_millis)


def project_for_backend(exporter: SpanExporter, backend: str) -> SpanExporter:
    """Wrap the exporter when the configured backend needs a derived shape.

    Args:
        exporter: The exporter selected by configuration.
        backend: The configured observability backend name.

    Returns:
        The exporter, wrapped only for backends that read a non-standard shape.
    """

    if str(backend or "").strip().lower() != _LANGFUSE_BACKEND:
        return exporter
    return BackendProjectingSpanExporter(exporter)
