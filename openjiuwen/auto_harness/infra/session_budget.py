# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""会话预算控制器 — 时间与 API 成本管理。"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_WARN_THRESHOLD = 0.8  # 80% 预警阈值


class SessionBudgetController:
    """管理单次会话的时钟预算与 API 成本预算。

    Args:
        wall_clock_secs: 会话最大时长（秒）。
        cost_limit_usd: API 成本上限（美元）。
        task_timeout_secs: 单任务默认超时（秒）。
    """

    def __init__(
        self,
        wall_clock_secs: float = 3600.0,
        cost_limit_usd: float = 10.0,
        task_timeout_secs: float = 1200.0,
    ) -> None:
        self._wall_clock_secs = wall_clock_secs
        self._cost_limit_usd = cost_limit_usd
        self._task_timeout_secs = task_timeout_secs
        self._start: float | None = None
        self._cost_usd: float = 0.0
        self._time_warned = False
        self._cost_warned = False

    # ------ lifecycle ------

    def start(self) -> None:
        """记录会话起始时间。"""
        self._start = time.monotonic()
        logger.info(
            "Session budget started: %ss wall-clock, $%.2f cost",
            self._wall_clock_secs,
            self._cost_limit_usd,
        )

    # ------ cost tracking ------

    def add_cost(self, amount_usd: float) -> None:
        """累加 API 成本并在达到阈值时发出警告。"""
        self._cost_usd += amount_usd
        ratio = self._cost_usd / self._cost_limit_usd
        if ratio >= _WARN_THRESHOLD and not self._cost_warned:
            self._cost_warned = True
            logger.warning(
                "Cost budget %.0f%% used ($%.4f / $%.2f)",
                ratio * 100,
                self._cost_usd,
                self._cost_limit_usd,
            )

    # ------ properties ------

    @property
    def elapsed_secs(self) -> float:
        """已用时长（秒）。"""
        if self._start is None:
            return 0.0
        return time.monotonic() - self._start

    @property
    def remaining_secs(self) -> float:
        """剩余时钟预算（秒）。"""
        return max(0.0, self._wall_clock_secs - self.elapsed_secs)

    @property
    def remaining_cost_usd(self) -> float:
        """剩余成本预算（美元）。"""
        return max(0.0, self._cost_limit_usd - self._cost_usd)

    @property
    def should_stop(self) -> bool:
        """时钟或成本预算耗尽时返回 True。"""
        if self._start is not None:
            elapsed = self.elapsed_secs
            ratio = elapsed / self._wall_clock_secs
            if ratio >= _WARN_THRESHOLD and not self._time_warned:
                self._time_warned = True
                logger.warning(
                    "Wall-clock budget %.0f%% used (%.0fs / %.0fs)",
                    ratio * 100,
                    elapsed,
                    self._wall_clock_secs,
                )
            if elapsed >= self._wall_clock_secs:
                return True
        return self._cost_usd >= self._cost_limit_usd

    # ------ task-level check ------

    def check_task_budget(
        self, task_timeout_secs: float | None = None,
    ) -> bool:
        """检查是否有足够时间启动下一个任务。

        Args:
            task_timeout_secs: 任务超时，默认使用构造参数。

        Returns:
            True 表示预算充足，可以启动任务。
        """
        timeout = task_timeout_secs or self._task_timeout_secs
        return self.remaining_secs >= timeout
