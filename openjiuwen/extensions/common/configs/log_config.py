# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
import os
from typing import Dict, Any, List

import yaml

from openjiuwen.extensions.common.configs.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.common.logging.utils import normalize_and_validate_log_path
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


class LogConfig:
    def __init__(self, config_path: str = None):
        if config_path is None:
            self._log_config = copy.deepcopy(DEFAULT_INNER_LOG_CONFIG)
        else:
            self._log_config = self._load_config(config_path)
        self._log_path = self._get_log_path()

    def reload(self, config_path: str):
        self._log_config = self._load_config(config_path)
        self._log_path = self._get_log_path()

    @staticmethod
    def _load_config(config_path: str) -> Dict[str, Any]:
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            if 'logging' not in config:
                raise JiuWenBaseException(
                    error_code=StatusCode.LOG_CONFIG_INVALID_ERROR.code,
                    message=StatusCode.LOG_CONFIG_INVALID_ERROR.errmsg.format(
                        error_msg="YAML configuration file is missing 'logging' section")
                )

            return config['logging']
        except FileNotFoundError:
            # 找不到文件时，提供安全默认配置，避免外部项目因无 tests 目录而崩溃
            return {
                'level': 'WARNING',
                'output': ['console'],
                'log_path': './logs/',
                'log_file': 'run/jiuwen.log',
                'interface_log_file': 'interface/jiuwen_interface.log',
                'prompt_builder_interface_log_file': 'interface/jiuwen_prompt_builder_interface.log',
                'performance_log_file': 'performance/jiuwen_performance.log',
                'backup_count': 20,
                'max_bytes': 20971520,
                'format': '%(asctime)s | %(log_type)s | %(trace_id)s | %(levelname)s | %(message)s',
                'log_file_pattern': None,
                'backup_file_pattern': None
            }
        except yaml.YAMLError as e:
            raise JiuWenBaseException(
                error_code=StatusCode.LOG_CONFIG_LOAD_ERROR.code,
                message=StatusCode.LOG_CONFIG_LOAD_ERROR.errmsg.format(
                    error_msg=f"YAML configuration file format is incorrect: {e}")
            ) from e
        except OSError as e:
            raise JiuWenBaseException(
                error_code=StatusCode.LOG_CONFIG_LOAD_ERROR.code,
                message=StatusCode.LOG_CONFIG_LOAD_ERROR.errmsg.format(
                    error_msg=f"Failed to read configuration file: {e}")
            ) from e
        except Exception as e:
            raise JiuWenBaseException(
                error_code=StatusCode.LOG_CONFIG_LOAD_ERROR.code,
                message=StatusCode.LOG_CONFIG_LOAD_ERROR.errmsg.format(
                    error_msg=f"Unexpected error while loading configuration file: {e}")
            ) from e

    def _get_log_path(self) -> str:
        log_path = self._log_config.get('log_path', './logs/')
        normalize_and_validate_log_path(log_path)
        return log_path

    def _get_base_config(self, log_file: str, output: List[str] = None) -> Dict[str, Any]:
        from .config_manager import name_to_level
        level_str = self._log_config.get('level', 'INFO').upper()
        level_value = name_to_level.get(level_str, 20)

        if output is None:
            output = self._log_config.get('output', ['console', 'file'])

        full_log_file = os.path.join(self._log_path, log_file)
        normalize_and_validate_log_path(full_log_file)

        return {
            'log_file': full_log_file,
            'output': output,
            'level': level_value,
            'backup_count': self._log_config.get('backup_count', 20),
            'max_bytes': self._log_config.get('max_bytes', 20971520),
            'format': self._log_config.get('format',
                                           '%(asctime)s | %(log_type)s | %(trace_id)s | %(levelname)s | %(message)s'),
            'log_file_pattern': self._log_config.get('log_file_pattern', None),
            'backup_file_pattern': self._log_config.get('backup_file_pattern', None)
        }

    def get_common_config(self) -> Dict[str, Any]:
        return self._get_base_config(self._log_config.get('log_file', 'run/jiuwen.log'))

    def get_interface_config(self) -> Dict[str, Any]:
        return self._get_base_config(
            self._log_config.get('interface_log_file', 'interface/jiuwen_interface.log'),
            self._log_config.get('interface_output', ['console', 'file'])
        )

    def get_prompt_builder_config(self) -> Dict[str, Any]:
        return self._get_base_config(
            self._log_config.get('prompt_builder_interface_log_file', 'interface/jiuwen_prompt_builder_interface.log'),
            self._log_config.get('interface_output', ['console', 'file'])
        )

    def get_performance_config(self) -> Dict[str, Any]:
        return self._get_base_config(
            self._log_config.get('performance_log_file', 'performance/jiuwen_performance.log'),
            self._log_config.get('performance_output', ['console', 'file'])
        )

    def get_custom_config(self, log_type: str, **kwargs) -> Dict[str, Any]:
        base_config = self._get_base_config(f'{log_type}.log')
        base_config.update(kwargs)
        return base_config

    def get_all_configs(self) -> Dict[str, Dict[str, Any]]:
        return {
            'common': self.get_common_config(),
            'interface': self.get_interface_config(),
            'prompt_builder': self.get_prompt_builder_config(),
            'performance': self.get_performance_config()
        }


log_config = LogConfig()


def configure_log(config_path: str):
    """
    It will take effect immediately upon use to the global log_config.
    """
    log_config.reload(config_path)
