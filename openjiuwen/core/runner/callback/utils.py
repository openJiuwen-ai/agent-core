# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Callback Framework Utility Functions

Provides simplified access to callback framework singleton.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework


def get_callback_framework() -> "AsyncCallbackFramework":
    """Get the callback framework singleton.

    Returns:
        The AsyncCallbackFramework instance from Runner.

    Raises:
        AttributeError: If Runner or callback_framework is not initialized.
    """
    from openjiuwen.core.runner import Runner

    return Runner.callback_framework


async def emit(event, **kwargs):
    """Emit a callback event.

    Args:
        event: The event from the Events class (e.g., LLMCallEvents.LLM_CALL_STARTED)
        **kwargs: Additional arguments passed to the trigger call.
    """
    fw = get_callback_framework()
    await fw.trigger(event, **kwargs)
