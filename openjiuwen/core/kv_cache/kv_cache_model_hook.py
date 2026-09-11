# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.kv_cache.kv_cache_types import (
    InferenceLease,
    KVCacheRuntimeProtocol,
)

_RuntimeLease = tuple[KVCacheRuntimeProtocol, InferenceLease | None]


class KVCacheModelHook:
    """Keep Runtime inference accounting around affinity-enabled model calls."""

    @staticmethod
    async def begin(
        model: Any,
        request_kwargs: dict,
        model_name: str | None,
    ) -> _RuntimeLease | None:
        if not request_kwargs.get("session_id"):
            return None
        try:
            supports = getattr(model, "supports_kv_cache_affinity", None)
            if not callable(supports) or not supports():
                return None
            # Delay the Session package import until Model initialization is
            # complete to avoid foundation.llm <-> session initialization cycles.
            from openjiuwen.core.session import get_current_session

            session = get_current_session()
            get_runtime = getattr(session, "get_kv_cache_runtime", None)
            get_identity = getattr(session, "get_cache_identity", None)
            if not callable(get_runtime) or not callable(get_identity):
                return None
            runtime = get_runtime()
            if runtime is None:
                return None
            identity = get_identity()
            if request_kwargs["session_id"] != identity.cache_id:
                return None
            return runtime, await runtime.begin_inference(
                identity,
                model,
                model_name=model_name,
            )
        except asyncio.CancelledError as exc:
            if _caller_is_cancelling():
                raise
            logger.warning(
                "KVC inference admission was internally cancelled; continue inference: %s",
                exc,
            )
            return None
        except Exception as exc:
            logger.warning("KVC inference admission failed; continue inference: %s", exc)
            return None

    @staticmethod
    async def end(
        runtime_lease: _RuntimeLease | None,
        *,
        succeeded: bool,
    ) -> None:
        if runtime_lease is None:
            return
        runtime, lease = runtime_lease
        try:
            await runtime.end_inference(lease, succeeded=succeeded)
        except asyncio.CancelledError as exc:
            if _caller_is_cancelling():
                raise
            logger.warning(
                "KVC inference cleanup was internally cancelled; preserve model result: %s",
                exc,
            )
        except Exception as exc:
            logger.warning("KVC inference cleanup failed; preserve model result: %s", exc)


def _caller_is_cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


__all__ = ["KVCacheModelHook"]
