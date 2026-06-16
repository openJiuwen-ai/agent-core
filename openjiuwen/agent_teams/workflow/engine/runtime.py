# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-run engine state shared by all injected primitives."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .backends.base import AgentBackend
from .journal import Journal
from .progress import ProgressSink, noop_progress_sink


def _noop_log(message: str) -> None:
    """Default ``log_sink``: drop diagnostics. Embedders inject a real logger.

    Replaces dw's ``print`` default — library code must not ``print`` (the team
    integration injects a ``team_logger``-backed sink).
    """
    return None


@dataclass
class Runtime:
    backend: AgentBackend
    journal: Journal
    args: Any = None

    # Behaviour knobs.
    log_sink: Callable[[str], None] = _noop_log
    """Plain-text diagnostics (lint warnings, backend failures, spawn-limit /
    concurrent-scope warnings). NOT the per-phase/agent progress feed — that is
    ``progress_sink``."""
    progress_sink: ProgressSink = noop_progress_sink
    """Structured per-phase / per-agent progress (``WorkflowProgressEvent``).
    Drives the leader spectator broadcast and the 4-layer ``WorkflowRun``."""
    retries: int = 2  # extra attempts after the first on backend/validation error
    strict: bool = False
    spawn_limit: int = 1000
    budget_total: int | None = None
    cap_override: int | None = None  # force the concurrency cap (tests)

    # Mutable run state (created/advanced inside the running loop).
    sem: asyncio.Semaphore | None = field(default=None, repr=False)
    spawn_count: int = 0
    tokens_spent: int = 0
    current_phase: str | None = None
    warned_concurrent_scope: bool = False  # one-shot guard for the raw-gather warning
    warned_concurrent_session: bool = False  # one-shot guard for overlapping session sends

    def make_cap(self) -> int:
        """Concurrent ``agent()`` calls allowed. Clamped to >= 1."""
        if self.cap_override is not None:
            return max(1, self.cap_override)
        return max(1, min(16, (os.cpu_count() or 4) - 2))
