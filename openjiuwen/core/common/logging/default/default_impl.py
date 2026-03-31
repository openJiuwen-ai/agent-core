# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Default Logging Implementation Module

Provides default logging implementations, including:
- DefaultLogger: Default logger implementation
- SafeRotatingFileHandler: Secure log file rotation handler
- ContextFilter: Context filter (adapted for async environments)
"""


import json
import logging
import os
import sys
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from typing import (
    Any,
    Dict,
    Optional,
)

from openjiuwen.core.common.logging.base_impl import (
    format_log_filename,
    resolve_log_type_label,
    StructuredLoggerMixin,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging.events import (
    LogLevel,
)
from openjiuwen.core.common.logging.protocol import LoggerProtocol
from openjiuwen.core.common.logging.utils import (
    get_log_max_bytes,
    get_session_id,
    normalize_and_validate_log_path,
)


class SafeRotatingFileHandler(RotatingFileHandler):
    """
    Secure log file rotation handler

    Extends standard RotatingFileHandler, providing:
    - Secure file permission settings
    - Support for log file name patterns
    - Automatic log directory creation
    - Backup file permission management
    """

    def __init__(
            self,
            filename: str,
            *args: Any,
            log_file_pattern: Optional[str] = None,
            backup_file_pattern: Optional[str] = None,
            **kwargs: Any,
    ) -> None:
        """
        Initialize secure log file rotation handler

        Args:
            filename: Log file path
            *args: Other positional arguments for RotatingFileHandler
            log_file_pattern: Log file name pattern (supports {name}, {ext}, {pid}, {timestamp}, etc.)
            backup_file_pattern: Backup file name pattern
            **kwargs: Other keyword arguments for RotatingFileHandler
        """
        if log_file_pattern:
            filename = self._format_filename(filename, log_file_pattern)

        # Ensure log file directory exists
        log_dir = os.path.dirname(filename)
        if log_dir:
            try:
                abs_log_dir = os.path.abspath(os.path.expanduser(log_dir))
                os.makedirs(abs_log_dir, mode=0o750, exist_ok=True)
            except OSError:
                pass

        super().__init__(filename, *args, **kwargs)
        self.backup_file_pattern = backup_file_pattern or "{baseFilename}.{index}"

        # Set log file permissions
        try:
            os.chmod(self.baseFilename, 0o640)
        except OSError as e:
            raise build_error(
                StatusCode.COMMON_LOG_EXECUTION_RUNTIME_ERROR,
                error_msg=f"failed to set file permissions: {e}"
            ) from e

    def _format_filename(self, base_filename: str, pattern: str) -> str:
        """Format filename according to pattern."""
        return format_log_filename(base_filename, pattern)

    def doRollover(self) -> None:
        """
        Perform log rotation

        Set backup file permissions during rotation to ensure security.
        """
        super().doRollover()

        # Set backup file permissions
        for i in range(self.backupCount, 0, -1):
            sfn = self.backup_file_pattern.format(baseFilename=self.baseFilename, index=i)
            if os.path.exists(sfn):
                try:
                    os.chmod(sfn, 0o440)  # Read-only permission
                except OSError as e:
                    raise build_error(
                        StatusCode.COMMON_LOG_EXECUTION_RUNTIME_ERROR,
                        error_msg=f"failed to set backup file permissions: {e}"
                    ) from e

        # Set new log file permissions
        try:
            os.chmod(self.baseFilename, 0o640)
        except OSError as e:
            raise build_error(
                StatusCode.COMMON_LOG_EXECUTION_RUNTIME_ERROR,
                error_msg=f"failed to set log file permissions: {e}"
            ) from e


class ContextFilter(logging.Filter):
    """
    Context filter

    Adds context information (trace_id and log_type) to log records.
    Adapted for async environments, uses contextvars to get context information.
    """

    def __init__(self, log_type: str) -> None:
        """
        Initialize context filter

        Args:
            log_type: Log type identifier
        """
        super().__init__()
        self.log_type = log_type

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Filter log record, add context information

        Args:
            record: Log record object

        Returns:
            Always returns True (does not filter any records)
        """
        # Get trace_id from context variable (adapted for async environments)
        record.trace_id = get_session_id()

        # Set log type, special handling for performance type
        record.log_type = resolve_log_type_label(self.log_type)

        return True


class DefaultStructuredLoggerMixin(StructuredLoggerMixin):
    """Structured logging helpers specific to the stdlib backend."""

    def _get_structured_output_format(self) -> str:
        output_format = str(self.config.get("structured_output_format", "json")).lower()
        return output_format if output_format in {"json", "text"} else "json"

    def _serialize_structured_event(self, event_dict: Dict[str, Any]) -> str:
        if self._get_structured_output_format() == "text":
            return self._format_structured_event_as_text(event_dict)

        try:
            return json.dumps(event_dict, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(event_dict)

    def _format_structured_event_as_text(self, event_dict: Dict[str, Any]) -> str:
        message = self._sanitize_message(event_dict.get("message", ""))
        key_values = []

        for key, value in event_dict.items():
            if key == "message":
                continue
            if value is None:
                continue
            if isinstance(value, str) and value == "":
                continue
            if isinstance(value, (dict, list, tuple, set)) and not value:
                continue

            rendered_value = str(dict(value)) if isinstance(value, Mapping) else str(value)
            key_values.append(f"{key}={rendered_value}")

        if message and key_values:
            return f"{message}; {'; '.join(key_values)}"
        if key_values:
            return "; ".join(key_values)
        return message

    def _process_log_message(
        self,
        log_level: LogLevel,
        msg: str,
        event_type=None,
        event=None,
        **kwargs: Any,
    ) -> str:
        event_dict = self._build_structured_event_dict(log_level, msg, event_type, event, **kwargs)
        if event_dict is None:
            return self._sanitize_message(msg)
        return self._serialize_structured_event(event_dict)


class DefaultLogger(DefaultStructuredLoggerMixin, LoggerProtocol):
    """
    Default logger implementation

    Implements LoggerProtocol interface, providing complete logging functionality:
    - Supports console and file output
    - Supports log rotation
    - Automatic control character cleanup
    - Automatic caller information detection
    - Automatic context information injection
    """

    def __init__(self, log_type: str, config: Dict[str, Any]) -> None:
        """
        Initialize default logger

        Args:
            log_type: Log type identifier
            config: Log configuration dictionary
        """
        self.log_type = log_type
        self.config = config.copy()  # Use copy to avoid external modification impact
        self._logger = logging.getLogger(log_type)
        self._setup_logger()

    def _setup_logger(self) -> None:
        """
        Setup logger

        Set log level, output targets, and formatter according to configuration.
        """
        # Parse log level
        level_config = self.config.get("level", "WARNING")
        if isinstance(level_config, str):
            level = getattr(logging, level_config.upper(), logging.WARNING)
        elif isinstance(level_config, int):
            level = level_config
        else:
            level = logging.WARNING

        self._logger.setLevel(level)
        self._logger.propagate = False

        # Get output targets and log file path
        output = self.config.get("output", ["console"])
        log_file = self.config.get("log_file", f"{self.log_type}.log")

        normalize_and_validate_log_path(log_file)

        # Clear existing handlers
        for handler in self._logger.handlers[:]:
            handler.close()
            self._logger.removeHandler(handler)

        # Add console handler
        if "console" in output:
            stream_handler = logging.StreamHandler(stream=sys.stdout)
            stream_handler.addFilter(ContextFilter(self.log_type))
            stream_handler.setFormatter(self._get_formatter())
            self._logger.addHandler(stream_handler)

        # Add file handler
        if "file" in output:
            try:
                abs_log_file = os.path.abspath(os.path.expanduser(log_file))
            except (OSError, TypeError):
                # If path normalization fails, use original path
                abs_log_file = log_file

            # Ensure log directory exists
            log_dir = os.path.dirname(abs_log_file)
            if log_dir:
                try:
                    os.makedirs(log_dir, mode=0o750, exist_ok=True)
                except OSError as e:
                    raise build_error(
                        StatusCode.COMMON_LOG_PATH_INIT_FAILED,
                        error_msg=f"the log_dir is `{log_dir}`, error detail: {e}"
                    ) from e

            # Get configuration parameters
            backup_count = self.config.get("backup_count", 20)
            max_bytes = get_log_max_bytes(self.config.get("max_bytes", 20 * 1024 * 1024))
            log_file_pattern = self.config.get("log_file_pattern", None)
            backup_file_pattern = self.config.get("backup_file_pattern", None)

            # Create file handler
            file_handler = SafeRotatingFileHandler(
                filename=abs_log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
                log_file_pattern=log_file_pattern,
                backup_file_pattern=backup_file_pattern,
            )
            file_handler.addFilter(ContextFilter(self.log_type))
            file_handler.setFormatter(self._get_formatter())
            self._logger.addHandler(file_handler)

    def _get_formatter(self) -> logging.Formatter:
        """
        Get formatter

        Returns:
            Configured formatter instance
        """
        log_format = (
                self.config.get("format")
                or "%(asctime)s.%(msecs)03d | %(log_type)s | %(trace_id)s | %(levelname)s | %(message)s"
        )
        return logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """
        Log DEBUG level message

        Args:
            msg: Log message (string)
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments (can include event_type, event for structured logging)
        """
        # Extract event_type, event, and stacklevel from kwargs
        event_type = kwargs.pop("event_type", None)
        event = kwargs.pop("event", None)
        stacklevel = kwargs.pop("stacklevel", 2)
        formatted_msg = self._auto_format_message(msg, args)
        processed_msg = self._process_log_message(LogLevel.DEBUG, formatted_msg, event_type, event, **kwargs)
        self._logger.debug(processed_msg, stacklevel=stacklevel)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """
        Log INFO level message

        Args:
            msg: Log message (string)
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments (can include event_type, event for structured logging)
        """
        # Extract event_type, event, and stacklevel from kwargs
        event_type = kwargs.pop("event_type", None)
        event = kwargs.pop("event", None)
        stacklevel = kwargs.pop("stacklevel", 2)
        formatted_msg = self._auto_format_message(msg, args)
        processed_msg = self._process_log_message(LogLevel.INFO, formatted_msg, event_type, event, **kwargs)
        self._logger.info(processed_msg, stacklevel=stacklevel)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """
        Log WARNING level message

        Args:
            msg: Log message (string)
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments (can include event_type, event for structured logging)
        """
        # Extract event_type, event, and stacklevel from kwargs
        event_type = kwargs.pop("event_type", None)
        event = kwargs.pop("event", None)
        stacklevel = kwargs.pop("stacklevel", 2)
        formatted_msg = self._auto_format_message(msg, args)
        processed_msg = self._process_log_message(LogLevel.WARNING, formatted_msg, event_type, event, **kwargs)
        self._logger.warning(processed_msg, stacklevel=stacklevel)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """
        Log ERROR level message

        Args:
            msg: Log message (string)
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments (can include event_type, event for structured logging)
        """
        # Extract event_type, event, and stacklevel from kwargs
        event_type = kwargs.pop("event_type", None)
        event = kwargs.pop("event", None)
        stacklevel = kwargs.pop("stacklevel", 2)
        formatted_msg = self._auto_format_message(msg, args)
        processed_msg = self._process_log_message(LogLevel.ERROR, formatted_msg, event_type, event, **kwargs)
        self._logger.error(processed_msg, stacklevel=stacklevel)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """
        Log CRITICAL level message

        Args:
            msg: Log message (string)
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments (can include event_type, event for structured logging)
        """
        # Extract event_type, event, and stacklevel from kwargs
        event_type = kwargs.pop("event_type", None)
        event = kwargs.pop("event", None)
        stacklevel = kwargs.pop("stacklevel", 2)
        formatted_msg = self._auto_format_message(msg, args)
        processed_msg = self._process_log_message(LogLevel.CRITICAL, formatted_msg, event_type, event, **kwargs)
        self._logger.critical(processed_msg, stacklevel=stacklevel)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """
        Log exception (includes stack trace)

        Args:
            msg: Log message (string)
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments (can include event_type, event for structured logging)
        """
        import traceback

        # Extract event_type, event, and stacklevel from kwargs
        event_type = kwargs.pop("event_type", None)
        event = kwargs.pop("event", None)
        stacklevel = kwargs.pop("stacklevel", 2)

        # Capture stack trace if not already provided
        if event is None and "stacktrace" not in kwargs:
            try:
                stacktrace = "".join(traceback.format_exc())
                if stacktrace and stacktrace.strip() != "NoneType: None":
                    kwargs["stacktrace"] = stacktrace
            except Exception:
                pass  # If traceback capture fails, continue without it

        formatted_msg = self._auto_format_message(msg, args)
        processed_msg = self._process_log_message(LogLevel.ERROR, formatted_msg, event_type, event, **kwargs)
        self._logger.exception(processed_msg, stacklevel=stacklevel)

    def log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        """
        Log message at specified level

        Args:
            level: Log level (integer)
            msg: Log message (string)
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments (can include event_type, event for structured logging)
        """
        # Map logging level to LogLevel enum
        log_level_map = {
            logging.DEBUG: LogLevel.DEBUG,
            logging.INFO: LogLevel.INFO,
            logging.WARNING: LogLevel.WARNING,
            logging.ERROR: LogLevel.ERROR,
            logging.CRITICAL: LogLevel.CRITICAL,
        }
        log_level = log_level_map.get(level, LogLevel.INFO)
        # Extract event_type, event, and stacklevel from kwargs
        event_type = kwargs.pop("event_type", None)
        event = kwargs.pop("event", None)
        stacklevel = kwargs.pop("stacklevel", 2)
        formatted_msg = self._auto_format_message(msg, args)
        processed_msg = self._process_log_message(log_level, formatted_msg, event_type, event, **kwargs)
        self._logger.log(level, processed_msg, stacklevel=stacklevel)

    def set_level(self, level: int) -> None:
        """Set log level"""
        self._logger.setLevel(level)

    def add_handler(self, handler: logging.Handler) -> None:
        """Add log handler"""
        self._logger.addHandler(handler)

    def remove_handler(self, handler: logging.Handler) -> None:
        """Remove log handler"""
        self._logger.removeHandler(handler)

    def add_filter(self, log_filter: logging.Filter) -> None:
        """Add log filter"""
        self._logger.addFilter(log_filter)

    def remove_filter(self, log_filter: logging.Filter) -> None:
        """Remove log filter"""
        self._logger.removeFilter(log_filter)

    def get_config(self) -> Dict[str, Any]:
        """
        Get log configuration

        Returns:
            Copy of configuration dictionary
        """
        return self.config.copy()

    def reconfigure(self, config: Dict[str, Any]) -> None:
        """
        Reconfigure logger

        Args:
            config: New configuration dictionary
        """
        self.config = config.copy()
        self._setup_logger()

    def logger(self):
        return self._logger

    def close(self) -> None:
        """Release logger handlers owned by this instance."""
        for handler in self._logger.handlers[:]:
            try:
                handler.close()
            finally:
                self._logger.removeHandler(handler)
