# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import threading
from typing import Optional, Any

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.security.path_checker import is_sensitive_path

_thread_log_instance = threading.local()


def set_thread_session(trace_id: str = "default_trace_id") -> None:
    _thread_log_instance.trace_id = trace_id


def get_thread_session() -> Optional[str]:
    return getattr(_thread_log_instance, 'trace_id', '')


def get_log_max_bytes(max_bytes_config) -> int:
    try:
        max_bytes = int(max_bytes_config)
    except (ValueError, TypeError) as e:
        raise JiuWenBaseException(
            error_code=StatusCode.LOG_CONFIG_INVALID_ERROR.code,
            message=StatusCode.LOG_CONFIG_INVALID_ERROR.errmsg.format(
                error_msg=f"Invalid max_bytes configuration: {max_bytes_config}, error: {e}")
        ) from e

    default_log_max_bytes = 100 * 1024 * 1024
    if max_bytes <= 0 or max_bytes > default_log_max_bytes:
        max_bytes = default_log_max_bytes

    return max_bytes 


def normalize_and_validate_log_path(path_value: Any) -> str:
    """
    Normalize log path (realpath -> abspath) and check sensitivity.

    This helper is shared by logger config and default logger implementation.
    It raises JiuWenBaseException when:
      - the value type is invalid, or
      - the normalized path is considered sensitive/unsafe.
    """
    # Support str / PathLike, and guard against invalid types / empty values
    try:
        path_str = os.fspath(path_value)
    except TypeError:
        raise JiuWenBaseException(
            error_code=StatusCode.LOG_PATH_SENSITIVE_ERROR.code,
            message=StatusCode.LOG_PATH_SENSITIVE_ERROR.errmsg.format(path=path_value),
        ) from None

    if not path_str or str(path_str).strip() == "":
        raise JiuWenBaseException(
            error_code=StatusCode.LOG_PATH_SENSITIVE_ERROR.code,
            message=StatusCode.LOG_PATH_SENSITIVE_ERROR.errmsg.format(path=path_str),
        )

    try:
        real_path = os.path.realpath(path_str)
    except OSError:
        real_path = os.path.abspath(os.path.expanduser(path_str))

    if is_sensitive_path(real_path):
        raise JiuWenBaseException(
            error_code=StatusCode.LOG_PATH_SENSITIVE_ERROR.code,
            message=StatusCode.LOG_PATH_SENSITIVE_ERROR.errmsg.format(path=real_path),
        )

    return real_path
