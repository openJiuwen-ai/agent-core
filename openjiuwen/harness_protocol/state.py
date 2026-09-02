# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-neutral lifecycle state for third-party agent harnesses."""

from enum import Enum


class HarnessState(str, Enum):
    """High-level lifecycle phase shared by harness implementations.

    ``PAUSING`` is the transient phase between an accepted pause request and
    the harness reaching a resumable boundary.
    """

    IDLE = "idle"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    TERMINATED = "terminated"


__all__ = ["HarnessState"]
