# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Event-loop-aware lifecycle helpers for process-wide shared async LLM clients."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncGenerator, Callable
from typing import Any, Dict, Iterable, Optional, Tuple, TypeVar

from openjiuwen.core.common.logging import logger

ClientT = TypeVar("ClientT")
CacheEntryLoopMap = Dict[Tuple, Tuple[int, weakref.ReferenceType[asyncio.AbstractEventLoop]]]
LoopFinalizerMap = weakref.WeakKeyDictionary  # WeakKeyDictionary[AbstractEventLoop, AsyncGenerator]


def _get_running_loop_or_none() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _register_loop_shutdown_cleanup(
    loop: asyncio.AbstractEventLoop,
    client_cache: Dict[Tuple, Any],
    cache_loops: CacheEntryLoopMap,
    loop_finalizers: LoopFinalizerMap,
    client_label: str,
) -> None:
    """Register an async-generator finalizer so ``loop.shutdown_asyncgens()`` closes loop-bound pools.

    Priming up to ``yield`` is synchronous (no ``await`` before ``yield``), which registers
    the generator in ``loop._asyncgens`` immediately without spawning a background Task.
    When ``asyncio.run()`` or ``pytest-asyncio`` tears the loop down, ``shutdown_asyncgens()``
    drives the ``finally`` block while the loop is still open, cleanly closing keep-alive
    transports before ``loop.close()``.
    """
    if loop in loop_finalizers:
        return

    async def _cleanup_on_loop_shutdown() -> AsyncGenerator[None, None]:
        try:
            yield
        finally:
            stale_keys = [
                key
                for key, (client_id, loop_ref) in list(cache_loops.items())
                if loop_ref() is loop and id(client_cache.get(key)) == client_id
            ]
            for key in stale_keys:
                cache_loops.pop(key, None)
                stale_client = client_cache.pop(key, None)
                if stale_client is not None:
                    await _safe_close_async_client(stale_client, client_label)

    agen = _cleanup_on_loop_shutdown()
    coro = agen.__anext__()
    try:
        coro.send(None)
    except StopIteration:
        pass
    loop_finalizers[loop] = agen


async def _safe_close_async_client(client: Any, client_label: str) -> bool:
    """Close an async SDK client, downgrading closed-loop teardown races to debug."""
    try:
        await client.close()
        return True
    except RuntimeError as exc:
        if "Event loop is closed" in str(exc):
            logger.debug(f"Skipped closing {client_label} on already-closed event loop: {exc}")
            return False
        logger.warning(f"Error closing {client_label}: {exc}")
        return False
    except Exception as exc:  # pragma: no cover - defensive cleanup
        logger.warning(f"Error closing {client_label}: {exc}")
        return False


def get_or_build_shared_async_client(
    *,
    key: Tuple,
    client_cache: Dict[Tuple, ClientT],
    cache_loops: CacheEntryLoopMap,
    loop_finalizers: LoopFinalizerMap,
    build_client: Callable[[], ClientT],
    on_created: Callable[[], None],
    client_label: str,
) -> ClientT:
    """Return a cached client bound to the current event loop, rebuilding if the loop changed."""
    current_loop = _get_running_loop_or_none()
    client = client_cache.get(key)

    if client is not None and current_loop is not None:
        entry = cache_loops.get(key)
        if entry is not None and entry[0] == id(client):
            cached_loop = entry[1]()
            if cached_loop is None or cached_loop is not current_loop or cached_loop.is_closed():
                client_cache.pop(key, None)
                cache_loops.pop(key, None)
                client = None

    if client is None:
        client = build_client()
        client_cache[key] = client
        if current_loop is not None:
            cache_loops[key] = (id(client), weakref.ref(current_loop))
            _register_loop_shutdown_cleanup(
                current_loop,
                client_cache,
                cache_loops,
                loop_finalizers,
                client_label,
            )
        on_created()

    return client


async def aclose_all_shared_clients(
    *,
    client_cache: Dict[Tuple, Any],
    cache_loops: CacheEntryLoopMap,
    client_label: str,
) -> None:
    """Close and clear all cached async SDK clients."""
    clients = list(client_cache.values())
    client_cache.clear()
    cache_loops.clear()

    for client in clients:
        await _safe_close_async_client(client, f"cached {client_label}")


async def aclose_targeted_shared_clients(
    *,
    keys: Iterable[Tuple],
    client_cache: Dict[Tuple, Any],
    cache_loops: CacheEntryLoopMap,
    client_label: str,
) -> int:
    """Close and evict cached async SDK clients matching ``keys``."""
    closed = 0
    for key in keys:
        cache_loops.pop(key, None)
        client = client_cache.pop(key, None)
        if client is None:
            continue
        if await _safe_close_async_client(client, client_label):
            closed += 1
    if closed:
        logger.info(f"Closed {closed} {client_label}(s) for removed/updated model config")
    return closed
