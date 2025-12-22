# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import sys
import inspect
import logging
from datetime import datetime, timezone
from typing import Dict, Any
from logging.handlers import RotatingFileHandler

from openjiuwen.core.common.logging.protocol import LoggerProtocol
from openjiuwen.core.common.logging.utils import (
    get_thread_session,
    get_log_max_bytes,
    normalize_and_validate_log_path,
)
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


_EXCLUDED_SUFFIXES = ('.pyc', '.pyo')
_EXCLUDED_KEYWORDS = (
    'log_handlers.py',
    'logger_impl.py',
    'log_manager.py',
    'default_impl.py',
    'test_',
    'logging',
)


class SafeRotatingFileHandler(RotatingFileHandler):
    def __init__(self, filename, *args, log_file_pattern=None, backup_file_pattern=None, **kwargs):
        """Initialize the secure round-robin file processor"""
        if log_file_pattern:
            filename = self._format_filename(filename, log_file_pattern)

        # Make sure the log file directory exists
        log_dir = os.path.dirname(filename)
        if log_dir:
            try:
                abs_log_dir = os.path.abspath(os.path.expanduser(log_dir))
                os.makedirs(abs_log_dir, mode=0o750, exist_ok=True)
            except OSError:
                pass

        super().__init__(filename, *args, **kwargs)
        self.backup_file_pattern = backup_file_pattern or "{baseFilename}.{index}"
        try:
            os.chmod(self.baseFilename, 0o640)
        except OSError as e:
            raise JiuWenBaseException(
                error_code=StatusCode.LOG_FILE_OPERATION_ERROR.code,
                message=StatusCode.LOG_FILE_OPERATION_ERROR.errmsg.format(
                    error_msg=f"Failed to set file permissions: {e}")
            ) from e

    def _format_filename(self, base_filename: str, pattern: str) -> str:
        """Format the file name according to the pattern"""
        dir_path = os.path.dirname(base_filename)
        file_name = os.path.basename(base_filename)

        if '.' in file_name:
            name_part, ext_part = file_name.rsplit('.', 1)
            ext = '.' + ext_part
        else:
            name_part = file_name
            ext = ''

        now = datetime.now(tz=timezone.utc)
        replacements = {
            'name': name_part,
            'ext': ext,
            'pid': str(os.getpid()),
            'timestamp': now.strftime('%Y%m%d%H%M%S'),
            'date': now.strftime('%Y%m%d'),
            'time': now.strftime('%H%M%S'),
            'datetime': now.strftime('%Y-%m-%d_%H-%M-%S'),
        }

        try:
            formatted_name = pattern.format(**replacements)

            if '{ext}' not in pattern and ext and not formatted_name.endswith(ext):
                formatted_name = formatted_name + ext

            if dir_path:
                return os.path.join(dir_path, formatted_name)
            else:
                return formatted_name
        except KeyError as e:
            return base_filename

    def doRollover(self):
        super().doRollover()
        for i in range(self.backupCount, 0, -1):
            sfn = self.backup_file_pattern.format(
                baseFilename=self.baseFilename,
                index=i
            )
            if os.path.exists(sfn):
                try:
                    os.chmod(sfn, 0o440)
                except OSError as e:
                    raise JiuWenBaseException(
                        error_code=StatusCode.LOG_FILE_OPERATION_ERROR.code,
                        message=StatusCode.LOG_FILE_OPERATION_ERROR.errmsg.format(
                            error_msg=f"Failed to set backup file permissions: {e}")
                    ) from e
        try:
            os.chmod(self.baseFilename, 0o640)
        except OSError as e:
            raise JiuWenBaseException(
                error_code=StatusCode.LOG_FILE_OPERATION_ERROR.code,
                message=StatusCode.LOG_FILE_OPERATION_ERROR.errmsg.format(
                    error_msg=f"Failed to set log file permissions: {e}")
            ) from e


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
                        not filename.endswith(_EXCLUDED_SUFFIXES)
                        and not any(k in filename for k in _EXCLUDED_KEYWORDS)):  # 跳过logging模块
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

        normalize_and_validate_log_path(log_file)

        for handler in self._logger.handlers[:]:
            handler.close()
            self._logger.removeHandler(handler)

        if 'console' in output:
            stream_handler = logging.StreamHandler(stream=sys.stdout)
            stream_handler.addFilter(ThreadContextFilter(self.log_type))
            stream_handler.setFormatter(self._get_formatter())
            self._logger.addHandler(stream_handler)

        if 'file' in output:
            try:
                abs_log_file = os.path.abspath(os.path.expanduser(log_file))
            except (OSError, TypeError) as e:
                # If path normalization fails, use original path and let SafeRotatingFileHandler handle it
                abs_log_file = log_file

            log_dir = os.path.dirname(abs_log_file)
            if log_dir:
                try:
                    os.makedirs(log_dir, mode=0o750, exist_ok=True)
                except OSError as e:
                    raise JiuWenBaseException(
                        error_code=StatusCode.LOG_PATH_CREATE_FAILED.code,
                        message=StatusCode.LOG_PATH_CREATE_FAILED.errmsg.format(path=log_dir, error_msg=str(e))
                    ) from e

            backup_count = self.config.get('backup_count', 20)
            max_bytes = get_log_max_bytes(self.config.get('max_bytes', 20 * 1024 * 1024))
            log_file_pattern = self.config.get('log_file_pattern', None)
            backup_file_pattern = self.config.get('backup_file_pattern', None)

            file_handler = SafeRotatingFileHandler(
                filename=abs_log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8',
                log_file_pattern=log_file_pattern,
                backup_file_pattern=backup_file_pattern
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

    def set_level(self, level: int) -> None:
        self._logger.setLevel(level)

    def add_handler(self, handler: logging.Handler) -> None:
        self._logger.addHandler(handler)

    def remove_handler(self, handler: logging.Handler) -> None:
        self._logger.removeHandler(handler)

    def add_filter(self, filter) -> None:
        self._logger.addFilter(filter)

    def remove_filter(self, filter) -> None:
        self._logger.removeFilter(filter)

    def get_config(self) -> Dict[str, Any]:
        """Obtain log configuration"""
        return self.config.copy()

    def reconfigure(self, config: Dict[str, Any]) -> None:
        """Reconfigure the logger"""
        self.config = config
        self._setup_logger() 
