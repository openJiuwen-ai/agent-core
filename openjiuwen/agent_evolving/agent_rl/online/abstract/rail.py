# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rail interfaces shared by RL and SFT online collection backends."""

from __future__ import annotations

from typing import Protocol

from openjiuwen.harness.rails import EvolutionRail


class OnlineTrainingRail(EvolutionRail, Protocol):
    """Marker protocol for rails that upload online training trajectories."""
