# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Log Manager

This implementation is optimized for async environments (e.g., asyncio), with thread lock mechanism removed.
In single-threaded async environments, due to GIL and single-threaded execution characteristics,
no additional thread synchronization mechanism is needed.

Note: If used in multi-threaded environments, additional synchronization mechanisms may be required.
"""

from typing import (
    Any,
    Dict,
    Optional,
    Type,
)

from openjiuwen.core.common.logging.protocol import LoggerProtocol


class LogManager:
    """
    Log Manager Class

    Provides logger creation, registration, and retrieval functionality.
    Optimized for async environments, no thread locks used.
    """

    _loggers: Dict[str, LoggerProtocol] = {}
    _initialized = False
    _default_logger_class: Optional[Type[LoggerProtocol]] = None
    _selected_backend: Optional[str] = None

    @classmethod
    def set_default_logger_class(cls, logger_class: Type[LoggerProtocol]) -> None:
        """Set default logger class"""
        cls._default_logger_class = logger_class

    @classmethod
    def initialize(cls, backend: Optional[str] = None) -> None:
        """
        Initialize log manager

        Note: In async environments, this is typically called only once at application startup.
        If called multiple times, already initialized parts will be skipped (idempotent operation).
        """
        if cls._initialized:
            return

        cls._selected_backend = backend.strip().lower() if isinstance(backend, str) and backend.strip() else None
        log_config = cls._get_log_config()
        resolved_backend = cls._selected_backend
        if resolved_backend is None and log_config is not None and hasattr(log_config, "get_backend"):
            resolved_backend = log_config.get_backend()  # type: ignore[assignment,no-any-return]

        default_logger_class = cls._get_default_logger_class(resolved_backend)

        if log_config:
            all_configs: Dict[str, Dict[str, Any]] = log_config.get_all_configs(backend=cls._selected_backend)  # type: ignore[attr-defined]
            for log_type, config in all_configs.items():  # type: ignore[misc]
                if log_type not in cls._loggers:
                    cls._loggers[log_type] = default_logger_class(log_type, config)  # type: ignore[call-arg]
        else:
            raise RuntimeError(
                "LogConfig not available. Please ensure extensions.common. configs.log_config is properly configured."
            )

        cls._initialized = True

    @classmethod
    def register_logger(cls, log_type: str, logger: LoggerProtocol) -> None:
        """
        Register custom logger

        Args:
            log_type: Log type identifier
            logger: Logger instance, must implement LoggerProtocol

        Raises:
            TypeError: If logger does not implement LoggerProtocol
        """
        # Runtime type check to ensure logger implements required protocol methods
        if not hasattr(logger, "info") or not hasattr(logger, "debug"):
            raise TypeError(f"Logger must implement LoggerProtocol, got {type(logger)}")

        cls._loggers[log_type] = logger

    @classmethod
    def get_logger(cls, log_type: str) -> LoggerProtocol:
        """
        Get logger, create default logger if not exists

        Args:
            log_type: Log type identifier

        Returns:
            Logger instance

        Raises:
            RuntimeError: If config is unavailable and logger does not exist
        """
        if not cls._initialized:
            cls.initialize()

        if log_type not in cls._loggers:
            default_logger_class = cls._get_default_logger_class()
            log_config = cls._get_log_config()

            if log_config:
                config = log_config.get_custom_config(log_type, backend=cls._selected_backend)  # type: ignore[attr-defined]
            else:
                raise RuntimeError(f"LogConfig not available. Cannot create logger for '{log_type}'.")

            cls._loggers[log_type] = default_logger_class(log_type, config)  # type: ignore[call-arg]

        return cls._loggers[log_type]

    @classmethod
    def get_all_loggers(cls) -> Dict[str, LoggerProtocol]:
        """
        Get all registered loggers

        Returns:
            Copy dictionary of all loggers
        """
        if not cls._initialized:
            cls.initialize()
        return cls._loggers.copy()

    @classmethod
    def reset(cls) -> None:
        """
        Reset log manager

        Clear all loggers and initialization state.
        Mainly used for testing scenarios.
        """
        for logger in cls._loggers.values():
            close_logger = getattr(logger, "close", None)
            if callable(close_logger):
                close_logger()
        cls._loggers = {}
        cls._initialized = False
        cls._default_logger_class = None
        cls._selected_backend = None
        try:
            from openjiuwen.core.common.logging import reset_lazy_loggers

            reset_lazy_loggers()
        except Exception:
            pass
        try:
            from openjiuwen.core.common.logging.events import reset_common_logger_cache

            reset_common_logger_cache()
        except Exception:
            pass

    @classmethod
    def _get_default_logger_class(cls, backend: Optional[str] = None) -> Type[LoggerProtocol]:
        resolved_backend = backend or cls._get_configured_backend()

        if resolved_backend:
            cls._default_logger_class = cls._get_logger_class_for_backend(resolved_backend)

        if cls._default_logger_class is None:
            try:
                from openjiuwen.core.common.logging.default.default_impl import DefaultLogger

                cls._default_logger_class = DefaultLogger
            except ImportError as e:
                raise RuntimeError("No default logger class set and cannot import DefaultLogger from extensions") from e
        return cls._default_logger_class

    @classmethod
    def _get_configured_backend(cls) -> Optional[str]:
        log_config = cls._get_log_config()
        if log_config is None or not hasattr(log_config, "get_backend"):
            return None
        return log_config.get_backend()  # type: ignore[no-any-return]

    @classmethod
    def _get_logger_class_for_backend(cls, backend: str) -> Type[LoggerProtocol]:
        backend_name = backend.strip().lower()

        if backend_name == "default":
            from openjiuwen.core.common.logging.default.default_impl import DefaultLogger

            return DefaultLogger

        if backend_name == "loguru":
            try:
                from openjiuwen.core.common.logging.loguru.loguru_impl import LoguruLogger
            except ImportError as e:
                raise RuntimeError("Loguru backend requested but loguru is not available.") from e

            return LoguruLogger

        raise RuntimeError(f"Unsupported logging backend: {backend}")

    @classmethod
    def _get_log_config(cls) -> Optional[object]:
        try:
            from openjiuwen.core.common.logging.log_config import log_config

            return log_config
        except ImportError:
            return None
