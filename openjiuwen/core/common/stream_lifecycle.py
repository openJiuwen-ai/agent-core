# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Detect explicitly owned native streams without importing a harness."""

import inspect


def owns_stream_lifecycle(func, args, kwargs):
    """Return whether the call is explicitly owned by a stream lifecycle.

    The result defaults to false so ordinary native and personal-agent streams
    retain their existing cancellation behavior.
    """
    if kwargs.get("_background_task_lifecycle") is not None:
        return True
    original = inspect.unwrap(func)
    owner = getattr(original, "__self__", None)
    if owner is None and args:
        owner = args[0]
    config = getattr(owner, "deep_config", None)
    return getattr(config, "stream_task_lifecycle", None) is not None
