# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

DEFAULT_INNER_LOG_CONFIG = {
    "backend": "loguru",
    "defaults": {
        "level": "INFO",
        "enqueue": True,
        "catch": False,
        "backtrace": False,
        "diagnose": False,
    },
    "sinks": {
        "console": {
            "target": "stdout",
            "level": "INFO",
            "serialize": False,
            "colorize": True,
            "enqueue": False,
            "backtrace": True,
            "diagnose": False,
            "format": (
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<magenta>{process.id}</magenta> | "
                "<level>{level: <8}</level> | "
                "<cyan>{extra[log_type]}</cyan> | "
                "<yellow>{extra[trace_id]}</yellow> | "
                "<blue>{extra[short_path]}:{line}</blue> | "
                "<level>{message}</level>"
            ),
        },
        "app_json": {
            "target": "./logs/run/jiuwen.jsonl",
            "level": "INFO",
            "serialize": True,
            "enqueue": True,
            "rotation": "500 MB",
            "retention": "14 days",
            "compression": "gz",
            "encoding": "utf-8",
        },
        "perf_json": {
            "target": "./logs/performance/jiuwen_performance.jsonl",
            "level": "INFO",
            "serialize": True,
            "enqueue": True,
            "rotation": "200 MB",
            "retention": "7 days",
            "compression": "gz",
            "encoding": "utf-8",
        },
    },
    "routes": {
        "common": ["console", "app_json"],
        "interface": ["console", "app_json"],
        "prompt_builder": ["console", "app_json"],
        "performance": ["perf_json"],
        "*": ["console", "app_json"],
    },
    "loggers": {
        "common": {
            "level": "INFO",
        },
        "agent": {
            "level": "INFO",
        }
    },
}
