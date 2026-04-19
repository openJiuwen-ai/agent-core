# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""两阶段 CI 修复循环控制器（IMMUTABLE）。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FixLoopResult:
    """修复循环执行结果。"""

    success: bool = False
    attempts: int = 0
    phase: int = 1
    error_log: List[str] = field(default_factory=list)


class FixLoopController:
    """两阶段 CI 修复循环。

    Phase 1 — 直接修复：运行 CI → 解析错误 → agent 修复 → 重试。
    Phase 2 — 评审修复：evaluator 审查质量，不通过则继续修复。

    Args:
        phase1_max_retries: Phase 1 最大重试次数。
        phase2_max_retries: Phase 2 最大重试次数。
        timeout_per_attempt: 每次尝试的超时（秒）。
    """

    def __init__(
        self,
        phase1_max_retries: int = 10,
        phase2_max_retries: int = 9,
        timeout_per_attempt: float = 600.0,
    ) -> None:
        self._p1_max = phase1_max_retries
        self._p2_max = phase2_max_retries
        self._timeout = timeout_per_attempt

    async def run(
        self,
        ci_runner: Callable[[], Coroutine[Any, Any, Any]],
        agent_fixer: Callable[[str], Coroutine[Any, Any, Any]],
        evaluator: Optional[
            Callable[[], Coroutine[Any, Any, Any]]
        ] = None,
    ) -> FixLoopResult:
        """执行两阶段修复循环。

        Args:
            ci_runner: 运行 CI，返回含 .passed 和 .errors 的对象。
            agent_fixer: 接收错误信息并尝试修复。
            evaluator: 可选，审查修复质量，返回含 .approved 的对象。

        Returns:
            FixLoopResult 包含成功状态、尝试次数和错误日志。
        """
        result = FixLoopResult()

        # ---- Phase 1: 直接修复 ----
        result.phase = 1
        for i in range(1, self._p1_max + 1):
            result.attempts = i
            logger.info("Phase 1 attempt %d/%d", i, self._p1_max)
            try:
                ci = await asyncio.wait_for(
                    ci_runner(), timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                msg = f"Phase 1 attempt {i}: CI timeout"
                logger.warning(msg)
                result.error_log.append(msg)
                continue

            if ci.passed:
                result.success = True
                logger.info("CI passed on phase 1 attempt %d", i)
                return result

            errors = ci.errors or "unknown error"
            result.error_log.append(
                f"Phase 1 attempt {i}: {errors[:200]}"
            )
            try:
                await asyncio.wait_for(
                    agent_fixer(errors), timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                msg = f"Phase 1 attempt {i}: fixer timeout"
                logger.warning(msg)
                result.error_log.append(msg)

        # ---- Phase 2: 评审修复 ----
        if evaluator is None:
            return result

        result.phase = 2
        for j in range(1, self._p2_max + 1):
            result.attempts += 1
            logger.info("Phase 2 attempt %d/%d", j, self._p2_max)
            try:
                review = await asyncio.wait_for(
                    evaluator(), timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                msg = f"Phase 2 attempt {j}: evaluator timeout"
                logger.warning(msg)
                result.error_log.append(msg)
                continue

            if review.approved:
                result.success = True
                logger.info(
                    "Evaluator approved on phase 2 attempt %d", j,
                )
                return result

            result.error_log.append(
                f"Phase 2 attempt {j}: evaluator rejected"
            )
            try:
                await asyncio.wait_for(
                    agent_fixer("evaluator rejected"),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                msg = f"Phase 2 attempt {j}: fixer timeout"
                logger.warning(msg)
                result.error_log.append(msg)

        return result
