# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
import os
from typing import Any, Dict, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging.config_manager import (
    WARNING,
    normalize_log_level,
)
from openjiuwen.core.common.logging.default.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.common.logging.utils import normalize_and_validate_log_path

_LOGGER_BASE_KEYS = {
    "common": {
        "log_file_key": "log_file",
        "default_log_file": "run/jiuwen.log",
        "output_key": "output",
        "default_output": ["console", "file"],
    },
    "interface": {
        "log_file_key": "interface_log_file",
        "default_log_file": "interface/jiuwen_interface.log",
        "output_key": "interface_output",
        "default_output": ["console", "file"],
    },
    "prompt_builder": {
        "log_file_key": "prompt_builder_interface_log_file",
        "default_log_file": "interface/jiuwen_prompt_builder_interface.log",
        "output_key": "interface_output",
        "default_output": ["console", "file"],
    },
    "performance": {
        "log_file_key": "performance_log_file",
        "default_log_file": "performance/jiuwen_performance.log",
        "output_key": "performance_output",
        "default_output": ["console", "file"],
    },
}
_DEFAULT_ALLOWED_ROOT_KEYS = {
    "backend",
    "level",
    "structured_output_format",
    "backup_count",
    "max_bytes",
    "format",
    "log_path",
    "log_file",
    "output",
    "interface_log_file",
    "interface_output",
    "prompt_builder_interface_log_file",
    "performance_log_file",
    "performance_output",
    "log_file_pattern",
    "backup_file_pattern",
    "loggers",
}
_DEFAULT_ALLOWED_LOGGER_KEYS = {"level"}


def normalize_default_logging_config(logging_config: Any, default_level: int = WARNING) -> dict[str, Any]:
    if not isinstance(logging_config, dict):
        return copy.deepcopy(DEFAULT_INNER_LOG_CONFIG)

    normalized_config = copy.deepcopy(logging_config)
    normalized_config["backend"] = "default"
    normalized_config["level"] = normalize_log_level(
        normalized_config.get("level", DEFAULT_INNER_LOG_CONFIG.get("level", default_level)),
        default_level,
    )

    loggers_config = normalized_config.get("loggers")
    if loggers_config is None:
        normalized_config["loggers"] = {}
    elif isinstance(loggers_config, dict):
        normalized_config["loggers"] = {
            logger_name: _normalize_default_logger_config(logger_config, default_level)
            for logger_name, logger_config in loggers_config.items()
        }

    return normalized_config


def _normalize_default_logger_config(logger_config: Any, default_level: int) -> Any:
    if not isinstance(logger_config, dict):
        return logger_config

    normalized_logger = copy.deepcopy(logger_config)
    if "level" in normalized_logger:
        normalized_logger["level"] = normalize_log_level(normalized_logger["level"], default_level)
    return normalized_logger


def validate_default_backend_config(logging_config: Dict[str, Any]) -> None:
    unknown_keys = set(logging_config) - _DEFAULT_ALLOWED_ROOT_KEYS
    if unknown_keys:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"default backend config has unsupported keys: {sorted(unknown_keys)}"
        )

    loggers_config = logging_config.get("loggers")
    if loggers_config is None:
        return
    if not isinstance(loggers_config, dict):
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg="default backend config 'loggers' must be a mapping"
        )

    for logger_name, logger_config in loggers_config.items():
        if not isinstance(logger_config, dict):
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_INVALID,
                error_msg=f"default logger config for '{logger_name}' must be a mapping"
            )

        unknown_logger_keys = set(logger_config) - _DEFAULT_ALLOWED_LOGGER_KEYS
        if unknown_logger_keys:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_INVALID,
                error_msg=f"default logger '{logger_name}' has unsupported keys: {sorted(unknown_logger_keys)}"
            )


def load_default_backend_config(logging_config: Dict[str, Any]) -> Dict[str, Any]:
    normalized_config = normalize_default_logging_config(logging_config)
    validate_default_backend_config(normalized_config)
    return normalized_config


def build_default_logger_config(logging_config: Dict[str, Any], log_type: str) -> Dict[str, Any]:
    log_path = _get_log_path(logging_config)
    logger_keys = _LOGGER_BASE_KEYS.get(log_type, {})
    default_log_file = logger_keys.get("default_log_file", f"{log_type}.log")
    default_output = logger_keys.get("default_output", DEFAULT_INNER_LOG_CONFIG.get("output", ["console", "file"]))

    log_file_key = logger_keys.get("log_file_key")
    output_key = logger_keys.get("output_key", "output")

    configured_log_file = logging_config.get(log_file_key, default_log_file) if log_file_key else default_log_file
    configured_output = logging_config.get(output_key, default_output)

    config = {
        "backend": "default",
        "log_file": _resolve_log_file(log_path, configured_log_file),
        "output": copy.deepcopy(configured_output),
        "level": normalize_log_level(logging_config.get("level", DEFAULT_INNER_LOG_CONFIG.get("level", WARNING)), WARNING),
        "structured_output_format": logging_config.get(
            "structured_output_format",
            DEFAULT_INNER_LOG_CONFIG.get("structured_output_format", "json"),
        ),
        "backup_count": logging_config.get("backup_count", DEFAULT_INNER_LOG_CONFIG.get("backup_count", 20)),
        "max_bytes": logging_config.get("max_bytes", DEFAULT_INNER_LOG_CONFIG.get("max_bytes", 20971520)),
        "format": logging_config.get(
            "format",
            DEFAULT_INNER_LOG_CONFIG.get(
                "format",
                "%(asctime)s | %(log_type)s | %(trace_id)s | %(levelname)s | %(message)s",
            ),
        ),
        "log_file_pattern": logging_config.get("log_file_pattern", DEFAULT_INNER_LOG_CONFIG.get("log_file_pattern")),
        "backup_file_pattern": logging_config.get(
            "backup_file_pattern",
            DEFAULT_INNER_LOG_CONFIG.get("backup_file_pattern"),
        ),
    }

    level_override = _get_logger_level_override(logging_config, log_type)
    if level_override is not None:
        config["level"] = level_override

    config["level"] = normalize_log_level(config["level"], WARNING)
    config["log_file"] = _resolve_log_file(log_path, config["log_file"])
    return config


def _get_log_path(logging_config: Dict[str, Any]) -> str:
    log_path = logging_config.get("log_path", DEFAULT_INNER_LOG_CONFIG.get("log_path", "./logs/"))
    normalize_and_validate_log_path(log_path)
    return log_path


def _resolve_log_file(log_path: str, log_file: str) -> str:
    expanded_log_file = os.path.expanduser(log_file)
    if os.path.isabs(expanded_log_file):
        full_log_file = os.path.abspath(expanded_log_file)
    else:
        full_log_file = os.path.join(log_path, log_file)
    normalize_and_validate_log_path(full_log_file)
    return full_log_file


def _get_logger_level_override(logging_config: Dict[str, Any], log_type: str) -> Optional[int]:
    loggers_config = logging_config.get("loggers")
    if not isinstance(loggers_config, dict):
        return None

    logger_config = loggers_config.get(log_type)
    if not isinstance(logger_config, dict) or "level" not in logger_config:
        return None

    return normalize_log_level(logger_config["level"], WARNING)
