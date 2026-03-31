# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
import os
from typing import Any

import yaml

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging.default.constant import DEFAULT_LOG_CONFIG
from openjiuwen.core.common.security.path_checker import is_sensitive_path

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


def normalize_logging_config(logging_config: Any, default_level: int = WARNING) -> dict[str, Any]:
    """Normalize a logging config section by dispatching to the selected backend provider."""
    if not isinstance(logging_config, dict):
        return {"level": default_level}

    normalized_config = copy.deepcopy(logging_config)
    normalized_config["level"] = normalize_log_level(normalized_config.get("level", default_level), default_level)
    backend = _extract_backend(normalized_config)

    if backend == "loguru":
        from openjiuwen.core.common.logging.loguru.config_provider import normalize_loguru_logging_config

        return normalize_loguru_logging_config(normalized_config, default_level=INFO)

    if backend != "default":
        return normalized_config

    from openjiuwen.core.common.logging.default.config_provider import normalize_default_logging_config

    return normalize_default_logging_config(normalized_config, default_level=default_level)


def _extract_backend(logging_config: dict[str, Any]) -> str:
    backend = logging_config.get("backend", "default")
    if not isinstance(backend, str) or not backend.strip():
        return "default"
    return backend.strip().lower()


class ConfigManager:

    def __init__(self, config_path: str = None):
        self._config = None
        self._load_config(config_path)

    def reload(self, config_path: str):
        self._load_config(config_path)

    def _load_config(self, config_path: str):
        try:
            if config_path is None:
                config_dict = copy.deepcopy(DEFAULT_LOG_CONFIG)
            else:
                try:
                    real_path = os.path.realpath(config_path)
                except OSError:
                    real_path = os.path.abspath(os.path.expanduser(config_path))

                if is_sensitive_path(real_path):
                    raise build_error(
                        StatusCode.COMMON_LOG_PATH_INVALID,
                        error_msg=f"the path is {real_path}"
                    )

                try:
                    with open(real_path, "r", encoding="utf-8") as f:
                        config_dict = yaml.safe_load(f)
                except OSError as e:
                    raise build_error(
                        StatusCode.COMMON_LOG_CONFIG_PROCESS_ERROR,
                        error_msg=f"failed to read configuration file: {e}"
                    ) from e

            if isinstance(config_dict, dict) and "logging" in config_dict:
                config_dict["logging"] = normalize_logging_config(config_dict["logging"])

            self._config = config_dict
        except FileNotFoundError:
            self._config = copy.deepcopy(DEFAULT_LOG_CONFIG)
        except BaseError:
            raise
        except yaml.YAMLError as e:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_PROCESS_ERROR,
                error_msg=f"YAML configuration file format is incorrect: {e}"
            ) from e
        except Exception as e:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_PROCESS_ERROR,
                error_msg=f"unexpected error while loading configuration file: {e}"
            ) from e

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    @property
    def config(self) -> dict:
        return self._config

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


class ConfigDict(dict):

    def __init__(self, local_config_manager: ConfigManager):
        super().__init__(local_config_manager._config)
        self._config_manager = local_config_manager

    def get(self, key: str, default: Any = None) -> Any:
        return self._config_manager.get(key, default)

    def __call__(self):
        return self

    def refresh(self):
        self.clear()
        self.update(self._config_manager.config)


config_manager = ConfigManager()
config = ConfigDict(config_manager)


def configure(config_path: str):
    """For external project invocation, it is used to specify a custom YAML configuration path."""
    config_manager.reload(config_path)
    config.refresh()
