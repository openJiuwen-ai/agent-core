# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lifecycle helpers shared by synchronous and asynchronous subagent calls."""

from __future__ import annotations

import inspect
from typing import Any

from openjiuwen.core.common.logging import logger


async def prepare_subagent_task_resources(subagent: Any) -> None:
    """Acquire optional task resource references before invocation."""
    prepare = getattr(subagent, "prepare_task_resources", None)
    if not callable(prepare):
        return
    result = prepare()
    if inspect.isawaitable(result):
        await result


async def cleanup_subagent_task_resources(subagent: Any) -> None:
    """Run optional task cleanup without masking the subagent result."""
    cleanup = getattr(subagent, "cleanup_task_resources", None)
    if not callable(cleanup):
        return
    try:
        result = cleanup()
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception("[SubagentLifecycle] task resource cleanup failed")


__all__ = [
    "cleanup_subagent_task_resources",
    "prepare_subagent_task_resources",
]
