# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded diagnostic logs scoped to one agent invocation."""

import json
import os
import re
import sys
import threading
import traceback
from asyncio import CancelledError
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from uuid import uuid4

from openjiuwen.core.common.logging.utils import (
    get_member_id,
    get_session_id,
    normalize_and_validate_log_path,
    set_member_id,
    set_session_id,
)

_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_RECORD_BYTES = 2048
_MAX_JSON_CHARS = 64 * 1024
_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|token|secret|password|authorization)\b"
    r"[\"']?\s*[:=]\s*)(?:[\"'][^\"']*[\"']|[^\s,;}]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;\"']+")
_KEY_RE = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b")
_PAYLOAD_RE = re.compile(
    r"(?i)\b(?:prompt|messages?|response_content|input_content|output_content|content|"
    r"arguments|args|tool_calls|input_data|output_data|request_body|response_body)"
    r"[\"']?\s*[:=]"
)
_STRUCTURE_RE = re.compile(r"\{\s*[\"']|\[\s*[\"'{]")
_SENSITIVE_TEMPLATE_RE = re.compile(
    r"(?i)\b(?:prompt|messages?|query|response|result|request|content|arguments|args|"
    r"api[_-]?key|token|secret|password|authorization|credentials?)\b"
)
_BRACE_ARG_RE = re.compile(r"\{(\d*)\}")
_PERCENT_ARG_RE = re.compile(r"%[sdrf]")
_ACTIVE_INVOCATION: ContextVar["InvocationLogScope | None"] = ContextVar(
    "invocation_log_scope", default=None,
)


def _clip(value: str, limit: int) -> str:
    return value[:limit].encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")


def _summary(value: Any, limit: int = 768) -> str:
    """Keep diagnostic text while discarding payloads and credentials."""
    if isinstance(value, Enum):
        value = value.value
    if value is None:
        return ""
    if not isinstance(value, (str, int, float, bool)):
        return "[payload omitted]"
    text = str(value)[:8192]
    text = _BEARER_RE.sub("Bearer <REDACTED>", text)
    text = _SECRET_RE.sub(r"\1<REDACTED>", text)
    text = _KEY_RE.sub("<REDACTED>", text)
    # Free-form logs can contain embedded tool arguments or Python repr payloads.
    payload = _PAYLOAD_RE.search(text)
    structure = _STRUCTURE_RE.search(text)
    boundaries = [match.start() for match in (payload, structure) if match is not None]
    if boundaries:
        text = text[:min(boundaries)] + "[payload omitted]"
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return _clip(text, limit)


def _label(value: Any) -> str:
    return _summary(value, 96)


def _format_summary(message: Any, args: tuple[Any, ...]) -> str:
    """Substitute simple placeholders without expanding arbitrary format widths."""
    template = _summary(message)
    if _SENSITIVE_TEMPLATE_RE.search(template):
        return template
    safe_args = tuple(_summary(arg, 256) for arg in args[:16])
    next_index = 0

    def replace(match: re.Match) -> str:
        nonlocal next_index
        index = next_index
        if match.re is _BRACE_ARG_RE and match.group(1):
            index = int(match.group(1))
        next_index += 1
        return safe_args[index] if index < len(safe_args) else match.group(0)

    if _BRACE_ARG_RE.search(template):
        return _BRACE_ARG_RE.sub(replace, template)
    return _PERCENT_ARG_RE.sub(replace, template)


def _message_fields(message: Any) -> dict[str, Any]:
    """Extract only top-level diagnostic fields from JSON log messages."""
    if isinstance(message, dict):
        return message
    if isinstance(message, str) and message.lstrip().startswith(("{", "[")):
        if len(message) > _MAX_JSON_CHARS:
            return {"message": "[oversized structured payload omitted]"}
        try:
            parsed = json.loads(message)
        except (ValueError, RecursionError):
            return {"message": _summary(message)}
        if isinstance(parsed, dict):
            return parsed
        return {"message": "[structured payload omitted]"}
    return {"message": message}


def _exception_summary(exc_info: Any) -> str:
    if exc_info is True:
        exc_info = sys.exc_info()
    if isinstance(exc_info, BaseException):
        exception = exc_info
        trace = exception.__traceback__
    elif isinstance(exc_info, tuple) and len(exc_info) == 3:
        _, exception, trace = exc_info
    else:
        return ""
    if not isinstance(exception, BaseException):
        return ""
    # Traceback frame locations are useful evidence; source and locals are excluded.
    frames = traceback.extract_tb(trace, limit=4) if trace is not None else []
    locations = "; ".join(f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames)
    try:
        detail = _summary(str(exception), 256)
    except Exception:
        detail = "[exception detail unavailable]"
    return _clip(f"{type(exception).__name__}: {detail}; {locations}", 512)


class InvocationLogScope:
    """One invocation's file and capture status, yielded by invocation_log_scope."""

    def __init__(self, directory: str | Path, agent_id: str, session_id: str, role: str) -> None:
        root = Path(normalize_and_validate_log_path(directory))
        root.mkdir(mode=0o750, parents=True, exist_ok=True)
        root = Path(normalize_and_validate_log_path(root))
        self.invocation_id = uuid4().hex
        self.path = Path(normalize_and_validate_log_path(root / f"invocation-{self.invocation_id}.jsonl"))
        if self.path.parent != root:
            raise ValueError("Invocation log must remain inside its configured directory")
        parent = _ACTIVE_INVOCATION.get()
        self._parent_scope = parent
        self._previous_trace = get_session_id()
        self._previous_member = get_member_id()
        self._identity = {
            "agent_id": _label(agent_id),
            "session_id": _label(session_id),
            "invocation_id": self.invocation_id,
            "parent_invocation_id": parent.invocation_id if parent is not None else "",
            "role": _label(role),
        }
        self.truncated = False
        self.write_error = ""
        self.bytes_written = 0
        self.dropped_records = 0
        self._lock = threading.Lock()
        self._closed = False
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        try:
            self._stream: BinaryIO = os.fdopen(descriptor, "wb")
        except OSError:
            os.close(descriptor)
            raise

    @contextmanager
    def suspend(self) -> Iterator[None]:
        """Temporarily restore the caller's log context around a stream yield.

        Only the currently active scope can suspend itself. Repeated suspension
        or suspension outside this scope leaves the current context unchanged.
        Nested invocation scopes restore their immediate parent's context.
        """
        if _ACTIVE_INVOCATION.get() is not self:
            yield
            return
        current_trace = get_session_id()
        current_member = get_member_id()
        token = _ACTIVE_INVOCATION.set(self._parent_scope)
        set_session_id(self._previous_trace)
        set_member_id(self._previous_member)
        try:
            yield
        finally:
            _ACTIVE_INVOCATION.reset(token)
            set_session_id(current_trace)
            set_member_id(current_member)

    def _write(self, level: str, log_type: str, fields: dict[str, Any], *, final: bool = False) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": _label(level),
            **self._identity,
            "trace_id": _label(get_session_id()),
            "member_id": _label(get_member_id()),
            "log_type": _label(log_type),
            "message": _summary(fields.get("message")),
            "exception": _summary(fields.get("exception"), 512),
            "event_type": _label(fields.get("event_type")),
            "status": _label(fields.get("status")),
        }
        with self._lock:
            if self._closed or self.write_error:
                return
            if final:
                record["truncated"] = self.truncated
                record["dropped_records"] = self.dropped_records
            encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            # JSON escaping can expand short strings; bound the encoded record too.
            while len(encoded) > _MAX_RECORD_BYTES:
                if not record["message"] and not record["exception"]:
                    for key in ("role", "trace_id", "member_id", "event_type", "status"):
                        record[key] = _clip(record[key], 32)
                for key in ("message", "exception"):
                    record[key] = _clip(record[key], len(record[key].encode("utf-8")) // 2)
                encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            ceiling = _MAX_FILE_BYTES if final else _MAX_FILE_BYTES - _MAX_RECORD_BYTES
            if self.bytes_written + len(encoded) > ceiling:
                self.truncated = True
                self.dropped_records += 1
                return
            try:
                self._stream.write(encoded)
                self._stream.flush()
                self.bytes_written += len(encoded)
                if final:
                    self._closed = True
            except OSError as exc:
                # Capture failure must never interrupt the agent or recurse into logging.
                self.write_error = type(exc).__name__

    def _close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self._stream.close()
            except OSError as exc:
                self.write_error = type(exc).__name__


@contextmanager
def invocation_log_scope(
    directory: str | Path,
    *,
    agent_id: str,
    session_id: str,
    role: str = "",
) -> Iterator[InvocationLogScope]:
    """Capture INFO and higher diagnostic summaries in a unique UTF-8 JSONL file.

    Child tasks inherit the scope; nested scopes write exclusively to their own
    file and retain parent_invocation_id. Context and handles are restored even
    when the invocation raises or is cancelled. Global logger configuration is
    unaffected. Callers can inspect truncated/write_error after exit.

    Args:
        directory: Destination directory, validated and created if missing.
        agent_id: Agent identity, also bound as the current member ID.
        session_id: Session identity, also bound as the current trace ID.
        role: Optional diagnostic role label.

    Yields:
        The unique file path, invocation ID, and capture status.
    """
    scope = InvocationLogScope(directory, agent_id, session_id, role)
    previous_trace = get_session_id()
    previous_member = get_member_id()
    token = _ACTIVE_INVOCATION.set(scope)
    set_session_id(session_id or previous_trace)
    set_member_id(agent_id or previous_member)
    status = "success"
    exception = ""
    try:
        scope._write("INFO", "invocation", {"event_type": "invocation_start", "status": "running"})
        yield scope
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, (CancelledError, GeneratorExit, KeyboardInterrupt)) else "failure"
        # asyncio cancellation is a BaseException, so cleanup cannot rely on Exception.
        exception = _exception_summary(exc)
        raise
    finally:
        try:
            scope._write(
                "INFO" if status == "success" else "ERROR",
                "invocation",
                {"event_type": "invocation_end", "status": status, "exception": exception},
                final=True,
            )
        finally:
            scope._close()
            _ACTIVE_INVOCATION.reset(token)
            set_session_id(previous_trace)
            set_member_id(previous_member)


def capture_invocation_log(
    log_type: str,
    level: Any,
    message: Any,
    args: tuple[Any, ...] = (),
    *,
    event: Any = None,
    event_type: Any = None,
    exc_info: Any = None,
) -> None:
    """Capture once at backend entry, before global level filtering or queues."""
    scope = _ACTIVE_INVOCATION.get()
    if scope is None:
        return
    try:
        _capture(scope, log_type, level, message, args, event, event_type, exc_info)
    except Exception as exc:
        # A malformed diagnostic event must not change normal logger behavior.
        scope.write_error = type(exc).__name__


def _capture(
    scope: InvocationLogScope,
    log_type: str,
    level: Any,
    message: Any,
    args: tuple[Any, ...],
    event: Any,
    event_type: Any,
    exc_info: Any,
) -> None:
    level_name = level.value if isinstance(level, Enum) else str(level)
    if level_name.upper() == "DEBUG":
        return
    fields = _message_fields(message)
    if args:
        # Never stringify container arguments, which commonly contain full requests.
        fields = {**fields, "message": _format_summary(fields.get("message"), args)}
    if event is not None:
        # Access the diagnostic whitelist directly; do not serialize payload fields.
        fields = {
            "message": fields.get("message") or getattr(event, "message", ""),
            "exception": getattr(event, "exception", "") or getattr(event, "error_message", ""),
            "event_type": getattr(event, "event_type", event_type),
            "status": getattr(event, "status", ""),
        }
    elif event_type is not None:
        fields = {**fields, "event_type": event_type}
    exception = _exception_summary(exc_info)
    if exception:
        fields = {**fields, "exception": exception}
    scope._write(level_name.upper(), log_type, fields)
