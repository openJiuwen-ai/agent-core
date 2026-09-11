# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The search's loggers, as children of the host's.

This replaces the sidecar's own logging setup, which opened a rotating file
under `data/logs/evolve.log` and set `propagate = False` so nothing would bubble
into uvicorn's root logger. Both were right for a separate process and are
wrong in this one: a library that captures its own file and refuses to
propagate goes silent in the log its host is actually reading.

The redaction survives the move, as a filter rather than a formatter -- a
formatter belongs to a handler, and this module no longer owns one. It is not
decoration: the search is handed a model key, and a stack trace that echoes a
request header would put it on disk.
"""

from __future__ import annotations

import logging
import re

_ROOT = "openjiuwen.rsi.program_opt"

_SENSITIVE_ASSIGNMENT = re.compile(
    r'''\b(authorization|api[-_]?key|token|password|secret)\b["']?\s*[:=]\s*["']?(?:bearer\s+)?[^\s,;"'}]+''',
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"\bbearer\s+[a-z0-9._~+/=-]+", re.IGNORECASE)


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Rendered here rather than left to the handler: the arguments are as
        # likely to carry the key as the format string is.
        message = record.getMessage()
        redacted = _BEARER_VALUE.sub("Bearer [REDACTED]", _SENSITIVE_ASSIGNMENT.sub(r"\1=[REDACTED]", message))
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


def get_logger(name: str | None = None) -> logging.Logger:
    logger = logging.getLogger(_ROOT if name is None else f"{_ROOT}.{name}")
    if not any(isinstance(existing, _Redact) for existing in logger.filters):
        logger.addFilter(_Redact())
    return logger


__all__ = ["get_logger"]
