#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
import os
from typing import Dict, Any, List

import yaml

from openjiuwen.extensions.common.configs.constant import DEFAULT_INNER_LOG_CONFIG


class LogConfig:
    def __init__(self, config_path: str = None):
        if config_path is None:
            self._log_config = copy.deepcopy(DEFAULT_INNER_LOG_CONFIG)
        else:
            self._log_config = self._load_config(config_path)
        self._log_path = self._get_log_path()

    def reload(self, config_path: str):
        """重新加载日志配置。"""
        self._log_config = self._load_config(config_path)
        self._log_path = self._get_log_path()

    @staticmethod
    def _load_config(config_path: str) -> Dict[str, Any]:
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            if 'logging' not in config:
                raise ValueError("YAML配置文件中缺少 'logging' 配置节")

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
            raise ValueError(f"YAML配置文件格式错误: {e}") from e
        except Exception as e:
            raise Exception(f"加载配置文件失败: {e}") from e

    def _get_log_path(self) -> str:
        return self._log_config.get('log_path', './logs/')

    def _get_base_config(self, log_file: str, output: List[str] = None) -> Dict[str, Any]:
        from .config_manager import name_to_level
        level_str = self._log_config.get('level', 'INFO').upper()
        level_value = name_to_level.get(level_str, 20)

        if output is None:
            output = self._log_config.get('output', ['console', 'file'])

        return {
            'log_file': os.path.join(self._log_path, log_file),
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
    供外部项目调用，用于指定自定义日志 YAML 配置路径。
    使用后会即时生效到全局 log_config。
    """
    log_config.reload(config_path)
