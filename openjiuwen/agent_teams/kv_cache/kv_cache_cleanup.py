# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cancellation-safe cleanup around Session-owned KVC release."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.kv_cache.kv_cache_config import KVC_TERMINAL_CLEANUP_TIMEOUT_SECONDS


async def cancellation_safe_release_then_dispose(
    *,
    release_kvc: Callable[[], Awaitable[bool]] | None,
    dispose: Callable[[], Awaitable[None]],
    owner_id: str,
    timeout: float = KVC_TERMINAL_CLEANUP_TIMEOUT_SECONDS,
) -> None:
    """Finish best-effort KVC release and disposal without swallowing cancellation."""
    cleanup_task = asyncio.create_task(
        _release_then_dispose(release_kvc=release_kvc, dispose=dispose, owner_id=owner_id)
    )
    try:
        await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=timeout)
    except asyncio.CancelledError:
        await _finish_after_cancellation(cleanup_task, timeout, owner_id)
        raise
    except asyncio.TimeoutError as exc:
        await _cancel(cleanup_task)
        team_logger.warning("KVC cleanup timed out for %s: %s", owner_id, exc)
    except Exception as exc:
        team_logger.warning("KVC cleanup failed for %s: %s", owner_id, exc)


async def _finish_after_cancellation(
    task: asyncio.Task[None],
    timeout: float,
    owner_id: str,
) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _cancel(task)
        team_logger.warning(
            "KVC cleanup after cancellation timed out for %s: %s",
            owner_id,
            exc,
        )
    except Exception as exc:
        team_logger.warning(
            "KVC cleanup after cancellation failed for %s: %s",
            owner_id,
            exc,
        )


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _release_then_dispose(
    *,
    release_kvc: Callable[[], Awaitable[bool]] | None,
    dispose: Callable[[], Awaitable[None]],
    owner_id: str,
) -> None:
    if release_kvc is not None:
        try:
            await release_kvc()
        except Exception as exc:
            team_logger.warning("KVC release failed for %s: %s", owner_id, exc)
    try:
        await dispose()
    except Exception as exc:
        team_logger.warning("runtime dispose failed for %s: %s", owner_id, exc)


__all__ = ["cancellation_safe_release_then_dispose"]
