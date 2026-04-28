# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TrajectoryRail: Built-in trajectory collection rail.

Only collects trajectories, does not trigger any evolution logic.

Use cases:
- Observability and debugging: Record complete agent behavior trajectories
- Offline data collection: Accumulate data for subsequent offline training
- Behavior analysis: Write trajectories to storage for external system consumption

Bound to the evolution framework (inherits EvolutionRail), trajectory format
is fully consistent with the evolution path.
"""

from __future__ import annotations

from typing import Optional

from openjiuwen.agent_evolving.trajectory import TrajectoryStore
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail


class TrajectoryRail(EvolutionRail):
    """Trajectory collection only, no evolution.

    Inherits EvolutionRail: trajectory collection is automatic (4 hooks).
    No need to override any methods - just collects trajectories.

    Usage::

        agent.add_rail(TrajectoryRail())  # Ready to use, no config needed

    For custom trajectory storage::

        store = FileTrajectoryStore(Path("/path/to/trajectories"))
        agent.add_rail(TrajectoryRail(trajectory_store=store))
    """

    priority = 10

    def __init__(self, trajectory_store: Optional[TrajectoryStore] = None):
        """Initialize TrajectoryRail.

        Args:
            trajectory_store: Optional trajectory store. If None, uses InMemoryTrajectoryStore.
        """
        super().__init__(trajectory_store=trajectory_store)


__all__ = ["TrajectoryRail"]
