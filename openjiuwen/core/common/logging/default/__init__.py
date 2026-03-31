# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Default Logging Implementation Module

Provides default logging implementation components, including:
- DefaultLogger: Default logger implementation
- SafeRotatingFileHandler: Secure log file rotation handler
- ContextFilter: Context filter (adapted for async environments)
- LogConfig: Log configuration management
- ConfigManager: Configuration manager
"""

from openjiuwen.core.common.logging.config_manager import (
    ConfigManager,
    config,
)
from openjiuwen.core.common.logging.default.default_impl import (
    ContextFilter,
    DefaultLogger,
    SafeRotatingFileHandler,
)
from openjiuwen.core.common.logging.utils import (
    get_session_id,
    set_session_id,
)


def __getattr__(name):
    if name in {"LogConfig", "log_config"}:
        from openjiuwen.core.common.logging.log_config import LogConfig, log_config

        return {"LogConfig": LogConfig, "log_config": log_config}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # Configuration management
    "config",
    "ConfigManager",
    "log_config",
    "LogConfig",
    # Logger implementation
    "DefaultLogger",
    # Handlers and filters
    "SafeRotatingFileHandler",
    "ContextFilter",
    # Utility functions
    "set_session_id",
    "get_session_id",
]
