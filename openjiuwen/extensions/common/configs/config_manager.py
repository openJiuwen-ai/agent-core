#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
import os
from typing import Any

import yaml

from openjiuwen.extensions.common.configs.constant import DEFAULT_LOG_CONFIG
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
    'CRITICAL': CRITICAL,
    'FATAL': FATAL,
    'ERROR': ERROR,
    'WARNING': WARNING,
    'WARN': WARN,
    'INFO': INFO,
    'DEBUG': DEBUG,
    'NOTSET': NOTSET,
}


class ConfigManager:

    def __init__(self, config_path: str = None):
        self._config = None
        self._load_config(config_path)

    def reload(self, config_path: str):
        """重新加载配置文件。"""
        self._load_config(config_path)

    def _load_config(self, config_path: str):
        try:
            if config_path is None:
                config_dict = copy.deepcopy(DEFAULT_LOG_CONFIG)
            else:
                real_path = os.path.realpath(config_path)
                if is_sensitive_path(real_path):
                    raise Exception("path is not safe")
                with open(real_path, "r", encoding="utf-8") as f:
                    config_dict = yaml.safe_load(f)

            if 'logging' in config_dict:
                level_str = config_dict['logging'].get('level', 'WARNING').upper()
                config_dict['logging']['level'] = name_to_level.get(level_str, WARNING)

            self._config = config_dict
        except FileNotFoundError:
            # 若找不到配置文件，回退到安全的默认配置，避免在被外部项目引用时崩溃
            self._config = {
                'logging': {
                    'level': WARNING
                }
            }
        except yaml.YAMLError as e:
            raise ValueError(f"YAML配置文件格式错误: {e}") from e
        except Exception as e:
            raise Exception(f"加载配置文件失败: {e}") from e

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split('.')
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
        """在底层配置重载后刷新自身内容。"""
        self.clear()
        self.update(self._config_manager.config)


config_manager = ConfigManager()
config = ConfigDict(config_manager)


def configure(config_path: str):
    """
    供外部项目调用，用于指定自定义的 YAML 配置路径。
    使用后会即时生效到全局 config。
    """
    config_manager.reload(config_path)
    config.refresh()
