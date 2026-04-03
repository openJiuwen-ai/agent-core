# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Default Logging Implementation Module

Provides default logging implementation components, including:
- DefaultLogger: Default logger implementation
- SafeRotatingFileHandler: Secure log file rotation handler
- ContextFilter: Context filter (adapted for async environments)
"""

from openjiuwen.core.common.logging.default.default_impl import (
    ContextFilter,
    DefaultLogger,
    SafeRotatingFileHandler,
)
from openjiuwen.core.common.logging.utils import (
    get_session_id,
    set_session_id,
)

__all__ = [
    "DefaultLogger",
    "SafeRotatingFileHandler",
    "ContextFilter",
    "set_session_id",
    "get_session_id",
]
