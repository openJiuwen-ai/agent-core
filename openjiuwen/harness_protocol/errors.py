# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Errors raised by third-party agent harness protocol implementations."""

from __future__ import annotations


class HarnessError(RuntimeError):
    """Base error for the third-party harness protocol boundary."""


class HarnessStateError(HarnessError):
    """Raised when a command is invalid for the harness's current state."""


class UnsupportedHarnessCapabilityError(HarnessError):
    """Raised when the caller requests a capability the harness did not declare."""


class HarnessProtocolError(HarnessError):
    """Raised when an implementation violates a protocol invariant."""


class CheckpointConflictError(HarnessProtocolError):
    """Raised when a checkpoint write is stale or violates compare-and-set."""


__all__ = [
    "HarnessError",
    "HarnessProtocolError",
    "HarnessStateError",
    "CheckpointConflictError",
    "UnsupportedHarnessCapabilityError",
]
