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
import logging.handlers
import os
import re
from pathlib import Path

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


_SINK_ATTACHED = False


def _sink_path() -> Path:
    home = (
        os.environ.get("JIUWENSWARM_HOME")
        or os.environ.get("HOME")
        or os.path.expanduser("~")
    )
    return Path(home) / ".jiuwenswarm" / "rsi" / "logs" / "program_opt.log"


def _attach_sink() -> None:
    # The host's own logging (openjiuwen's LogManager family) never configures
    # the stdlib root this tree bubbles into: the root keeps no handler and
    # WARNING as its level, so an INFO record dies at the logger's inherited
    # effective level and never reaches a handler at all. Keep propagate=True
    # so a host that does configure the root still sees everything; this sink
    # is additive, not a replacement for it.
    global _SINK_ATTACHED
    if _SINK_ATTACHED:
        return
    _SINK_ATTACHED = True
    logger = logging.getLogger(_ROOT)
    # NOTSET would inherit the stdlib root's WARNING and drop INFO here,
    # before any handler -- including ours -- could see it.
    logger.setLevel(logging.INFO)
    try:
        _sink_path().parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            _sink_path(), maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    except OSError:
        # Read-only homes and jailed workers get the old behaviour -- the
        # redaction filter and propagation -- rather than a failed search.
        pass


def get_logger(name: str | None = None) -> logging.Logger:
    _attach_sink()
    logger = logging.getLogger(_ROOT if name is None else f"{_ROOT}.{name}")
    if not any(isinstance(existing, _Redact) for existing in logger.filters):
        logger.addFilter(_Redact())
    return logger


__all__ = ["get_logger"]
