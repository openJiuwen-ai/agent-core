# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any

from openjiuwen.core.common.logging import logger as common_logger
from openjiuwen.core.common.logging.browser_context import is_browser_agent_log_context


_BROWSER_LOGGER_NAME = "openjiuwen.browser_agent"
_BROWSER_HANDLER_MARKER = "_openjiuwen_browser_agent_file_handler"
_BROWSER_LOG_ANNOUNCED_MARKER = "_openjiuwen_browser_agent_file_announced"
_FALSE_VALUES = {"0", "false", "no", "off", ""}
_DISABLE_VALUES = {"0", "false", "no", "off", "none", "null", "-"}
_LOCK = Lock()
_AUDIT_LOCK = Lock()


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


def write_browser_agent_audit_artifact(kind: str, raw: Any) -> dict[str, Any]:
    """Persist an opted-in, content-addressed raw browser observation."""
    if isinstance(raw, str):
        raw_text = raw
    else:
        raw_text = json.dumps(raw, ensure_ascii=False, default=str)
    raw_bytes = raw_text.encode("utf-8", "ignore")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    audit = {
        "raw_size_bytes": len(raw_bytes),
        "raw_sha256": digest[:16],
        "stored": False,
    }
    if not is_browser_agent_log_context() or not _env_bool(
        "OPENJIUWEN_BROWSER_AGENT_AUDIT_RAW",
        default=False,
    ):
        return audit

    log_path = get_browser_agent_log_path()
    if log_path is None:
        return audit
    safe_kind = re.sub(r"[^a-z0-9_-]+", "_", str(kind or "observation").lower()).strip("_")
    safe_kind = safe_kind or "observation"
    artifact_name = f"{safe_kind}_{digest[:16]}.json.gz"
    artifact_path = log_path.parent / "browser_agent_audit" / artifact_name
    try:
        with _AUDIT_LOCK:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            if not artifact_path.exists():
                with gzip.open(artifact_path, mode="wt", encoding="utf-8") as stream:
                    json.dump(
                        {"kind": safe_kind, "sha256": digest, "raw": raw_text},
                        stream,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
        audit["stored"] = True
        get_browser_agent_logger().info(
            "[BROWSER_AUDIT] kind=%s sha256=%s size=%s artifact=%s",
            safe_kind,
            digest[:16],
            len(raw_bytes),
            artifact_name,
        )
    except OSError as exc:
        get_browser_agent_logger().warning(
            "[BROWSER_AUDIT_ERROR] kind=%s sha256=%s error=%s",
            safe_kind,
            digest[:16],
            exc,
        )
    return audit
