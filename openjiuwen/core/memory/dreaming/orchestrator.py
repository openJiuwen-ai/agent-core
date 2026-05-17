# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Dreaming Orchestrator: Periodic Scheduling + Busy Backoff"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class DreamingOrchestrator:
    """Idle-aware periodic dreaming service.

    Lifecycle:
        await start()  → Start background asyncio task
        await stop()   → Cancel task

    Scheduling Policy:
        1. Busy Backoff: busy_checker() returns True → Delay next next cycle
        2. Change Trigger: sweep_fn() checks if sweep has new data

    Idempotent: Repeated start()/stop() is safe.
    """

    def __init__(
        self,
        sweep_fn: Callable[[], Awaitable[None]],
        interval_seconds: float,
        busy_checker: Callable[[], bool] | None = None,
        name: str = "dreaming",
    ) -> None:
        self._sweep_fn = sweep_fn
        self._interval = max(60.0, interval_seconds)
        self._busy_checker = busy_checker
        self._name = name
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def health(self) -> dict[str, Any]:
        return {"running": self._running, "interval_seconds": self._interval}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=f"{self._name}-loop")
        logger.info("[%s] Orchestrator started, interval %.0fs", self._name, self._interval)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.error("[%s] loop task cancelled during stop", self._name)
        self._task = None
        logger.info("[%s] Orchestrator stopped", self._name)

    async def _loop(self) -> None:
        try:
            await asyncio.sleep(120.0)  # Initial delay, wait for process to settle
            while self._running:
                await self._tick()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            logger.exception("[%s] loop cancelled", self._name)
            raise
        except Exception:
            logger.exception(
                "[%s] loop terminated by unexpected error (running=%s, interval=%.0fs)",
                self._name, self._running, self._interval,
            )
            self._running = False

    async def _tick(self) -> None:
        try:
            # Busy Backoff
            if self._busy_checker is not None:
                try:
                    if self._busy_checker():
                        logger.info("[%s] agent busy, delay sweep", self._name)
                        return
                except Exception:
                    logger.warning("[%s] busy_checker raised exception, skipping check", self._name, exc_info=True)

            logger.info("[%s] start sweep", self._name)
            await self._sweep_fn()
            logger.info("[%s] sweep completed", self._name)
        except asyncio.CancelledError:
            logger.exception("[%s] sweep cancelled", self._name)
            raise
        except Exception as exc:
            logger.exception("[%s] sweep exception: %s", self._name, exc)
