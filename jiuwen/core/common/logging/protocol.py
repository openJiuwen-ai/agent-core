#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Protocol, runtime_checkable, Dict, Any, Optional
import logging


@runtime_checkable
class LoggerProtocol(Protocol):
    """日志记录器协议，定义所有日志实现必须提供的方法"""

    def debug(self, msg: str, *args, **kwargs) -> None:
        """记录调试级别日志"""
        ...

    def info(self, msg: str, *args, **kwargs) -> None:
        """记录信息级别日志"""
        ...

    def warning(self, msg: str, *args, **kwargs) -> None:
        """记录警告级别日志"""
        ...

    def error(self, msg: str, *args, **kwargs) -> None:
        """记录错误级别日志"""
        ...

    def critical(self, msg: str, *args, **kwargs) -> None:
        """记录严重级别日志"""
        ...

    def exception(self, msg: str, *args, **kwargs) -> None:
        """记录异常信息（包含堆栈跟踪）"""
        ...

    def log(self, level: int, msg: str, *args, **kwargs) -> None:
        """通用日志记录方法"""
        ...

    def setLevel(self, level: int) -> None:
        """设置日志级别"""
        ...

    def addHandler(self, handler: logging.Handler) -> None:
        """添加日志处理器"""
        ...

    def removeHandler(self, handler: logging.Handler) -> None:
        """移除日志处理器"""
        ...

    def addFilter(self, filter) -> None:
        """添加过滤器"""
        ...

    def removeFilter(self, filter) -> None:
        """移除过滤器"""
        ...

    def get_config(self) -> Dict[str, Any]:
        """获取日志配置"""
        ...

    def reconfigure(self, config: Dict[str, Any]) -> None:
        """重新配置日志记录器"""
        ... 