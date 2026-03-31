# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Shared helpers for logging backend implementations."""

import os
import re
from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
)

from openjiuwen.core.common.logging.events import (
    BaseLogEvent,
    create_log_event,
    LogEventType,
    LogLevel,
)
from openjiuwen.core.common.logging.utils import get_session_id


def resolve_log_type_label(log_type: str) -> str:
    """Map internal logger types to the label rendered in outputs."""
    return "perf" if log_type == "performance" else log_type


def format_log_filename(base_filename: str, pattern: str) -> str:
    """Apply the configured filename pattern to a log file path."""
    dir_path = os.path.dirname(base_filename)
    file_name = os.path.basename(base_filename)

    if "." in file_name:
        name_part, ext_part = file_name.rsplit(".", 1)
        ext = "." + ext_part
    else:
        name_part = file_name
        ext = ""

    now = datetime.now(tz=timezone.utc)
    replacements = {
        "name": name_part,
        "ext": ext,
        "pid": str(os.getpid()),
        "timestamp": now.strftime("%Y%m%d%H%M%S"),
        "date": now.strftime("%Y%m%d"),
        "time": now.strftime("%H%M%S"),
        "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
    }

    try:
        formatted_name = pattern.format(**replacements)
    except KeyError:
        return base_filename

    if "{ext}" not in pattern and ext and not formatted_name.endswith(ext):
        formatted_name = formatted_name + ext

    if dir_path:
        return os.path.join(dir_path, formatted_name)
    return formatted_name


_BRACE_PLACEHOLDER_RE = re.compile(
    r"\{(?:\d*|[a-zA-Z_]\w*)?(?:![rsa])?(?::[^}]*)?\}"
)


class StructuredLoggerMixin:
    """Common structured logging behavior shared by backend adapters."""

    _CONTROL_CHAR_MAP = {
        "\r": "\\r",
        "\n": "\\n",
        "\t": "\\t",
        "\b": "\\b",
        "\v": "\\v",
        "\f": "\\f",
        "\0": "\\0",
    }

    log_type: str
    config: Dict[str, Any]

    @staticmethod
    def _auto_format_message(msg: Any, args: Tuple[Any, ...]) -> str:
        """Auto-detect placeholder style and format the message.

        Supports both brace-style ({}, {0}) and percent-style (%s, %d).
        When both styles are present, brace-style takes priority.
        """
        if not args:
            return str(msg)

        msg_str = str(msg)

        if _BRACE_PLACEHOLDER_RE.search(msg_str):
            try:
                return msg_str.format(*args)
            except (IndexError, KeyError, ValueError):
                pass

        try:
            return msg_str % args
        except (TypeError, ValueError):
            return msg_str

    def _sanitize_message(self, msg: Any) -> str:
        """Escape control characters in plain log messages."""
        if not isinstance(msg, str):
            return str(msg)

        result: List[str] = []
        for char in msg:
            code = ord(char)
            if code < 32 or code == 127:
                result.append(self._CONTROL_CHAR_MAP.get(char, f"\\x{code:02x}"))
            else:
                result.append(char)
        return "".join(result)

    def _build_structured_event_dict(
        self,
        log_level: LogLevel,
        msg: str,
        event_type: Optional[LogEventType | str] = None,
        event: Optional[BaseLogEvent] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Build a structured event payload for backends that support it."""
        if event is not None:
            if event.log_level != log_level:
                event.log_level = log_level

            if msg and msg.strip():
                event.message = self._sanitize_message(msg)
            elif not event.message:
                event.message = ""

            return event.to_dict()

        if event_type is None:
            return None

        if "trace_id" not in kwargs:
            trace_id = get_session_id()
            if trace_id != "default_trace_id":
                kwargs["trace_id"] = trace_id

        if "module_id" not in kwargs:
            kwargs["module_id"] = self.log_type
        if "module_name" not in kwargs:
            kwargs["module_name"] = self.log_type
        if "message" not in kwargs:
            kwargs["message"] = self._sanitize_message(msg)

        event_obj = create_log_event(event_type, log_level=log_level, **kwargs)
        return event_obj.to_dict()
