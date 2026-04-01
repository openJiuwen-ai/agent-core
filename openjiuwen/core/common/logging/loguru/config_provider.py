# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
import os
from typing import Any, Dict

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging.config_manager import (
    INFO,
    normalize_log_level,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.common.logging.utils import normalize_and_validate_log_path

_LOGURU_ALLOWED_ROOT_KEYS = {
    "backend",
    "level",
    "defaults",
    "sinks",
    "routes",
    "loggers",
}
_LOGURU_ALLOWED_SINK_KEYS = {
    "target",
    "level",
    "serialize",
    "serialize_mode",
    "format",
    "colorize",
    "enqueue",
    "catch",
    "backtrace",
    "diagnose",
    "rotation",
    "retention",
    "compression",
    "encoding",
}
_LOGURU_ALLOWED_LOGGER_KEYS = {"level"}
_STD_STREAM_TARGETS = {"stdout", "stderr"}
_LOGURU_SERIALIZE_MODES = {"loguru", "event"}


def normalize_loguru_logging_config(logging_config: Any, default_level: int = INFO) -> dict[str, Any]:
    if not isinstance(logging_config, dict):
        return copy.deepcopy(DEFAULT_INNER_LOG_CONFIG)

    normalized_config = copy.deepcopy(logging_config)
    normalized_config["backend"] = "loguru"

    defaults_config = normalized_config.get("defaults")
    if not isinstance(defaults_config, dict):
        defaults_config = {}

    effective_default_level = normalize_log_level(
        defaults_config.get(
            "level", normalized_config.get("level", DEFAULT_INNER_LOG_CONFIG["defaults"].get("level", default_level))
        ),
        default_level,
    )

    merged_defaults = copy.deepcopy(DEFAULT_INNER_LOG_CONFIG.get("defaults", {}))
    merged_defaults.update(defaults_config)
    merged_defaults["level"] = effective_default_level

    normalized_config["level"] = effective_default_level
    normalized_config["defaults"] = merged_defaults

    sinks_config = normalized_config.get("sinks")
    if isinstance(sinks_config, dict):
        normalized_config["sinks"] = {
            sink_name: _normalize_loguru_sink_config(sink_config, effective_default_level)
            for sink_name, sink_config in sinks_config.items()
        }
    else:
        normalized_config["sinks"] = {}

    routes_config = normalized_config.get("routes")
    if isinstance(routes_config, dict):
        normalized_config["routes"] = {
            route_name: _normalize_route_targets(route_name, route_targets)
            for route_name, route_targets in routes_config.items()
        }
    else:
        normalized_config["routes"] = {}

    loggers_config = normalized_config.get("loggers")
    if loggers_config is None:
        normalized_config["loggers"] = {}
    elif isinstance(loggers_config, dict):
        normalized_config["loggers"] = {
            logger_name: _normalize_loguru_logger_config(logger_config, effective_default_level)
            for logger_name, logger_config in loggers_config.items()
        }

    return normalized_config


def _normalize_loguru_sink_config(sink_config: Any, default_level: int) -> Any:
    if not isinstance(sink_config, dict):
        return sink_config

    normalized_sink = copy.deepcopy(sink_config)
    if "level" in normalized_sink:
        normalized_sink["level"] = normalize_log_level(normalized_sink["level"], default_level)
    if isinstance(normalized_sink.get("serialize_mode"), str):
        normalized_sink["serialize_mode"] = normalized_sink["serialize_mode"].strip().lower()
    return normalized_sink


def _normalize_route_targets(route_name: str, route_targets: Any) -> Any:
    if not isinstance(route_targets, (list, tuple)):
        return route_targets

    normalized_targets: list[str] = []
    for sink_name in route_targets:
        if isinstance(sink_name, str) and sink_name.strip():
            normalized_targets.append(sink_name.strip())
        else:
            normalized_targets.append(sink_name)
    return normalized_targets


def _normalize_loguru_logger_config(logger_config: Any, default_level: int) -> Any:
    if not isinstance(logger_config, dict):
        return logger_config

    normalized_logger = copy.deepcopy(logger_config)
    if "level" in normalized_logger:
        normalized_logger["level"] = normalize_log_level(normalized_logger["level"], default_level)
    return normalized_logger


def validate_loguru_backend_config(logging_config: Dict[str, Any]) -> None:
    unknown_keys = set(logging_config) - _LOGURU_ALLOWED_ROOT_KEYS
    if unknown_keys:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"loguru backend config has unsupported keys: {sorted(unknown_keys)}"
        )

    sinks_config = logging_config.get("sinks")
    if not isinstance(sinks_config, dict) or not sinks_config:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg="loguru config requires a non-empty 'sinks' mapping"
        )

    for sink_name, sink_config in sinks_config.items():
        _validate_loguru_sink_template(sink_name, sink_config)

    routes_config = logging_config.get("routes")
    if not isinstance(routes_config, dict) or not routes_config:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg="loguru config requires a non-empty 'routes' mapping"
        )

    for route_name, route_targets in routes_config.items():
        _validate_sink_name_list(route_name, route_targets, sinks_config)

    loggers_config = logging_config.get("loggers")
    if loggers_config is None:
        return
    if not isinstance(loggers_config, dict):
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg="loguru config 'loggers' must be a mapping"
        )

    for logger_name, logger_config in loggers_config.items():
        _validate_loguru_logger_template(logger_name, logger_config)


def _validate_loguru_sink_template(sink_name: str, sink_config: Dict[str, Any]) -> None:
    if not isinstance(sink_config, dict):
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"loguru sink config must be a mapping, got {type(sink_config)}"
        )

    unknown_keys = set(sink_config) - _LOGURU_ALLOWED_SINK_KEYS
    if unknown_keys:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"loguru sink '{sink_name}' has unsupported keys: {sorted(unknown_keys)}"
        )

    if "target" not in sink_config:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"loguru sink '{sink_name}' is missing required key 'target'"
        )

    resolve_loguru_target(sink_config["target"])

    serialize_mode = sink_config.get("serialize_mode")
    if serialize_mode is not None and serialize_mode not in _LOGURU_SERIALIZE_MODES:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=(
                f"loguru sink '{sink_name}' has invalid serialize_mode '{serialize_mode}', "
                f"expected one of {sorted(_LOGURU_SERIALIZE_MODES)}"
            )
        )


def _validate_loguru_logger_template(logger_name: str, logger_config: Dict[str, Any]) -> None:
    if not isinstance(logger_config, dict):
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"loguru logger '{logger_name}' must be a mapping"
        )

    unknown_keys = set(logger_config) - _LOGURU_ALLOWED_LOGGER_KEYS
    if unknown_keys:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"loguru logger '{logger_name}' has unsupported keys: {sorted(unknown_keys)}"
        )


def _validate_sink_name_list(route_name: str, sink_names: list[str], sinks_config: Dict[str, Any]) -> None:
    if not isinstance(sink_names, list):
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"loguru route '{route_name}' must be a list of sink names"
        )

    for sink_name in sink_names:
        if not isinstance(sink_name, str) or sink_name not in sinks_config:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_INVALID,
                error_msg=f"loguru route '{route_name}' references unknown sink '{sink_name}'"
            )


def load_loguru_backend_config(logging_config: Dict[str, Any]) -> Dict[str, Any]:
    normalized_config = normalize_loguru_logging_config(logging_config)
    validate_loguru_backend_config(normalized_config)
    return normalized_config


def build_loguru_logger_config(logging_config: Dict[str, Any], log_type: str) -> Dict[str, Any]:
    defaults = copy.deepcopy(logging_config.get("defaults", DEFAULT_INNER_LOG_CONFIG.get("defaults", {})))
    base_sinks = copy.deepcopy(logging_config.get("sinks", {}))
    effective_level = _get_logger_level_override(logging_config, log_type)
    if effective_level is None:
        effective_level = normalize_log_level(defaults.get("level", INFO), INFO)
    effective_sink_names = resolve_route_sink_names(logging_config, log_type)

    sink_defaults = {
        key: value
        for key, value in defaults.items()
        if key in _LOGURU_ALLOWED_SINK_KEYS
    }

    materialized_sinks = []
    for sink_name in effective_sink_names:
        if sink_name not in base_sinks:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_INVALID,
                error_msg=f"loguru logger '{log_type}' references unknown sink '{sink_name}'"
            )

        sink_config = copy.deepcopy(sink_defaults)
        sink_config.update(copy.deepcopy(base_sinks[sink_name]))
        sink_config["name"] = sink_name
        sink_config["target"] = resolve_loguru_target(sink_config["target"])
        sink_config["level"] = normalize_log_level(sink_config.get("level", defaults.get("level", INFO)), INFO)
        materialized_sinks.append(sink_config)

    return {
        "backend": "loguru",
        "level": effective_level,
        "effective_level": effective_level,
        "sinks": materialized_sinks,
    }


def resolve_route_sink_names(logging_config: Dict[str, Any], log_type: str) -> list[str]:
    routes_config = logging_config.get("routes", {})
    if not isinstance(routes_config, dict):
        routes_config = {}

    route_targets = routes_config.get(log_type)
    if route_targets is None:
        route_targets = routes_config.get("*")

    if route_targets is None:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"loguru logger '{log_type}' does not have a route and no '*' fallback is configured"
        )

    return list(route_targets)


def resolve_loguru_target(target: Any) -> str:
    if not isinstance(target, str) or not target.strip():
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"loguru sink target is invalid: {target}"
        )

    normalized_target = target.strip()
    lowered_target = normalized_target.lower()
    if lowered_target in _STD_STREAM_TARGETS:
        return lowered_target

    expanded_target = os.path.abspath(os.path.expanduser(normalized_target))
    normalize_and_validate_log_path(expanded_target)
    return expanded_target


def _get_logger_level_override(logging_config: Dict[str, Any], log_type: str) -> int | None:
    loggers_config = logging_config.get("loggers")
    if not isinstance(loggers_config, dict):
        return None

    logger_config = loggers_config.get(log_type)
    if not isinstance(logger_config, dict) or "level" not in logger_config:
        return None

    return normalize_log_level(logger_config["level"], INFO)
