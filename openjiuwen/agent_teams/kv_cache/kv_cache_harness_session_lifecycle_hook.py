# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lifecycle hooks for standalone Team Harness Sessions using KVC."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

from openjiuwen.core.common.logging import team_logger


@dataclass
class _HarnessSessionHookState:
    parent_session_id: str | None
    evict_on_finish: bool


_HARNESS_SESSION_HOOKS: WeakKeyDictionary[Any, _HarnessSessionHookState] = WeakKeyDictionary()


def configure_harness_session_hooks(
    harness: Any,
    *,
    product_session_id: Any,
    evict_on_finish: bool,
) -> bool:
    """Configure lineage and terminal behavior for a standalone worker."""
    if not is_harness_affinity_enabled(harness):
        return False
    try:
        _HARNESS_SESSION_HOOKS[harness] = _HarnessSessionHookState(
            parent_session_id=_normalize_session_id(product_session_id),
            evict_on_finish=evict_on_finish,
        )
        return True
    except Exception as exc:
        _log_failure("configure_harness_session_hooks", exc)
        return False


def on_harness_session_created(harness: Any, session: Any) -> None:
    """Attach a worker Session to its product-level cache root."""
    try:
        state = _HARNESS_SESSION_HOOKS.get(harness)
        if state is None or state.parent_session_id is None:
            return
        bind_parent = getattr(session, "bind_parent_session_id", None)
        if callable(bind_parent):
            bind_parent(state.parent_session_id)
    except Exception as exc:
        _log_failure("on_harness_session_created", exc)


async def after_harness_session_finished(harness: Any, session: Any) -> None:
    """Release a one-shot worker's KVC after its inference has settled."""
    try:
        state = _HARNESS_SESSION_HOOKS.get(harness)
        if state is None or not state.evict_on_finish:
            return
        release_kvc = getattr(session, "release_kvc", None)
        if callable(release_kvc):
            await release_kvc()
    except Exception as exc:
        _log_failure("after_harness_session_finished", exc)
    finally:
        state = _HARNESS_SESSION_HOOKS.get(harness)
        if state is not None and state.evict_on_finish:
            _HARNESS_SESSION_HOOKS.pop(harness, None)


def clear_harness_session_hooks(harness: Any) -> None:
    _HARNESS_SESSION_HOOKS.pop(harness, None)


def is_harness_affinity_enabled(harness: Any) -> bool:
    try:
        deep_config = getattr(harness, "deep_config", None)
        config = getattr(deep_config, "kv_cache_affinity_config", None)
        return getattr(config, "enable_kv_cache_affinity", False) is True
    except Exception as exc:
        _log_failure("is_harness_affinity_enabled", exc)
        return False


def _normalize_session_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _log_failure(operation: str, exc: Exception) -> None:
    team_logger.warning("[TeamKVC] {} failed; preserving normal flow: {}", operation, exc)


__all__ = [
    "after_harness_session_finished",
    "clear_harness_session_hooks",
    "configure_harness_session_hooks",
    "is_harness_affinity_enabled",
    "on_harness_session_created",
]
