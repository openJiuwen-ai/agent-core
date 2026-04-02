# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
from typing import Any

CRITICAL = 50
FATAL = CRITICAL
ERROR = 40
WARNING = 30
WARN = WARNING
INFO = 20
DEBUG = 10
NOTSET = 0

name_to_level = {
    "CRITICAL": CRITICAL,
    "FATAL": FATAL,
    "ERROR": ERROR,
    "WARNING": WARNING,
    "WARN": WARN,
    "INFO": INFO,
    "DEBUG": DEBUG,
    "NOTSET": NOTSET,
}


def normalize_log_level(level: Any, default: int = WARNING) -> int:
    """Normalize a log level name/value to the integer logging level."""
    if isinstance(level, bool):
        return default
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        return name_to_level.get(level.upper(), default)
    return default


def extract_backend(logging_config: dict[str, Any]) -> str:
    """Extract and normalize the backend name from a logging config dict."""
    backend = logging_config.get("backend", "default")
    if not isinstance(backend, str) or not backend.strip():
        return "default"
    return backend.strip().lower()


def normalize_logging_config(logging_config: Any, default_level: int = WARNING) -> dict[str, Any]:
    """Normalize a logging config section by dispatching to the selected backend provider."""
    if not isinstance(logging_config, dict):
        return {"level": default_level}

    normalized_config = copy.deepcopy(logging_config)
    normalized_config["level"] = normalize_log_level(normalized_config.get("level", default_level), default_level)
    backend = extract_backend(normalized_config)

    if backend == "loguru":
        from openjiuwen.core.common.logging.loguru.config_provider import normalize_loguru_logging_config

        return normalize_loguru_logging_config(normalized_config, default_level=INFO)

    if backend != "default":
        return normalized_config

    from openjiuwen.core.common.logging.default.config_provider import normalize_default_logging_config

    return normalize_default_logging_config(normalized_config, default_level=default_level)
