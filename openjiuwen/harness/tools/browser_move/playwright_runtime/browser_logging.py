# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from openjiuwen.core.common.logging import logger as common_logger
from openjiuwen.core.common.logging.dated_file_handler import (
    DatedDailyFileHandler,
    format_log_date,
)
from openjiuwen.core.common.logging.default.default_impl import (
    SafeRotatingFileHandler,
)


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


def _unified_log_dir() -> Optional[Path]:
    """Return the desktop-injected unified core log dir, if any.

    Reads JIUWENSWARM_CORE_LOG_DIR (agent-core unified core log dir) first,
    then JIUWENSWARM_LOG_DIR (legacy unified log dir).
    """
    env_log_dir = os.getenv("JIUWENSWARM_CORE_LOG_DIR", "").strip() or os.getenv("JIUWENSWARM_LOG_DIR", "").strip()
    if env_log_dir:
        return Path(env_log_dir).expanduser()
    return None


def _outer_date_layout(unified_dir: Path) -> Optional[tuple[Path, str]]:
    """Resolve the outer-date layout (desktop-injected), if active.

    With ``JIUWENSWARM_LOG_DATE_ROOT`` set (``<dataDir>/logs``) and the
    injected dir living under it, date directories sit at the root next to
    the desktop's own daily logs; the injected dir's relative path is
    re-created inside each date directory:

        <DATE_ROOT>/<YYYY-MM-DD>/jiuwenswarm/<uidKey>/core

    Args:
        unified_dir: Desktop-injected unified core log dir.

    Returns:
        ``(date_root, injected-relative-path)``; None when not active.
    """
    env_date_root = os.getenv("JIUWENSWARM_LOG_DATE_ROOT", "").strip()
    if not env_date_root:
        return None
    date_root = Path(env_date_root).expanduser()
    try:
        rel = unified_dir.relative_to(date_root)
    except ValueError:
        return None
    rel_str = rel.as_posix().strip("/")
    if not rel_str:
        return None
    return date_root, rel_str


def _dated_core_dir(unified_dir: Path) -> Path:
    """Return the core dir inside today's date directory.

    Outer-date layout (``JIUWENSWARM_LOG_DATE_ROOT`` injected):
    ``<DATE_ROOT>/<YYYY-MM-DD>/jiuwenswarm/<uidKey>/core``.

    Otherwise the injected dir is the user-level ``core`` subdir
    (``logs/jiuwenswarm/<uidKey>/core``); the date directory sits at the
    user level next to the jiuwenswarm logs, with ``core`` inside it:
    ``logs/jiuwenswarm/<uidKey>/<YYYY-MM-DD>/core``.

    Args:
        unified_dir: Desktop-injected unified core log dir.

    Returns:
        Today's core directory path (under the date dir).
    """
    outer = _outer_date_layout(unified_dir)
    if outer is not None:
        date_root, rel = outer
        return date_root / format_log_date() / rel
    env_core = os.getenv("JIUWENSWARM_CORE_LOG_DIR", "").strip()
    if env_core and unified_dir.name == "core":
        return unified_dir.parent / format_log_date() / "core"
    # JIUWENSWARM_LOG_DIR 注入形态（用户根本身）：core 按日期进子目录。
    return unified_dir / format_log_date() / "core"


def get_browser_agent_log_path() -> Optional[Path]:
    """Return the configured browser-agent log path.

    Browser-agent file logging is enabled by default so users running
    jiuwenswarm-start get a separate browser log without extra environment
    setup. When the desktop injects the unified log dir
    (JIUWENSWARM_CORE_LOG_DIR / JIUWENSWARM_LOG_DIR), the file is written
    under the per-day date directory next to the jiuwenswarm logs
    (<userRoot>/<YYYY-MM-DD>/core/browser_agent.log), or under the outer
    date root when JIUWENSWARM_LOG_DATE_ROOT is injected
    (<dataRoot>/logs/<YYYY-MM-DD>/jiuwenswarm/<uidKey>/core/browser_agent.log),
    mirroring the desktop logger layout. Without injection the file stays
    flat at ./logs/browser_agent.log. Set OPENJIUWEN_BROWSER_AGENT_LOG_FILE
    to one of 0/false/no/off/none/null/- to disable the dedicated file; any
    other value pins an exact (flat) file path.

    Returns:
        The resolved log path, or None when the dedicated file is disabled.
    """
    configured = os.getenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE")
    if configured is not None:
        configured = configured.strip()
        if configured.lower() in _DISABLE_VALUES:
            return None
        return Path(configured).expanduser().resolve()

    unified_dir = _unified_log_dir()
    if unified_dir is not None:
        return (_dated_core_dir(unified_dir) / "browser_agent.log").resolve()
    return (Path.cwd() / "logs" / "browser_agent.log").resolve()


def _dated_handler_base(unified_dir: Path) -> Path:
    """Return the root the dated handler creates date dirs under.

    Args:
        unified_dir: Desktop-injected unified core log dir.

    Returns:
        The handler's base_dir (the outer date root when injected, else the
        parent of ``core`` when injected as such).
    """
    outer = _outer_date_layout(unified_dir)
    if outer is not None:
        return outer[0]
    if unified_dir.name == "core":
        return unified_dir.parent
    return unified_dir


def _get_browser_agent_dated_handler(
    unified_dir: Path,
) -> Optional[DatedDailyFileHandler]:
    """Build the per-day handler for the unified log dir.

    Args:
        unified_dir: Desktop-injected unified core log dir (user-level
            ``core`` subdir; the date dir is created at its parent, or at
            the outer date root when the desktop injects it).

    Returns:
        A configured handler, or None when construction fails.
    """
    outer = _outer_date_layout(unified_dir)
    try:
        if outer is not None:
            date_root, rel = outer
            handler = DatedDailyFileHandler(
                base_dir=date_root,
                filename=f"{rel}/browser_agent.log",
            )
        elif unified_dir.name == "core":
            handler = DatedDailyFileHandler(
                base_dir=unified_dir.parent,
                filename="core/browser_agent.log",
            )
        else:
            handler = DatedDailyFileHandler(
                base_dir=unified_dir,
                filename="core/browser_agent.log",
            )
    except OSError:
        return None
    setattr(handler, _BROWSER_HANDLER_MARKER, True)
    handler.setLevel(_get_level())
    handler.setFormatter(logging.Formatter("%(asctime)s | browser_agent | %(levelname)s | %(message)s"))
    return handler


def _get_browser_agent_flat_handler(
    log_path: Path,
) -> SafeRotatingFileHandler:
    """Build the legacy flat handler for an explicitly pinned path.

    Args:
        log_path: Exact file path to write.

    Returns:
        A configured append-mode file handler.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = SafeRotatingFileHandler(
        filename=str(log_path),
        maxBytes=0,
        backupCount=0,
        encoding="utf-8",
    )
    setattr(file_handler, _BROWSER_HANDLER_MARKER, True)
    file_handler.setLevel(_get_level())
    file_handler.setFormatter(logging.Formatter("%(asctime)s | browser_agent | %(levelname)s | %(message)s"))
    return file_handler


def get_browser_agent_logger() -> logging.Logger:
    """Return the dedicated browser-agent logger.

    By default, browser-agent logs are written in UTF-8 and are not
    propagated to the combined application log. When the desktop injects
    JIUWENSWARM_CORE_LOG_DIR (agent-core unified core log dir) or
    JIUWENSWARM_LOG_DIR (legacy unified log dir), the file is written
    under the per-day date directory next to the jiuwenswarm logs
    (<userRoot>/<YYYY-MM-DD>/core/browser_agent.log), mirroring the
    desktop logger layout. Without injection the default path is the flat
    ./logs/browser_agent.log. Override the target path with
    OPENJIUWEN_BROWSER_AGENT_LOG_FILE (flat, append mode). Set
    OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON=1 to also mirror browser
    logs to the normal combined logger.
    """
    browser_logger = logging.getLogger(_BROWSER_LOGGER_NAME)
    browser_logger.setLevel(_get_level())

    mirror_common = _env_bool(
        "OPENJIUWEN_BROWSER_AGENT_LOG_MIRROR_COMMON",
        default=False,
    )
    browser_logger.propagate = mirror_common

    unified_dir = _unified_log_dir()
    configured = (os.getenv("OPENJIUWEN_BROWSER_AGENT_LOG_FILE") or "").strip()
    if configured.lower() in _DISABLE_VALUES:
        # Disabled entirely: no file handler, whatever the unified dir.
        return browser_logger
    if unified_dir is None or configured:
        log_path = get_browser_agent_log_path()
        if log_path is None:
            return browser_logger
        with _LOCK:
            for handler in browser_logger.handlers:
                if getattr(handler, _BROWSER_HANDLER_MARKER, False):
                    if getattr(handler, "baseFilename", None) == str(log_path):
                        return browser_logger
            try:
                file_handler: logging.Handler = _get_browser_agent_flat_handler(log_path)
            except Exception:  # noqa: BLE001 - logging must never block
                return browser_logger
            browser_logger.addHandler(file_handler)
            _announce_browser_log(browser_logger, str(log_path))
        return browser_logger

    with _LOCK:
        for handler in browser_logger.handlers:
            if getattr(handler, _BROWSER_HANDLER_MARKER, False):
                if getattr(handler, "base_dir", None) == str(
                    _dated_handler_base(unified_dir)
                ):
                    return browser_logger

        dated_handler = _get_browser_agent_dated_handler(unified_dir)
        if dated_handler is None:
            return browser_logger
        browser_logger.addHandler(dated_handler)
        _announce_browser_log(
            browser_logger,
            str(_dated_core_dir(unified_dir) / "browser_agent.log"),
        )

    return browser_logger


def _announce_browser_log(browser_logger: logging.Logger, target: str) -> None:
    """Announce the dedicated log file once via the common logger.

    Args:
        browser_logger: The browser-agent logger being configured.
        target: Human-readable description of the log target.
    """
    if getattr(browser_logger, _BROWSER_LOG_ANNOUNCED_MARKER, False):
        return
    common_logger.info(
        "[BROWSER_AGENT_LOG] dedicated browser log file enabled: %s",
        target,
    )
    setattr(browser_logger, _BROWSER_LOG_ANNOUNCED_MARKER, True)


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
