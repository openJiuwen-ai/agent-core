# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Trajectory operations module.

This module provides backward compatibility alias for TrajectoryExtractor.
"""

from __future__ import annotations

# Backward compatibility: import TrajectoryExtractor as alias
from openjiuwen.agent_evolving.trajectory.extractor import (
    TrajectoryExtractor as TracerTrajectoryExtractor  # noqa: F401
)
