# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Canonical trajectory model, capture processor, and synchronous archives."""

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.store import (
    FileTrajectoryStore,
    InMemoryTrajectoryStore,
    TrajectoryStore,
)

_REMOVED_EXPORT_HINTS = {
    "TrajectoryBuilder": "use openjiuwen.agent_evolving.trajectory.offline.TrajectoryBuilder",
    "TrajectoryExtractor": "use openjiuwen.agent_evolving.trajectory.offline.TrajectoryExtractor",
    "TracerTrajectoryExtractor": "use openjiuwen.agent_evolving.trajectory.offline.TrajectoryExtractor",
    "TrajectoryStep": "use canonical Trajectory spans and trajectory.spans accessors",
    "UpdateKey": "use openjiuwen.agent_evolving.types.UpdateKey",
    "Updates": "use openjiuwen.agent_evolving.types.Updates",
}


def __getattr__(name: str):
    hint = _REMOVED_EXPORT_HINTS.get(name)
    if hint is not None:
        raise AttributeError(f"{__name__}.{name} was removed; {hint}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FileTrajectoryStore",
    "InMemoryTrajectoryStore",
    "Trajectory",
    "TrajectorySpanProcessor",
    "TrajectoryStore",
]
