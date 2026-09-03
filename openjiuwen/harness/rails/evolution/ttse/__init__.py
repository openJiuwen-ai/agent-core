# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TTSE - Two-Track Self-Evolution as a native jiuwen evolution rail.

Public surface:
    TTSERail        - EvolutionRail + FACT/TIP induction & injection.
    TTSEConfig      - Knobs (bank path, caps, dedup threshold, embedding, batch, ...).
    TTSERecordStore - Shared FACT/TIP bank with JSON persistence + dedup.
    SuccessDetector / SuccessOutcome / TrajectoryErrorSuccessDetector
    SignalBasedSuccessDetector
                    - Default success gating (failure fast-path + reply Judge).
"""

from .config import TTSEConfig
from .configuration import configure_ttse_evolution, unconfigure_ttse_evolution
from .stores import TTSERecordStore
from .success import (
    SignalBasedSuccessDetector,
    SuccessDetector,
    SuccessOutcome,
    TrajectoryErrorSuccessDetector,
)
from .ttse_rail import TTSERail

__all__ = [
    "TTSERail",
    "TTSEConfig",
    "TTSERecordStore",
    "SuccessDetector",
    "SuccessOutcome",
    "TrajectoryErrorSuccessDetector",
    "SignalBasedSuccessDetector",
    "configure_ttse_evolution",
    "unconfigure_ttse_evolution",
]
