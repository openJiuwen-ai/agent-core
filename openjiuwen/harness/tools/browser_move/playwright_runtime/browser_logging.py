# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any

from openjiuwen.core.common.logging import logger as common_logger


_BROWSER_LOGGER_NAME = "openjiuwen.browser_agent"
_BROWSER_HANDLER_MARKER = "_openjiuwen_browser_agent_file_handler"
_BROWSER_LOG_ANNOUNCED_MARKER = "_openjiuwen_browser_agent_file_announced"
_FALSE_VALUES = {"0", "false", "no", "off", ""}
_DISABLE_VALUES = {"0", "false", "no", "off", "none", "null", "-"}
_LOCK = Lock()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in _FALSE_VALUES


def _get_level() -> int:
    level_name = os.getenv("OPENJIUWEN_BROWSER_AGENT_LOG_LEVEL", "INFO")
    return getattr(logging, level_name.strip().upper(), logging.INFO)


def _default_log_path() -> Path:
    return Path.cwd() / "logs" / "browser_agent.log"


def get_browser_agent_log_path() -> Path | None:
    """Return the configured browser-agent log path.

    Browser-agent file logging is enabled by default so users running
    jiuwenswarm-start get a separate browser log without extra environment
    setup. Set OPENJIUWEN_BROWSER_AGENT_LOG_FILE to one of
    0/false/no/off/none/null/- to disable the dedicated file.
    """
    configured = os.getenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE")
    if configured is None:
        return _default_log_path().resolve()

    configured = configured.strip()
    if configured.lower() in _DISABLE_VALUES:
        return None

    return Path(configured).expanduser().resolve()


def get_browser_agent_logger() -> logging.Logger:
    """Return the dedicated browser-agent logger.

    By default, browser-agent logs are written to ./logs/browser_agent.log in
    UTF-8 and are not propagated to the combined application log. Override the
    target path with OPENJIUWEN_BROWSER_AGENT_LOG_FILE. Set
    OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON=1 to also mirror browser logs
    to the normal combined logger.
    """
    browser_logger = logging.getLogger(_BROWSER_LOGGER_NAME)
    browser_logger.setLevel(_get_level())

    mirror_common = _env_bool(
        "OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON",
        default=False,
    )
    browser_logger.propagate = mirror_common

    log_path = get_browser_agent_log_path()
    if log_path is None:
        return browser_logger

    with _LOCK:
        for handler in browser_logger.handlers:
            if getattr(handler, _BROWSER_HANDLER_MARKER, False):
                if getattr(handler, "baseFilename", None) == str(log_path):
                    return browser_logger

        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(
            log_path,
            mode="w",
            encoding="utf-8",
        )
        setattr(file_handler, _BROWSER_HANDLER_MARKER, True)
        file_handler.setLevel(_get_level())
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | browser_agent | %(levelname)s | %(message)s"
            )
        )
        browser_logger.addHandler(file_handler)

        if not getattr(browser_logger, _BROWSER_LOG_ANNOUNCED_MARKER, False):
            common_logger.info(
                "[BROWSER_AGENT_LOG] dedicated browser log file enabled: %s",
                str(log_path),
            )
            setattr(browser_logger, _BROWSER_LOG_ANNOUNCED_MARKER, True)

    return browser_logger


def browser_agent_log_info(message: str, *args: Any) -> None:
    browser_logger = get_browser_agent_logger()
    browser_logger.info(message, *args)

    if get_browser_agent_log_path() is None:
        common_logger.info(message, *args)


def browser_agent_log_warning(message: str, *args: Any) -> None:
    browser_logger = get_browser_agent_logger()
    browser_logger.warning(message, *args)

    if get_browser_agent_log_path() is None:
        common_logger.warning(message, *args)


def browser_agent_log_error(message: str, *args: Any) -> None:
    browser_logger = get_browser_agent_logger()
    browser_logger.error(message, *args)

    if get_browser_agent_log_path() is None:
        common_logger.error(message, *args)
