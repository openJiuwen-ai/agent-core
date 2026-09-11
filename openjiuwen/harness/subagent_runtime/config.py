# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime configuration defaults and wait timeout bounds."""

from __future__ import annotations

from dataclasses import dataclass

# One turn of web research / coding routinely exceeds 10 minutes when several
# subagents fetch in parallel. Bumped from 1800.0: a real reporting-module run
# (write 7 sections + ts-review's 3-round/3-lens adversarial pass, each round
# spawning 3 isolated paper-reviewer subagent turns) measured ~1694s of
# inherent model/tool work before ever reaching ts-latex, leaving under two
# minutes of slack against the old 1800s default — the session was killed
# mid-review on a live run. 40 minutes leaves real margin for a review round
# plus the ts-latex compile/repair loop that follows it. Wait max remains 1
# hour.
TURN_TIMEOUT_S_DEFAULT = 2400.0


@dataclass(frozen=True)
class SubagentRuntimeConfig:
    """Tunable runtime limits for subagent instances."""

    max_subagents: int = 10
    max_concurrent_running: int = 5
    turn_timeout_s: float = TURN_TIMEOUT_S_DEFAULT
    enable_lru_eviction: bool = True
    enable_activity_stream: bool = True
    activity_queue_size: int = 256
    activity_text_max_len: int = 2000
    activity_throttle_ms: float = 500.0
    enable_transcript_stream: bool = True


# Align with turn_timeout_s: parent wait should cover a full subagent turn.
WAIT_TIMEOUT_MS_DEFAULT = int(TURN_TIMEOUT_S_DEFAULT * 1000)
WAIT_TIMEOUT_MS_MIN = 10_000
WAIT_TIMEOUT_MS_MAX = 3_600_000
