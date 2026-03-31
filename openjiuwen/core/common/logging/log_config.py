# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Logging config snapshot utilities for spawned child processes.

Provides functions to capture the current logging configuration as a
serializable dict and to restore it in a new process.
"""

from typing import Any


def get_log_config_snapshot() -> dict[str, Any]:
    """Return a JSON-serializable snapshot of the current logging config."""
    from openjiuwen.core.common.logging.default.log_config import log_config

    return log_config.get_snapshot()


def configure_log_config(snapshot: dict[str, Any]) -> None:
    """Apply a previously captured logging config snapshot.

    Args:
        snapshot: Dict produced by get_log_config_snapshot.
    """
    from openjiuwen.core.common.logging.default.log_config import log_config

    log_config.apply_snapshot(snapshot)
