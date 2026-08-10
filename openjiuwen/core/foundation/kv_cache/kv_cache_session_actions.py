# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ordered, best-effort KV-cache actions for one provider session."""

import asyncio
import contextlib
from typing import Any, Literal

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.kv_cache.kv_cache_config import resolve_kvc_action_timeout


SessionKVCacheSignalAction = Literal["offload", "prefetch"]

_SESSION_SIGNAL_TASKS: set[asyncio.Task[bool]] = set()
_SESSION_SIGNAL_TAILS: dict[tuple[int, str], asyncio.Future[bool]] = {}


async def _run_session_kv_action(
        model: Any,
        action: str,
        *,
        session_id: str,
        parent_session_id: str | None = None,
        timeout: float | None = None,
        enabled: bool = True,
) -> bool:
    if not enabled or model is None or not session_id:
        return False
    supports = getattr(model, "supports_kv_cache_affinity", None)
    if not callable(supports):
        return False
    try:
        if not supports():
            return False
    except Exception as exc:
        logger.warning(
            "[KVCache] capability check failed closed: action=%s session_id=%s error=%s",
            action,
            session_id,
            exc,
        )
        return False
    action_fn = getattr(model, f"{action}_kvc", None)
    if not callable(action_fn):
        return False
    try:
        action_timeout = resolve_kvc_action_timeout(action, "session", timeout)
        return bool(
            await action_fn(
                target="session",
                session_id=session_id,
                parent_session_id=parent_session_id or session_id,
                timeout=action_timeout,
            )
        )
    except Exception as exc:
        logger.warning(
            "[KVCache] session action failed: action=%s session_id=%s error=%s",
            action,
            session_id,
            exc,
        )
        return False


def dispatch_session_kv_cache_signal(
        model: Any,
        action: SessionKVCacheSignalAction,
        *,
        session_id: str,
        parent_session_id: str | None = None,
        timeout: float | None = None,
        enabled: bool = True,
) -> bool:
    """Schedule an ordered offload/prefetch without blocking the caller."""
    if action not in ("offload", "prefetch"):
        raise ValueError(f"unsupported KV cache signal action: {action}")
    if not enabled or model is None or not session_id:
        return False

    signal_key = (id(model), session_id)
    previous = _SESSION_SIGNAL_TAILS.get(signal_key)

    async def _run_ordered() -> bool:
        if previous is not None:
            await asyncio.gather(asyncio.shield(previous), return_exceptions=True)
        return await _run_session_kv_action(
            model,
            action,
            session_id=session_id,
            parent_session_id=parent_session_id,
            timeout=timeout,
            enabled=enabled,
        )

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _run_ordered(),
            name=f"kvc-{action}[{session_id}]",
        )
    except RuntimeError:
        logger.warning(
            "[KVCache] cannot schedule signal without a running event loop: "
            "action=%s session_id=%s",
            action,
            session_id,
        )
        return False

    _SESSION_SIGNAL_TASKS.add(task)
    _SESSION_SIGNAL_TAILS[signal_key] = task

    def _consume_result(done: asyncio.Task[bool]) -> None:
        _SESSION_SIGNAL_TASKS.discard(done)
        if _SESSION_SIGNAL_TAILS.get(signal_key) is done:
            _SESSION_SIGNAL_TAILS.pop(signal_key, None)
        try:
            succeeded = done.result()
            if not succeeded:
                logger.warning(
                    "[KVCache] background signal returned failure: "
                    "action=%s session_id=%s",
                    action,
                    session_id,
                )
        except asyncio.CancelledError:
            logger.debug(
                "[KVCache] background signal cancelled: "
                "action=%s session_id=%s",
                action,
                session_id,
            )
        except Exception as exc:  # pragma: no cover - defensive callback boundary
            logger.warning(
                "[KVCache] background signal failed: "
                "action=%s session_id=%s error=%s",
                action,
                session_id,
                exc,
            )

    task.add_done_callback(_consume_result)
    return True


async def cancel_pending_session_kv_cache_signals() -> None:
    """Cancel and consume pending generic session signals on shutdown/tests."""
    tasks = tuple(_SESSION_SIGNAL_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)
    _SESSION_SIGNAL_TAILS.clear()


async def evict_session_kv_cache(
        model: Any,
        *,
        session_id: str,
        parent_session_id: str | None = None,
        timeout: float | None = None,
        enabled: bool = True,
) -> bool:
    if not enabled or model is None or not session_id:
        return False
    signal_key = (id(model), session_id)
    previous = _SESSION_SIGNAL_TAILS.get(signal_key)
    barrier = asyncio.get_running_loop().create_future()
    _SESSION_SIGNAL_TAILS[signal_key] = barrier
    succeeded = False
    try:
        if previous is not None:
            await asyncio.gather(asyncio.shield(previous), return_exceptions=True)
        succeeded = await _run_session_kv_action(
            model,
            "evict",
            session_id=session_id,
            parent_session_id=parent_session_id,
            timeout=timeout,
            enabled=enabled,
        )
        return succeeded
    finally:
        if not barrier.done():
            barrier.set_result(succeeded)
        if _SESSION_SIGNAL_TAILS.get(signal_key) is barrier:
            _SESSION_SIGNAL_TAILS.pop(signal_key, None)


async def offload_session_kv_cache(
        model: Any,
        *,
        session_id: str,
        parent_session_id: str | None = None,
        timeout: float | None = None,
        enabled: bool = True,
) -> bool:
    return await _run_session_kv_action(
        model,
        "offload",
        session_id=session_id,
        parent_session_id=parent_session_id,
        timeout=timeout,
        enabled=enabled,
    )


async def prefetch_session_kv_cache(
        model: Any,
        *,
        session_id: str,
        parent_session_id: str | None = None,
        timeout: float | None = None,
        enabled: bool = True,
) -> bool:
    return await _run_session_kv_action(
        model,
        "prefetch",
        session_id=session_id,
        parent_session_id=parent_session_id,
        timeout=timeout,
        enabled=enabled,
    )
