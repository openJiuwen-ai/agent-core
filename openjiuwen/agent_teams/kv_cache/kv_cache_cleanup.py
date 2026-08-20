# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cancellation-safe terminal cleanup for KV-managed Team runtimes."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from openjiuwen.agent_teams.kv_cache.kv_cache_lifecycle import (
    KVCacheRuntimeBinding,
    cancellation_safe_best_effort_evict,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.kv_cache import KVC_TERMINAL_CLEANUP_TIMEOUT_SECONDS


async def cancellation_safe_evict_then_dispose(
    *,
    binding: KVCacheRuntimeBinding | None,
    dispose: Callable[[], Awaitable[None]],
    reason: str,
    owner_id: str,
    timeout: float = KVC_TERMINAL_CLEANUP_TIMEOUT_SECONDS,
) -> None:
    """Best-effort evict before dispose without masking caller cancellation."""
    cleanup_task = asyncio.create_task(
        _evict_then_dispose(
            binding=binding,
            dispose=dispose,
            reason=reason,
            owner_id=owner_id,
        )
    )
    try:
        await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=timeout)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=timeout)
        except asyncio.TimeoutError as exc:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task
            team_logger.warning("KVC cleanup after cancellation timed out for %s: %s", owner_id, exc)
        except Exception as exc:
            team_logger.warning("KVC cleanup after cancellation failed for %s: %s", owner_id, exc)
        raise
    except asyncio.TimeoutError as exc:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        team_logger.warning("KVC cleanup timed out for %s: %s", owner_id, exc)
    except Exception as exc:
        team_logger.warning("KVC cleanup failed for %s: %s", owner_id, exc)


async def cancellation_safe_evict(
    *,
    binding: KVCacheRuntimeBinding | None,
    reason: str,
    owner_id: str,
    timeout: float = KVC_TERMINAL_CLEANUP_TIMEOUT_SECONDS,
) -> bool:
    """Best-effort terminal evict without changing runtime ownership."""
    if binding is None:
        return False
    cleanup_task = asyncio.create_task(
        cancellation_safe_best_effort_evict(
            binding,
            reason=reason,
            worker_id=owner_id,
        )
    )
    try:
        return await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=timeout)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=timeout)
        except asyncio.TimeoutError as exc:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task
            team_logger.warning("KVC evict after cancellation timed out for %s: %s", owner_id, exc)
        except Exception as exc:
            team_logger.warning("KVC evict after cancellation failed for %s: %s", owner_id, exc)
        raise
    except asyncio.TimeoutError as exc:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        team_logger.warning("KVC evict timed out for %s: %s", owner_id, exc)
    except Exception as exc:
        team_logger.warning("KVC evict failed for %s: %s", owner_id, exc)
    return False


async def _evict_then_dispose(
    *,
    binding: KVCacheRuntimeBinding | None,
    dispose: Callable[[], Awaitable[None]],
    reason: str,
    owner_id: str,
) -> None:
    try:
        await cancellation_safe_best_effort_evict(
            binding,
            reason=reason,
            worker_id=owner_id,
        )
    except Exception as exc:
        team_logger.warning("KVC evict cleanup failed for %s: %s", owner_id, exc)
    try:
        await dispose()
    except Exception as exc:
        team_logger.warning("runtime dispose failed for %s: %s", owner_id, exc)


__all__ = ["cancellation_safe_evict", "cancellation_safe_evict_then_dispose"]
