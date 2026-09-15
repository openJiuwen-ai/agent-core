# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Central normalization of error causes into OpenTelemetry span writes.

An exception often carries its real explanation outside ``str(exc)`` — a
bare ``asyncio.TimeoutError()`` renders as an empty string, while the layer
that raised it built a detailed ``error_message`` (stage, timeouts, chunk
counts). Every error span written by the observability layer goes through
this module so the recorded cause follows one priority chain and one
redaction policy:

1. the caller-supplied ``error_message`` when non-blank,
2. a non-blank ``str(exception)``,
3. the exception type name (e.g. ``TimeoutError``),
4. the caller's generic fallback text.

The summary written to ``span.status.description`` is masked for
credentials, collapsed to one line and capped. The real exception object is
still handed to ``record_exception`` so the trace keeps the original
traceback; its ``exception.message`` / ``exception.stacktrace`` attributes
are overridden with masked variants (the SDK lets explicit attributes
replace its defaults) so neither the blank ``str(exc)`` nor the raw stack's
last line can re-leak what the summary hides.
"""

from __future__ import annotations

import traceback
from typing import Any

from opentelemetry.trace import Status, StatusCode

from openjiuwen.extensions.observability.redaction import (
    redact_error_stacktrace,
    redact_error_summary,
)
from openjiuwen.extensions.observability.semconv import ERROR_TYPE


def error_reason(
    *,
    exception: BaseException | None = None,
    error_message: Any = None,
    default: str = "",
) -> str:
    """Resolve the raw error reason through the shared priority chain."""

    if isinstance(error_message, str) and error_message.strip():
        return error_message.strip()
    if exception is not None:
        try:
            rendered = str(exception)
        except Exception:
            rendered = ""
        if rendered.strip():
            return rendered.strip()
        return type(exception).__name__
    return default


def record_span_error(
    span: Any,
    *,
    exception: BaseException | None = None,
    error_message: Any = None,
    default: str = "",
    error_type: str | None = None,
    config: Any = None,
) -> str:
    """Write one error outcome onto a recording span and end it.

    Records the real exception (keeping its traceback) when one is given,
    with ``exception.message`` / ``exception.stacktrace`` replaced by the
    masked summary, sets ``error.type`` and the error status, then ends the
    span.

    Args:
        span: The still-recording OTel span to write on.
        exception: The raised exception, if any.
        error_message: Caller-supplied reason with richer context.
        default: Fallback reason when nothing else resolves.
        error_type: Explicit ``error.type``; defaults to the exception class.
        config: Active observability configuration (optional).

    Returns:
        The redacted single-line reason written to the span status.
    """

    reason = redact_error_summary(
        error_reason(exception=exception, error_message=error_message, default=default),
        config,
    ) or default
    resolved_type = error_type or (
        type(exception).__name__ if exception is not None else None
    )
    if exception is not None:
        # The SDK overrides its own defaults with explicit attributes, so the
        # recorded event keeps the real traceback but never the raw
        # ``str(exception)`` tail that could carry secrets.
        stacktrace = "".join(
            traceback.format_exception(
                type(exception), value=exception, tb=exception.__traceback__
            )
        )
        span.record_exception(
            exception,
            attributes={
                "exception.message": reason,
                "exception.stacktrace": redact_error_stacktrace(stacktrace, config),
            },
        )
    if resolved_type:
        span.set_attribute(ERROR_TYPE, resolved_type)
    span.set_status(Status(StatusCode.ERROR, reason))
    span.end()
    return reason
