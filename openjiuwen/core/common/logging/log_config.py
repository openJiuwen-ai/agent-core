# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
from typing import Any, Dict, Optional

import yaml

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging.default.config_provider import (
    build_default_logger_config,
    load_default_backend_config,
)
from openjiuwen.core.common.logging.default.constant import (
    DEFAULT_INNER_LOG_CONFIG as DEFAULT_DEFAULT_LOG_CONFIG,
)
from openjiuwen.core.common.logging.log_levels import extract_backend
from openjiuwen.core.common.logging.loguru.config_provider import (
    build_loguru_logger_config,
    load_loguru_backend_config,
)

_BACKEND_LOADERS = {
    "default": load_default_backend_config,
    "loguru": load_loguru_backend_config,
}
_BACKEND_LOGGER_BUILDERS = {
    "default": build_default_logger_config,
    "loguru": build_loguru_logger_config,
}


class LogConfig:
    _BUILTIN_LOG_TYPES = ("common", "interface", "prompt_builder", "performance")

    def __init__(self, config_path: str = None):
        if config_path is None:
            self._log_config = self._normalize_loaded_config(copy.deepcopy(DEFAULT_DEFAULT_LOG_CONFIG))
        else:
            self._log_config = self._load_config(config_path)

    def reload(self, config_path: str):
        self._log_config = self._load_config(config_path)

    def load_from_dict(self, logging_config: Dict[str, Any]) -> None:
        self._log_config = self._normalize_loaded_config(copy.deepcopy(logging_config))

    def get_snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self._log_config)

    @classmethod
    def _load_config(cls, config_path: str) -> Dict[str, Any]:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            if "logging" not in config:
                raise build_error(
                    StatusCode.COMMON_LOG_CONFIG_INVALID,
                    error_msg="YAML configuration file is missing 'logging' section"
                )

            return cls._normalize_loaded_config(copy.deepcopy(config["logging"]))
        except FileNotFoundError:
            return cls._normalize_loaded_config(copy.deepcopy(DEFAULT_DEFAULT_LOG_CONFIG))
        except yaml.YAMLError as e:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_PROCESS_ERROR,
                error_msg=f"YAML configuration file format is incorrect: {e}"
            ) from e
        except OSError as e:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_PROCESS_ERROR,
                error_msg=f"failed to read configuration file: {e}"
            ) from e
        except Exception as e:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_PROCESS_ERROR,
                error_msg=f"unexpected error while loading configuration file: {e}"
            ) from e

    @classmethod
    def _normalize_loaded_config(cls, logging_config: Dict[str, Any]) -> Dict[str, Any]:
        backend = extract_backend(logging_config)
        loader = _BACKEND_LOADERS.get(backend)
        if loader is None:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_INVALID,
                error_msg=f"unsupported logging backend '{backend}'"
            )
        return loader(copy.deepcopy(logging_config))

    def get_logger_config(self, log_type: str, backend: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        resolved_backend = (backend or self.get_backend()).strip().lower()
        builder = _BACKEND_LOGGER_BUILDERS.get(resolved_backend)
        if builder is None:
            raise build_error(
                StatusCode.COMMON_LOG_CONFIG_INVALID,
                error_msg=f"unsupported logging backend '{resolved_backend}'"
            )

        config = builder(self._log_config, log_type)
        config.update(kwargs)
        return config

    def get_common_config(self, backend: Optional[str] = None) -> Dict[str, Any]:
        return self.get_logger_config("common", backend=backend)

    def get_interface_config(self, backend: Optional[str] = None) -> Dict[str, Any]:
        return self.get_logger_config("interface", backend=backend)

    def get_prompt_builder_config(self, backend: Optional[str] = None) -> Dict[str, Any]:
        return self.get_logger_config("prompt_builder", backend=backend)

    def get_performance_config(self, backend: Optional[str] = None) -> Dict[str, Any]:
        return self.get_logger_config("performance", backend=backend)

    def get_custom_config(self, log_type: str, backend: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        return self.get_logger_config(log_type, backend=backend, **kwargs)

    def get_backend(self) -> str:
        return extract_backend(self._log_config)

    def get_all_configs(self, backend: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        return {
            log_type: self.get_logger_config(log_type, backend=backend)
            for log_type in self._BUILTIN_LOG_TYPES
        }


log_config = LogConfig()


def configure_log(config_path: str):
    """It will take effect immediately upon use to the global log_config."""
    log_config.reload(config_path)
    from openjiuwen.core.common.logging.manager import LogManager

    LogManager.reset()


def configure_log_config(logging_config: Dict[str, Any]) -> None:
    """Replace the global logging config using an in-memory config snapshot."""
    log_config.load_from_dict(logging_config)
    from openjiuwen.core.common.logging.manager import LogManager

    LogManager.reset()


def get_log_config_snapshot() -> Dict[str, Any]:
    """Return a deep-copied snapshot of the active normalized logging config."""
    return log_config.get_snapshot()
