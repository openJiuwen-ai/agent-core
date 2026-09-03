"""Run-scoped logging: correlation context, host pipeline file logs, redaction."""

from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import re
import sys
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    ensure_manager_dir,
    ensure_module_attempt_dir,
    openjiuwen_log_dir,
    pipeline_log_path,
)

SCHEMA_VERSION = 1
DEFAULT_MAX_FIELD_CHARS = 4000
DEFAULT_BACKUP_COUNT = 10
DEFAULT_MAX_BYTES = 10 * 1024 * 1024

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|password|passwd|secret|token|authorization|credential|private[_-]?key)$",
    re.IGNORECASE,
)
_ENCRYPTED_TYPE_MARKERS = frozenset({"reasoning.encrypted", "encrypted"})
_DROP_KEYS = frozenset(
    {
        "api_key",
        "openai_api_key",
        "authorization",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
)

_context: contextvars.ContextVar[RunLogContext | None] = contextvars.ContextVar(
    "auto_research_log_context", default=None
)
_settings: contextvars.ContextVar[LoggingSettings | None] = contextvars.ContextVar(
    "auto_research_log_settings", default=None
)
_configured_run_id: str | None = None
_pipeline_file_handler: RotatingFileHandler | None = None


@dataclass(frozen=True)
class RunLogContext:
    run_id: str
    round_index: int | None = None
    module: str = ""
    attempt: int | None = None
    report_id: str = ""
    trace_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"run_id": self.run_id}
        if self.round_index is not None:
            payload["round_index"] = self.round_index
        if self.module:
            payload["module"] = self.module
        if self.attempt is not None:
            payload["attempt"] = self.attempt
        if self.report_id:
            payload["report_id"] = self.report_id
        if self.trace_id:
            payload["trace_id"] = self.trace_id
        return payload


@dataclass(frozen=True)
class LoggingSettings:
    enabled: bool = True
    level: str = "INFO"
    content_mode: str = "redacted"
    max_field_chars: int = DEFAULT_MAX_FIELD_CHARS
    backup_count: int = DEFAULT_BACKUP_COUNT
    max_bytes: int = DEFAULT_MAX_BYTES
    console: bool = False


_OPENJIUWEN_STDLIB_LOGGER_NAMES = (
    "common",
    "interface",
    "prompt_builder",
    "performance",
    "llm",
    "tool",
    "agent",
    "workflow",
    "session",
    "runner",
    "sys_operation",
    "graph",
    "operator",
    "mcp",
    "team",
    "server",
    "memory",
    "retrieval",
    "context_engine",
    "prompt",
    "store",
    "multi_agent",
    "controller",
)


class ContextFilter(logging.Filter):
    """Attach run correlation fields so formatters never KeyError."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = current_context()
        record.run_id = ctx.run_id if ctx is not None else ""
        record.round_index = ctx.round_index if ctx is not None else ""
        record.module = ctx.module if ctx is not None else ""
        record.attempt = ctx.attempt if ctx is not None else ""
        record.report_id = ctx.report_id if ctx is not None else ""
        record.trace_id = ctx.trace_id if ctx is not None else ""
        return True


_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "[run=%(run_id)s module=%(module)s round=%(round_index)s attempt=%(attempt)s]: "
    "%(message)s"
)


def current_context() -> RunLogContext | None:
    return _context.get()


def current_settings() -> LoggingSettings:
    return _settings.get() or LoggingSettings()


def make_trace_id(
    run_id: str,
    *,
    module: str = "",
    round_index: int | None = None,
    attempt: int | None = None,
    report_id: str = "",
) -> str:
    if report_id:
        return f"{run_id}:{report_id}"
    parts = [run_id]
    if module:
        parts.append(module)
    if round_index is not None:
        parts.append(str(round_index))
    if attempt is not None:
        parts.append(str(attempt))
    return ":".join(parts)


def logging_settings_from_config(config: dict[str, Any] | None) -> LoggingSettings:
    raw = dict((config or {}).get("logging") or {})
    level = str(raw.get("level") or "INFO").upper()
    mode = str(raw.get("content_mode") or "redacted").strip().lower()
    if mode not in {"redacted", "raw", "metadata"}:
        mode = "redacted"
    raw_console = raw.get("console", False)
    if isinstance(raw_console, str):
        console = raw_console.strip().lower() in {"1", "true", "yes", "on"}
    else:
        console = bool(raw_console)
    return LoggingSettings(
        enabled=bool(raw.get("enabled", True)),
        level=level,
        content_mode=mode,
        max_field_chars=int(raw.get("max_field_chars", DEFAULT_MAX_FIELD_CHARS)),
        backup_count=int(raw.get("backup_count", DEFAULT_BACKUP_COUNT)),
        max_bytes=int(raw.get("max_bytes", DEFAULT_MAX_BYTES)),
        console=console,
    )


def active_artifact_dir(run_id: str, fallback: Path) -> Path:
    """Attempt-scoped folder when a manager subagent is running; else ``fallback``."""
    ctx = current_context()
    if (
        ctx is not None
        and ctx.run_id == run_id
        and ctx.module
        and ctx.round_index is not None
        and ctx.attempt is not None
    ):
        return ensure_module_attempt_dir(run_id, ctx.module, ctx.round_index, ctx.attempt)
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


@contextmanager
def log_context(
    *,
    run_id: str | None = None,
    round_index: int | None = None,
    module: str | None = None,
    attempt: int | None = None,
    report_id: str | None = None,
    trace_id: str | None = None,
) -> Iterator[RunLogContext | None]:
    current = current_context()
    resolved_run = run_id if run_id is not None else (current.run_id if current else "")
    if not resolved_run:
        yield current
        return
    merged = RunLogContext(
        run_id=resolved_run,
        round_index=round_index if round_index is not None else (current.round_index if current else None),
        module=module if module is not None else (current.module if current else ""),
        attempt=attempt if attempt is not None else (current.attempt if current else None),
        report_id=report_id if report_id is not None else (current.report_id if current else ""),
        trace_id=trace_id if trace_id is not None else "",
    )
    if not merged.trace_id:
        merged = replace(
            merged,
            trace_id=make_trace_id(
                merged.run_id,
                module=merged.module,
                round_index=merged.round_index,
                attempt=merged.attempt,
                report_id=merged.report_id,
            ),
        )
    token = _context.set(merged)
    previous_session = _get_openjiuwen_session_id()
    try:
        _set_openjiuwen_session_id(merged.trace_id)
        yield merged
    finally:
        _context.reset(token)
        if previous_session is not None:
            _set_openjiuwen_session_id(previous_session)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    console = current_settings().console
    if not console:
        _remove_console_handlers(logger)
    if logger.handlers:
        return logger
    if _pipeline_file_handler is not None and not console:
        logger.setLevel(getattr(logging, current_settings().level, logging.INFO))
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(ContextFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def configure_run_logging(run_id: str, config: dict[str, Any] | None = None) -> Path:
    """Configure host + OpenJiuwen disk logging for one manager/pipeline run."""
    global _configured_run_id, _pipeline_file_handler
    settings = logging_settings_from_config(config)
    _settings.set(settings)
    ensure_manager_dir(run_id)
    path = pipeline_log_path(run_id)
    if not settings.enabled:
        _configured_run_id = run_id
        return path

    if _configured_run_id != run_id or _pipeline_file_handler is None:
        if _pipeline_file_handler is not None:
            logging.getLogger("auto_research").removeHandler(_pipeline_file_handler)
            _pipeline_file_handler.close()
        handler = RotatingFileHandler(
            path,
            maxBytes=settings.max_bytes,
            backupCount=settings.backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(ContextFilter())
        pipeline_logger = logging.getLogger("auto_research")
        pipeline_logger.setLevel(getattr(logging, settings.level, logging.INFO))
        pipeline_logger.propagate = False
        pipeline_logger.addHandler(handler)
        _pipeline_file_handler = handler
        _configured_run_id = run_id
        if not settings.console:
            _silence_logger_tree("auto_research")

    _configure_openjiuwen_logging(run_id, settings)
    _set_openjiuwen_session_id(run_id)
    get_logger("auto_research").info("pipeline logging configured for run_id=%s", run_id)
    return path


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def bound_text(value: str, limit: int | None = None) -> dict[str, Any]:
    text = value if isinstance(value, str) else str(value)
    cap = current_settings().max_field_chars if limit is None else limit
    payload: dict[str, Any] = {
        "chars": len(text),
        "digest": digest_text(text),
    }
    if cap <= 0 or len(text) <= cap:
        payload["text"] = text
        payload["truncated"] = False
        return payload
    payload["text"] = text[:cap]
    payload["truncated"] = True
    return payload


def sanitize_for_trace(value: Any, *, mode: str | None = None) -> Any:
    """Redact secrets / encrypted blobs and bound large strings for disk traces."""
    settings = current_settings()
    resolved_mode = (mode or settings.content_mode).strip().lower()
    limit = settings.max_field_chars
    secrets = _secret_values()
    return _sanitize(value, mode=resolved_mode, limit=limit, secrets=secrets, depth=0)


def _secret_values() -> tuple[str, ...]:
    names = ("API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")
    values = []
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw and len(raw) >= 8:
            values.append(raw)
    return tuple(values)


def _redact_secrets(text: str, secrets: tuple[str, ...]) -> str:
    redacted = text
    for secret in secrets:
        redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _sanitize(
    value: Any,
    *,
    mode: str,
    limit: int,
    secrets: tuple[str, ...],
    depth: int,
) -> Any:
    if depth > 12:
        return "[max-depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return _sanitize(
            value.decode("utf-8", errors="replace"),
            mode=mode,
            limit=limit,
            secrets=secrets,
            depth=depth + 1,
        )
    if isinstance(value, str):
        text = _redact_secrets(value, secrets)
        if mode == "metadata":
            return {"chars": len(text), "digest": digest_text(text)}
        if mode == "raw":
            safety = max(limit, 32_000)
            return text if len(text) <= safety else text[:safety] + "…[truncated]"
        if len(text) <= limit:
            return text
        return {
            "text": text[:limit],
            "chars": len(text),
            "digest": digest_text(text),
            "truncated": True,
        }
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in _DROP_KEYS or _SENSITIVE_KEY_RE.search(lowered):
                out[key_text] = "[redacted]"
                continue
            if lowered in {"reasoning_details", "reasoning"}:
                out[key_text] = _sanitize_reasoning(item, mode=mode, limit=limit, secrets=secrets, depth=depth + 1)
                continue
            if mode == "metadata" and lowered in {
                "messages",
                "content",
                "arguments",
                "tool_args",
                "tool_result",
                "response",
                "observation",
            }:
                out[key_text] = _metadata_stub(item)
                continue
            out[key_text] = _sanitize(item, mode=mode, limit=limit, secrets=secrets, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [
            _sanitize(item, mode=mode, limit=limit, secrets=secrets, depth=depth + 1)
            for item in value[:50]
        ]
    dumped = _safe_repr(value)
    return _sanitize(dumped, mode=mode, limit=limit, secrets=secrets, depth=depth + 1)


def _metadata_stub(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, dict):
        return {"type": "dict", "keys": sorted(str(key) for key in list(value)[:20])}
    text = value if isinstance(value, str) else _safe_repr(value)
    return {"chars": len(text), "digest": digest_text(text)}


def _sanitize_reasoning(
    value: Any, *, mode: str, limit: int, secrets: tuple[str, ...], depth: int
) -> Any:
    if isinstance(value, list):
        kept = []
        for item in value:
            if isinstance(item, dict) and str(item.get("type") or "").lower() in _ENCRYPTED_TYPE_MARKERS:
                kept.append({"type": item.get("type"), "redacted": True})
                continue
            kept.append(_sanitize(item, mode=mode, limit=limit, secrets=secrets, depth=depth + 1))
        return kept
    if isinstance(value, dict) and str(value.get("type") or "").lower() in _ENCRYPTED_TYPE_MARKERS:
        return {"type": value.get("type"), "redacted": True}
    return _sanitize(value, mode=mode, limit=limit, secrets=secrets, depth=depth + 1)


def _safe_repr(value: Any) -> str:
    for attr in ("model_dump", "dict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                dumped = method()
                if isinstance(dumped, dict):
                    return str(dumped)
            except Exception:  # noqa: BLE001
                pass
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return f"<{type(value).__name__}>"


def _is_console_handler(handler: logging.Handler) -> bool:
    if isinstance(handler, logging.FileHandler):
        return False
    stream = getattr(handler, "stream", None)
    return stream in {
        sys.stdout,
        sys.stderr,
        getattr(sys, "__stdout__", None),
        getattr(sys, "__stderr__", None),
    }


def _remove_console_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if _is_console_handler(handler):
            logger.removeHandler(handler)


def _silence_console_loggers(names: Iterable[str]) -> None:
    for name in names:
        logger = logging.getLogger(name)
        logger.propagate = False
        _remove_console_handlers(logger)


def _silence_logger_tree(prefix: str) -> None:
    root = logging.getLogger(prefix)
    root.propagate = False
    _remove_console_handlers(root)
    for name in list(logging.Logger.manager.loggerDict):
        if name == prefix or not name.startswith(prefix + "."):
            continue
        _remove_console_handlers(logging.getLogger(name))


def _configure_openjiuwen_logging(run_id: str, settings: LoggingSettings) -> None:
    try:
        from openjiuwen.core.common.logging.log_config import (
            configure_log_config,
            get_log_config_snapshot,
        )
        from openjiuwen.core.common.logging.manager import LogManager
    except Exception:  # noqa: BLE001 — host logging still works without SDK
        return
    try:
        snapshot = get_log_config_snapshot()
        log_dir = openjiuwen_log_dir(run_id)
        log_dir.mkdir(parents=True, exist_ok=True)
        outputs = ["file", "console"] if settings.console else ["file"]
        snapshot["log_path"] = str(log_dir)
        snapshot["output"] = list(outputs)
        snapshot["interface_output"] = list(outputs)
        snapshot["performance_output"] = list(outputs)
        snapshot["backup_count"] = settings.backup_count
        snapshot["max_bytes"] = settings.max_bytes
        snapshot["level"] = settings.level
        snapshot["propagate"] = False
        loggers = dict(snapshot.get("loggers") or {})
        if settings.content_mode != "raw":
            loggers["llm"] = {"level": "WARNING"}
            loggers["tool"] = {"level": "WARNING"}
        snapshot["loggers"] = loggers
        configure_log_config(snapshot)
        for name in ("common", "interface", "prompt_builder", "performance", "llm", "tool"):
            LogManager.get_logger(name)
        if not settings.console:
            _silence_console_loggers(_OPENJIUWEN_STDLIB_LOGGER_NAMES)
    except Exception:  # noqa: BLE001 — path checker / backend quirks must not abort a run
        get_logger(__name__).exception("failed to configure OpenJiuwen disk logging")


def _set_openjiuwen_session_id(trace_id: str) -> None:
    try:
        from openjiuwen.core.common.logging import set_session_id

        set_session_id(trace_id or "default_trace_id")
    except Exception:  # noqa: BLE001, S110
        pass


def _get_openjiuwen_session_id() -> str | None:
    try:
        from openjiuwen.core.common.logging import get_session_id

        return get_session_id()
    except Exception:  # noqa: BLE001
        return None
