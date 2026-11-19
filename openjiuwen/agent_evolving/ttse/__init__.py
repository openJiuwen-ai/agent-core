# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TTSE algorithm: FACT/TIP bank, induction, consult, Auto-dream.

The harness I/O layer is :class:`~openjiuwen.harness.rails.evolution.ttse_rail.TTSERail`.
"""

from .config import TTSEConfig
from .bank.stores import TTSERecordStore
from .evolve.success import (
    SignalBasedSuccessDetector,
    SuccessDetector,
    SuccessOutcome,
    TrajectoryErrorSuccessDetector,
)

__all__ = [
    "TTSEConfig",
    "TTSERecordStore",
    "SuccessDetector",
    "SuccessOutcome",
    "TrajectoryErrorSuccessDetector",
    "SignalBasedSuccessDetector",
]
