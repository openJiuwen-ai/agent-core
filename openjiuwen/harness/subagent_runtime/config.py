# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime configuration defaults and wait timeout bounds."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubagentRuntimeConfig:
    """Tunable runtime limits for subagent instances."""

    max_subagents: int = 10
    max_concurrent_running: int = 5
    turn_timeout_s: float = 600.0
    enable_lru_eviction: bool = True
    enable_activity_stream: bool = True
    activity_queue_size: int = 256
    activity_text_max_len: int = 200
    activity_throttle_ms: float = 500.0


# Align with turn_timeout_s (600s): parent wait should cover a full subagent turn.
WAIT_TIMEOUT_MS_DEFAULT = 600_000
WAIT_TIMEOUT_MS_MIN = 10_000
WAIT_TIMEOUT_MS_MAX = 3_600_000
