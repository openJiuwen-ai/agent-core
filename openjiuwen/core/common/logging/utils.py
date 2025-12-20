#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import threading
from typing import Optional
from openjiuwen.core.common.exception.exception import JiuWenBaseException

_thread_log_instance = threading.local()


def set_thread_session(trace_id: str = "default_trace_id") -> None:
    _thread_log_instance.trace_id = trace_id


def get_thread_session() -> Optional[str]:
    return getattr(_thread_log_instance, 'trace_id', '')


def get_log_max_bytes(max_bytes_config) -> int:
    try:
        max_bytes = int(max_bytes_config)
    except ValueError as e:
        raise JiuWenBaseException(
            error_code=-1, message="-1"
        ) from e

    default_log_max_bytes = 100 * 1024 * 1024
    if max_bytes <= 0 or max_bytes > default_log_max_bytes:
        max_bytes = default_log_max_bytes

    return max_bytes 
