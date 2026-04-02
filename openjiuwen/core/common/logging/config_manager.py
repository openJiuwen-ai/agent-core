# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
import os
from typing import Any

import yaml

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging.default.constant import DEFAULT_LOG_CONFIG
from openjiuwen.core.common.logging.log_levels import (  # noqa: F401 — re-exported for backwards compatibility
    CRITICAL,
    DEBUG,
    ERROR,
    FATAL,
    INFO,
    NOTSET,
    WARN,
    WARNING,
    extract_backend,
    name_to_level,
    normalize_log_level,
    normalize_logging_config,
)
from openjiuwen.core.common.security.path_checker import is_sensitive_path


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
