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
_BROWSER_TIMELINE_LOGGER_NAME = "openjiuwen.browser_agent.timeline"
_BROWSER_HANDLER_MARKER = "_openjiuwen_browser_agent_file_handler"
_BROWSER_TIMELINE_HANDLER_MARKER = "_openjiuwen_browser_agent_timeline_file_handler"
_BROWSER_LOG_ANNOUNCED_MARKER = "_openjiuwen_browser_agent_file_announced"
_BROWSER_TIMELINE_LOG_ANNOUNCED_MARKER = "_openjiuwen_browser_agent_timeline_file_announced"
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


def _get_timeline_level() -> int:
    level_name = os.getenv("OPENJIUWEN_BROWSER_AGENT_TIMELINE_LOG_LEVEL", "INFO")
    return getattr(logging, level_name.strip().upper(), logging.INFO)


def _default_log_path() -> Path:
    return Path.cwd() / "logs" / "browser_agent.log"


def _default_timeline_log_path() -> Path:
    debug_path = get_browser_agent_log_path()
    if debug_path is not None:
        return debug_path.with_name("browser_agent.timeline.log")
    return Path.cwd() / "logs" / "browser_agent.timeline.log"


def _configured_path(env_name: str, default_path: Path) -> Path | None:
    configured = os.getenv(env_name)
    if configured is None:
        return default_path.resolve()

    configured = configured.strip()
    if configured.lower() in _DISABLE_VALUES:
        return None

    return Path(configured).expanduser().resolve()


def get_browser_agent_log_path() -> Path | None:
    """Return the configured verbose browser-agent debug log path."""
    return _configured_path(
        "OPENJIUWEN_BROWSER_AGENT_LOG_FILE",
        _default_log_path(),
    )


def get_browser_agent_timeline_log_path() -> Path | None:
    """Return the configured human-readable browser-agent timeline path.

    The timeline log is intentionally compact: one line per important model,
    tool, observation, fallback, and task-summary event. Set
    OPENJIUWEN_BROWSER_AGENT_TIMELINE_LOG_FILE to 0/false/no/off/none/null/- to
    disable it.
    """
    return _configured_path(
        "OPENJIUWEN_BROWSER_AGENT_TIMELINE_LOG_FILE",
        _default_timeline_log_path(),
    )


def _add_file_handler(
    *,
    logger: logging.Logger,
    log_path: Path,
    marker: str,
    level: int,
    formatter: logging.Formatter,
) -> bool:
    for handler in logger.handlers:
        if getattr(handler, marker, False):
            if getattr(handler, "baseFilename", None) == str(log_path):
                return False

    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        log_path,
        mode="w",
        encoding="utf-8",
    )
    setattr(file_handler, marker, True)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return True


def get_browser_agent_logger() -> logging.Logger:
    """Return the verbose browser-agent debug logger.

    By default, browser-agent debug logs are written to ./logs/browser_agent.log
    in UTF-8 and are not propagated to the combined application log. Override
    the target path with OPENJIUWEN_BROWSER_AGENT_LOG_FILE. Set
    OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON=1 to also mirror browser logs to
    the normal combined logger.
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
        _add_file_handler(
            logger=browser_logger,
            log_path=log_path,
            marker=_BROWSER_HANDLER_MARKER,
            level=_get_level(),
            formatter=logging.Formatter(
                "%(asctime)s | browser_agent | %(levelname)s | %(message)s"
            ),
        )

        if not getattr(browser_logger, _BROWSER_LOG_ANNOUNCED_MARKER, False):
            common_logger.info(
                "[BROWSER_AGENT_LOG] dedicated browser debug log file enabled: %s",
                str(log_path),
            )
            setattr(browser_logger, _BROWSER_LOG_ANNOUNCED_MARKER, True)

    return browser_logger


def get_browser_agent_timeline_logger() -> logging.Logger:
    """Return the compact human-readable browser-agent timeline logger."""
    timeline_logger = logging.getLogger(_BROWSER_TIMELINE_LOGGER_NAME)
    timeline_logger.setLevel(_get_timeline_level())
    timeline_logger.propagate = False

    log_path = get_browser_agent_timeline_log_path()
    if log_path is None:
        return timeline_logger

    with _LOCK:
        _add_file_handler(
            logger=timeline_logger,
            log_path=log_path,
            marker=_BROWSER_TIMELINE_HANDLER_MARKER,
            level=_get_timeline_level(),
            formatter=logging.Formatter(
                "%(asctime)s | browser_timeline | %(levelname)s | %(message)s"
            ),
        )

        if not getattr(timeline_logger, _BROWSER_TIMELINE_LOG_ANNOUNCED_MARKER, False):
            common_logger.info(
                "[BROWSER_AGENT_LOG] dedicated browser timeline log file enabled: %s",
                str(log_path),
            )
            setattr(timeline_logger, _BROWSER_TIMELINE_LOG_ANNOUNCED_MARKER, True)

    return timeline_logger


def browser_agent_log_debug(message: str, *args: Any) -> None:
    browser_logger = get_browser_agent_logger()
    browser_logger.debug(message, *args)


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


def browser_agent_timeline_info(message: str, *args: Any) -> None:
    timeline_logger = get_browser_agent_timeline_logger()
    timeline_logger.info(message, *args)


def browser_agent_timeline_warning(message: str, *args: Any) -> None:
    timeline_logger = get_browser_agent_timeline_logger()
    timeline_logger.warning(message, *args)


def browser_agent_timeline_error(message: str, *args: Any) -> None:
    timeline_logger = get_browser_agent_timeline_logger()
    timeline_logger.error(message, *args)
