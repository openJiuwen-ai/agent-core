# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Loguru-backed logger adapter."""

import copy
import json
import logging
import sys
import traceback
from typing import (
    Any,
    ClassVar,
    Dict,
    List,
)

def _get_loguru():
    """Lazy-load loguru to avoid import failure when the package is absent."""
    from loguru import logger as _logger
    return _logger


from openjiuwen.core.common.logging.base_impl import (
    resolve_log_type_label,
    StructuredLoggerMixin,
)
from openjiuwen.core.common.logging.events import (
    BaseLogEvent,
    EventStatus,
    LogLevel,
    ModuleType,
)
from openjiuwen.core.common.logging.protocol import LoggerProtocol
from openjiuwen.core.common.logging.utils import get_session_id


class LoguruLogger(StructuredLoggerMixin, LoggerProtocol):
    """LoggerProtocol adapter using native loguru structured logging."""

    _DEFAULT_SINK_REMOVED: ClassVar[bool] = False
    _LOG_LEVEL_TO_NO: ClassVar[Dict[LogLevel, int]] = {
        LogLevel.DEBUG: logging.DEBUG,
        LogLevel.INFO: logging.INFO,
        LogLevel.WARNING: logging.WARNING,
        LogLevel.ERROR: logging.ERROR,
        LogLevel.CRITICAL: logging.CRITICAL,
    }
    _SERIALIZE_MODE_LOGURU: ClassVar[str] = "loguru"
    _SERIALIZE_MODE_EVENT: ClassVar[str] = "event"
    _MODULE_TYPES_BY_VALUE: ClassVar[Dict[str, ModuleType]] = {member.value: member for member in ModuleType}

    def __init__(self, log_type: str, config: Dict[str, Any]) -> None:
        self.log_type = log_type
        self.config = config.copy()
        self._log_type_label = resolve_log_type_label(log_type)
        self._sink_ids: List[int] = []
        self._handler_sink_ids: Dict[logging.Handler, int] = {}
        self._filters: List[Any] = []
        self._effective_level_no = self._to_level_no(self.config.get("effective_level", self.config.get("level")))

        self._ensure_default_sink_removed()
        self._logger = self._build_logger()
        self._setup_logger()

    @classmethod
    def _ensure_default_sink_removed(cls) -> None:
        if cls._DEFAULT_SINK_REMOVED:
            return
        _get_loguru().remove()
        cls._DEFAULT_SINK_REMOVED = True

    def _patch_record(self, record: Dict[str, Any]) -> None:
        extra = record["extra"]
        extra.setdefault("log_type", self._log_type_label)
        extra.setdefault("trace_id", get_session_id())
        extra.setdefault("event", None)
        extra.setdefault("event_text", "")
        extra.setdefault("rendered_message", record["message"])
        extra["event_serialized"] = self._serialize_event_payload(record)

        file_path = record["file"].path
        parts = file_path.replace("\\", "/").rsplit("/", 2)
        extra["short_path"] = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]

    def _build_logger(self):
        return _get_loguru().patch(self._patch_record).bind(log_type=self._log_type_label)

    def _setup_logger(self) -> None:
        self.close()
        self._logger = self._build_logger()
        self._effective_level_no = self._to_level_no(self.config.get("effective_level", self.config.get("level")))

        for sink_config in self.config.get("sinks", []):
            sink_options = self._build_sink_options(sink_config)
            sink_id = self._logger.add(**sink_options)
            self._sink_ids.append(sink_id)

    def _build_sink_options(self, sink_config: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve_sink_target(sink_config.get("target"))
        sink_level = self._get_loguru_level(sink_config.get("level", logging.INFO))
        serialize_mode = self._get_serialize_mode(sink_config)
        serialize = bool(sink_config.get("serialize", False))
        is_event_mode = serialize and serialize_mode == self._SERIALIZE_MODE_EVENT

        sink_options: Dict[str, Any] = {
            "sink": target,
            "level": sink_level,
            "format": "{extra[event_serialized]}" if is_event_mode else self._get_sink_format(sink_config),
            "filter": self._record_filter,
            "serialize": serialize and serialize_mode == self._SERIALIZE_MODE_LOGURU,
            "colorize": False if is_event_mode else sink_config.get("colorize"),
            "enqueue": bool(sink_config.get("enqueue", False)),
            "catch": bool(sink_config.get("catch", False)),
            "backtrace": bool(sink_config.get("backtrace", False)),
            "diagnose": bool(sink_config.get("diagnose", False)),
        }

        for key in ("rotation", "retention", "compression", "encoding"):
            if sink_config.get(key) is not None:
                sink_options[key] = sink_config[key]

        return sink_options

    @staticmethod
    def _resolve_sink_target(target: Any):
        if target == "stdout":
            return sys.stdout
        if target == "stderr":
            return sys.stderr
        return target

    def _get_serialize_mode(self, sink_config: Dict[str, Any]) -> str:
        serialize_mode = sink_config.get("serialize_mode", self._SERIALIZE_MODE_LOGURU)
        if isinstance(serialize_mode, str) and serialize_mode.strip():
            return serialize_mode.strip().lower()
        return self._SERIALIZE_MODE_LOGURU

    def _get_sink_format(self, sink_config: Dict[str, Any]) -> str:
        configured_format = sink_config.get("format")
        if isinstance(configured_format, str) and configured_format.strip():
            return configured_format

        if sink_config.get("serialize", False):
            return "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}"

        return (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {extra[log_type]} | {extra[trace_id]} | "
            "{level} | {extra[rendered_message]}"
        )

    @classmethod
    def _to_level_no(cls, level: Any) -> int:
        if isinstance(level, LogLevel):
            return cls._LOG_LEVEL_TO_NO[level]
        if isinstance(level, int):
            return level
        if isinstance(level, str):
            resolved = logging.getLevelName(level.upper())
            if isinstance(resolved, int):
                return resolved
        return logging.WARNING

    @staticmethod
    def _get_loguru_level(level: Any) -> str:
        if isinstance(level, str):
            return level.upper()
        if isinstance(level, int):
            resolved = logging.getLevelName(level)
            return resolved if isinstance(resolved, str) else "WARNING"
        return "WARNING"

    def _record_filter(self, record: Dict[str, Any]) -> bool:
        if record["extra"].get("log_type") != self._log_type_label:
            return False

        if not self._filters:
            return True

        log_record = logging.makeLogRecord(
            {
                "name": self.log_type,
                "msg": record["message"],
                "args": (),
                "levelno": record["level"].no,
                "levelname": record["level"].name,
                "pathname": record["file"].path,
                "filename": record["file"].name,
                "module": record["module"],
                "funcName": record["function"],
                "lineno": record["line"],
            }
        )
        log_record.log_type = record["extra"].get("log_type")
        log_record.trace_id = record["extra"].get("trace_id")

        for log_filter in self._filters:
            if hasattr(log_filter, "filter"):
                if not log_filter.filter(log_record):
                    return False
                continue

            if callable(log_filter) and not log_filter(log_record):
                return False

        return True


    @staticmethod
    def _render_event_text(event_dict: Dict[str, Any]) -> str:
        fields: List[str] = []
        for key, value in event_dict.items():
            if key == "message":
                continue
            if value is None:
                continue
            if isinstance(value, str) and value == "":
                continue
            if isinstance(value, (dict, list, tuple, set)) and not value:
                continue
            fields.append(f"{key}={value}")

        return " | ".join(fields)

    @staticmethod
    def _build_rendered_message(message: str, event_text: str) -> str:
        if event_text:
            return f"{message} | {event_text}"
        return message

    @staticmethod
    def _resolve_record_log_level(record: Dict[str, Any]) -> LogLevel:
        level_name = str(record["level"].name).upper()
        return LogLevel.__members__.get(level_name, LogLevel.INFO)

    @classmethod
    def _resolve_module_type(cls, log_type: Any) -> ModuleType:
        normalized = str(log_type or "").strip().lower()
        if normalized == "context_engine":
            normalized = ModuleType.CONTEXT.value
        return cls._MODULE_TYPES_BY_VALUE.get(normalized, ModuleType.SYSTEM)

    @staticmethod
    def _coerce_metadata(metadata: Any) -> Dict[str, Any]:
        if isinstance(metadata, dict):
            return copy.deepcopy(metadata)
        return {}

    def _build_base_event_payload(self, record: Dict[str, Any]) -> Dict[str, Any]:
        log_type = record["extra"].get("log_type", self._log_type_label)
        return BaseLogEvent(
            event_type="plain_log",
            log_level=self._resolve_record_log_level(record),
            timestamp=record["time"],
            module_type=self._resolve_module_type(log_type),
            module_id=log_type,
            module_name=log_type,
            trace_id=record["extra"].get("trace_id"),
            message=record["message"],
        ).to_dict()

    @staticmethod
    def _build_log_context(record: Dict[str, Any]) -> Dict[str, Any]:
        file_record = record["file"]
        process_record = record["process"]
        thread_record = record["thread"]

        return {
            "log_type": record["extra"].get("log_type"),
            "source": {
                "file_name": file_record.name,
                "file_path": file_record.path,
                "module": record["module"],
                "function": record["function"],
                "line": record["line"],
            },
            "process": {
                "id": process_record.id,
                "name": process_record.name,
            },
            "thread": {
                "id": thread_record.id,
                "name": thread_record.name,
            },
        }

    def _merge_runtime_metadata(self, payload: Dict[str, Any], record: Dict[str, Any]) -> None:
        metadata = self._coerce_metadata(payload.get("metadata"))
        metadata["_log_context"] = self._build_log_context(record)
        payload["metadata"] = metadata

    @staticmethod
    def _format_exception_traceback(exc_type: Any, exc_value: Any, exc_traceback: Any) -> str:
        if exc_traceback is None:
            return ""
        try:
            return "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        except TypeError:
            return str(exc_traceback)

    @classmethod
    def _extract_exception_fields(cls, record_exception: Any) -> Dict[str, str]:
        if record_exception is None:
            return {}

        exc_type = getattr(record_exception, "type", None)
        exc_value = getattr(record_exception, "value", None)
        exc_traceback = getattr(record_exception, "traceback", None)

        if exc_type is None and isinstance(record_exception, tuple) and len(record_exception) >= 3:
            exc_type, exc_value, exc_traceback = record_exception[:3]

        if exc_type is None and isinstance(record_exception, dict):
            exc_type = record_exception.get("type")
            exc_value = record_exception.get("value")
            exc_traceback = record_exception.get("traceback")

        if isinstance(record_exception, BaseException):
            exc_type = type(record_exception)
            exc_value = record_exception
            exc_traceback = record_exception.__traceback__

        if exc_value is None and exc_type is None and exc_traceback is None:
            return {}

        exception_text = ""
        if exc_value is not None:
            exception_text = str(exc_value)
        elif exc_type is not None:
            exception_text = getattr(exc_type, "__name__", str(exc_type))

        stacktrace = cls._format_exception_traceback(exc_type, exc_value, exc_traceback)
        if not stacktrace and exc_traceback is not None:
            stacktrace = str(exc_traceback)

        result: Dict[str, str] = {}
        if exception_text:
            result["exception"] = exception_text
            result["error_message"] = exception_text
        if stacktrace:
            result["stacktrace"] = stacktrace
        return result

    @classmethod
    def _record_is_failure(cls, record: Dict[str, Any]) -> bool:
        return record["level"].no >= logging.ERROR or record.get("exception") is not None

    def _enrich_exception_payload(self, payload: Dict[str, Any], record: Dict[str, Any], had_status: bool) -> None:
        for key, value in self._extract_exception_fields(record.get("exception")).items():
            if key not in payload:
                payload[key] = value

        if not had_status and self._record_is_failure(record):
            payload["status"] = EventStatus.FAILURE.value

    def _build_event_payload(self, record: Dict[str, Any]) -> Dict[str, Any]:
        extra = record["extra"]
        raw_event = extra.get("event")
        payload = copy.deepcopy(raw_event) if isinstance(raw_event, dict) else {}
        had_status = "status" in payload
        base_payload = self._build_base_event_payload(record)

        for key, value in base_payload.items():
            if key not in payload:
                payload[key] = value

        self._merge_runtime_metadata(payload, record)
        self._enrich_exception_payload(payload, record, had_status=had_status)
        return payload

    def _serialize_event_payload(self, record: Dict[str, Any]) -> str:
        return json.dumps(self._build_event_payload(record), ensure_ascii=False, default=str)

    def _should_emit(self, log_level: LogLevel) -> bool:
        return self._LOG_LEVEL_TO_NO[log_level] >= self._effective_level_no

    def _emit(self, level: str, log_level: LogLevel, msg: str, args: tuple[Any, ...], **kwargs: Any) -> None:
        if not self._should_emit(log_level):
            return

        event_type = kwargs.pop("event_type", None)
        event = kwargs.pop("event", None)
        stacklevel = kwargs.pop("stacklevel", 2)

        formatted_msg = self._auto_format_message(msg, args)
        sanitized_msg = self._sanitize_message(formatted_msg)
        event_dict = self._build_structured_event_dict(log_level, sanitized_msg, event_type, event, **kwargs)
        event_text = self._render_event_text(event_dict) if event_dict else ""
        rendered_message = self._build_rendered_message(sanitized_msg, event_text)

        bound_logger = self._logger.bind(
            event=event_dict,
            event_text=event_text,
            rendered_message=rendered_message,
        )
        bound_logger.opt(depth=stacklevel).log(level, sanitized_msg)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._emit("DEBUG", LogLevel.DEBUG, msg, args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._emit("INFO", LogLevel.INFO, msg, args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._emit("WARNING", LogLevel.WARNING, msg, args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._emit("ERROR", LogLevel.ERROR, msg, args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._emit("CRITICAL", LogLevel.CRITICAL, msg, args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if not self._should_emit(LogLevel.ERROR):
            return

        event_type = kwargs.pop("event_type", None)
        event = kwargs.pop("event", None)
        stacklevel = kwargs.pop("stacklevel", 2)

        if event is None and "stacktrace" not in kwargs:
            current_traceback = "".join(traceback.format_exc())
            if current_traceback and current_traceback.strip() != "NoneType: None":
                kwargs["stacktrace"] = current_traceback

        formatted_msg = self._auto_format_message(msg, args)
        sanitized_msg = self._sanitize_message(formatted_msg)
        event_dict = self._build_structured_event_dict(LogLevel.ERROR, sanitized_msg, event_type, event, **kwargs)
        event_text = self._render_event_text(event_dict) if event_dict else ""
        rendered_message = self._build_rendered_message(sanitized_msg, event_text)

        bound_logger = self._logger.bind(
            event=event_dict,
            event_text=event_text,
            rendered_message=rendered_message,
        )
        bound_logger.opt(exception=True, depth=stacklevel).error(sanitized_msg)

    def log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        log_level_map = {
            logging.DEBUG: LogLevel.DEBUG,
            logging.INFO: LogLevel.INFO,
            logging.WARNING: LogLevel.WARNING,
            logging.ERROR: LogLevel.ERROR,
            logging.CRITICAL: LogLevel.CRITICAL,
        }
        self._emit(self._get_loguru_level(level), log_level_map.get(level, LogLevel.INFO), msg, args, **kwargs)

    def set_level(self, level: int) -> None:
        self._effective_level_no = self._to_level_no(level)
        self.config["effective_level"] = self._effective_level_no
        self.config["level"] = self._effective_level_no

    def add_handler(self, handler: logging.Handler) -> None:
        sink_id = self._logger.add(
            handler,
            level="DEBUG",
            filter=self._record_filter,
            serialize=False,
            catch=False,
        )
        self._handler_sink_ids[handler] = sink_id

    def remove_handler(self, handler: logging.Handler) -> None:
        sink_id = self._handler_sink_ids.pop(handler, None)
        if sink_id is None:
            return
        _get_loguru().remove(sink_id)

    def add_filter(self, log_filter) -> None:
        self._filters.append(log_filter)

    def remove_filter(self, log_filter) -> None:
        if log_filter in self._filters:
            self._filters.remove(log_filter)

    def get_config(self) -> Dict[str, Any]:
        return self.config.copy()

    def reconfigure(self, config: Dict[str, Any]) -> None:
        self.config = config.copy()
        self._setup_logger()

    def logger(self):
        return self._logger

    def close(self) -> None:
        handler_sink_ids = list(self._handler_sink_ids.values())
        self._handler_sink_ids.clear()

        for sink_id in self._sink_ids + handler_sink_ids:
            try:
                _get_loguru().remove(sink_id)
            except ValueError:
                continue

        self._sink_ids = []
