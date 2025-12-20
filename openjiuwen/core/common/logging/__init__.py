#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from .protocol import LoggerProtocol
from .manager import LogManager
from .utils import set_thread_session, get_thread_session


_initialized = False


def _ensure_initialized():
    global _initialized
    if not _initialized:
        try:
            from openjiuwen.extensions.common.log.default_impl import DefaultLogger
            LogManager.set_default_logger_class(DefaultLogger)
        except ImportError:
            pass
        LogManager.initialize()
        _initialized = True


class LazyLogger:
    def __init__(self, getter_func):
        self._getter_func = getter_func
        self._logger = None

    def __getattr__(self, name):
        if self._logger is None:
            _ensure_initialized()
            self._logger = self._getter_func()
        return getattr(self._logger, name)


logger = LazyLogger(lambda: LogManager.get_logger("common"))

__all__ = [
    "LoggerProtocol",
    "LogManager",
    "set_thread_session",
    "get_thread_session",
    "logger",
]
