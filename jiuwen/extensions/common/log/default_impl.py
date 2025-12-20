#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import sys
import inspect
import logging
from typing import Dict, Any
from logging.handlers import RotatingFileHandler

from jiuwen.core.common.logging.protocol import LoggerProtocol
from jiuwen.core.common.logging.utils import get_thread_session, get_log_max_bytes
from jiuwen.core.utils.config.path_checker import is_sensitive_path


class SafeRotatingFileHandler(RotatingFileHandler):
    def __init__(self, filename, *args, **kwargs):
        pid = os.getpid()
        filename = f"{filename}-{pid}"
        super().__init__(filename, *args, **kwargs)
        os.chmod(self.baseFilename, 0o640)

    def doRollover(self):
        super().doRollover()
        for i in range(self.backupCount, 0, -1):
            sfn = f"{self.baseFilename}.{i}"
            if os.path.exists(sfn):
                os.chmod(sfn, 0o440)
        os.chmod(self.baseFilename, 0o640)


class ThreadContextFilter(logging.Filter):
    def __init__(self, log_type: str):
        super().__init__()
        self.log_type = log_type

    def filter(self, record):
        record.trace_id = get_thread_session()
        record.log_type = "perf" if self.log_type == 'performance' else self.log_type
        return True


class CallerAwareFormatter(logging.Formatter):

    def __init__(self, fmt=None, datefmt=None, style='%'):
        super().__init__(fmt, datefmt, style)
        self._skip_modules = {
            'jiuwen.extensions.common.log.default_impl',
            'jiuwen.core.common.logging.manager',
            'jiuwen.core.common.logging',
            'logging',
            'threading',
            'unittest'
        }

    def format(self, record):

        caller_info = self._get_caller_info()
        if caller_info:
            record.filename = caller_info['filename']
            record.lineno = caller_info['lineno']
            record.funcName = caller_info['funcName']
            record.pathname = caller_info['pathname']

        return super().format(record)

    def _get_caller_info(self):
        try:
            stack = inspect.stack()

            for frame_info in stack:
                frame = frame_info.frame
                module_name = frame.f_globals.get('__name__', '')

                if any(module_name.startswith(skip) for skip in self._skip_modules):
                    continue

                filename = frame_info.filename
                lineno = frame_info.lineno
                func_name = frame_info.function

                if (filename and
                    not filename.endswith('.pyc') and
                    not filename.endswith('.pyo') and
                    'log_handlers.py' not in filename and
                    'logger_impl.py' not in filename and
                    'log_manager.py' not in filename and
                    'default_impl.py' not in filename and
                    'test_' not in filename and
                    'logging' not in filename):  # 跳过logging模块
                    return {
                        'filename': os.path.basename(filename),
                        'lineno': lineno,
                        'funcName': func_name,
                        'pathname': filename
                    }

            return None
        except Exception:
            return None


class DefaultLogger(LoggerProtocol):
    """默认日志实现"""
    _CONTROL_CHAR_MAP = {
        '\r': '\\r',
        '\n': '\\n',
        '\t': '\\t',
        '\b': '\\b',
        '\v': '\\v',
        '\f': '\\f',
        '\0': '\\0',
    }

    def __init__(self, log_type: str, config: Dict[str, Any]):
        self.log_type = log_type
        self.config = config
        self._logger = logging.getLogger(log_type)
        self._setup_logger()

    def _setup_logger(self):
        level_config = self.config.get('level', 'WARNING')

        if isinstance(level_config, str):
            level = getattr(logging, level_config.upper(), logging.WARNING)
        elif isinstance(level_config, int):
            level = level_config
        else:
            level = logging.WARNING
            
        self._logger.setLevel(level)

        output = self.config.get('output', ['console'])
        log_file = self.config.get('log_file', f'{self.log_type}.log')

        if is_sensitive_path(log_file):
            raise Exception("log file path is not safe")

        for handler in self._logger.handlers[:]:
            handler.close()
            self._logger.removeHandler(handler)

        if 'console' in output:
            stream_handler = logging.StreamHandler(stream=sys.stdout)  
            stream_handler.addFilter(ThreadContextFilter(self.log_type))
            stream_handler.setFormatter(self._get_formatter())
            self._logger.addHandler(stream_handler)

        if 'file' in output:
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, mode=0o750, exist_ok=True)

            backup_count = self.config.get('backup_count', 20)
            max_bytes = get_log_max_bytes(self.config.get('max_bytes', 20 * 1024 * 1024))

            file_handler = SafeRotatingFileHandler(
                filename=log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.addFilter(ThreadContextFilter(self.log_type))
            file_handler.setFormatter(self._get_formatter())
            self._logger.addHandler(file_handler)

    def _get_formatter(self) -> logging.Formatter:
        log_format = self.config.get(
            'format') or '%(asctime)s.%(msecs)03d | %(log_type)s | %(trace_id)s | %(levelname)s | %(message)s'
        return CallerAwareFormatter(log_format, datefmt='%Y-%m-%d %H:%M:%S')

    def _sanitize_message(self, msg: str) -> str:
        if not isinstance(msg, str):
            return msg
        result = []
        for char in msg:
            code = ord(char)
            if code < 32 or code == 127:
                result.append(self._CONTROL_CHAR_MAP.get(char, f'\\x{code:02x}'))
            else:
                result.append(char)
        return ''.join(result)

    def debug(self, msg: str, *args, **kwargs) -> None:
        msg = self._sanitize_message(msg)
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        msg = self._sanitize_message(msg)
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        msg = self._sanitize_message(msg)
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        msg = self._sanitize_message(msg)
        self._logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        msg = self._sanitize_message(msg)
        self._logger.critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        msg = self._sanitize_message(msg)
        self._logger.exception(msg, *args, **kwargs)

    def log(self, level: int, msg: str, *args, **kwargs) -> None:
        msg = self._sanitize_message(msg)
        self._logger.log(level, msg, *args, **kwargs)

    def setLevel(self, level: int) -> None:
        self._logger.setLevel(level)

    def addHandler(self, handler: logging.Handler) -> None:
        self._logger.addHandler(handler)

    def removeHandler(self, handler: logging.Handler) -> None:
        self._logger.removeHandler(handler)

    def addFilter(self, filter) -> None:
        self._logger.addFilter(filter)

    def removeFilter(self, filter) -> None:
        self._logger.removeFilter(filter)

    def get_config(self) -> Dict[str, Any]:
        """获取日志配置"""
        return self.config.copy()

    def reconfigure(self, config: Dict[str, Any]) -> None:
        """重新配置日志记录器"""
        self.config = config
        self._setup_logger() 