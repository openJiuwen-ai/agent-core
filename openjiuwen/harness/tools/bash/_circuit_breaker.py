# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Command-level circuit breaker for BashTool.

When the *same* command string fails consecutively N times, the (N+1)th
invocation is short-circuited with a strong error message, forcing the agent
to modify the command or switch strategy.

The failure counter is keyed by a normalised version of the command (whitespace
collapsed, leading/trailing stripped) so that trivial formatting differences do
not bypass the breaker.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from threading import Lock

from openjiuwen.harness.tools.base_tool import ToolOutput


def _normalise(command: str) -> str:
    """Collapse whitespace so minor formatting diffs don't bypass the breaker."""
    return re.sub(r"\s+", " ", command.strip())


# ── config ─────────────────────────────────────────────────────

_DEFAULT_MAX_CONSECUTIVE_FAILURES = 3


def _read_max_failures() -> int:
    return _DEFAULT_MAX_CONSECUTIVE_FAILURES


# ── circuit breaker ────────────────────────────────────────────

@dataclass
class _CommandRecord:
    """Tracks consecutive failures for a single normalised command."""

    count: int = 0
    last_error: str = ""


@dataclass
class CommandCircuitBreaker:
    """Thread-safe command-level circuit breaker.

    * ``check`` is called **before** execution — it returns a ``ToolOutput``
      with ``success=False`` when the command has already hit the threshold.
    * ``record_failure`` is called **after** a failed execution.
    * ``record_success`` resets the counter for that command.
    """

    max_failures: int = field(default_factory=_read_max_failures)
    _records: dict[str, _CommandRecord] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    # ── public API ─────────────────────────────────────────────

    def check(self, command: str) -> ToolOutput | None:
        """Return a blocking ToolOutput if the command is tripped, else None."""
        key = _normalise(command)
        with self._lock:
            rec = self._records.get(key)
            if rec is None or rec.count < self.max_failures:
                return None
            return ToolOutput(
                success=False,
                error=(
                    f"[命令熔断] 此命令已连续失败 {rec.count} 次（阈值 {self.max_failures}），"
                    f"拒绝再次执行。请修改命令内容、更换路径或使用其他工具/策略。\n"
                    f"最近一次错误: {rec.last_error}"
                ),
            )

    def record_failure(self, command: str, error: str) -> None:
        """Increment the consecutive-failure counter for *command*."""
        key = _normalise(command)
        with self._lock:
            rec = self._records.get(key)
            if rec is None:
                rec = _CommandRecord()
                self._records[key] = rec
            rec.count += 1
            rec.last_error = (error or "")[:500]

    def record_success(self, command: str) -> None:
        """Reset the consecutive-failure counter for *command*."""
        key = _normalise(command)
        with self._lock:
            self._records.pop(key, None)


# ── module-level singleton ─────────────────────────────────────

_breaker: CommandCircuitBreaker | None = None


def get_breaker() -> CommandCircuitBreaker:
    """Return the process-wide circuit-breaker singleton."""
    global _breaker
    if _breaker is None:
        _breaker = CommandCircuitBreaker()
    return _breaker
