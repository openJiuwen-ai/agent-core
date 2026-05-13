# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamAgent private runtime resources.

The third quadrant of the four-quadrant TeamAgent decomposition: per-instance
runtime resources owned by a single TeamAgent. Unlike TeamInfra (which is
shared per process), every TeamAgent has its own harness, worktree manager,
memory manager, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Optional,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.harness import TeamHarness
    from openjiuwen.agent_teams.models.allocator import ModelAllocator
    from openjiuwen.agent_teams.rails import FirstIterationGate
    from openjiuwen.core.memory.team.manager import TeamMemoryManager
    from openjiuwen.harness.tools.worktree import WorktreeManager


@dataclass
class PrivateAgentResources:
    """Per-instance runtime resources for a single TeamAgent.

    These objects are not shared across team members and are constructed
    by AgentBuilder based on the assembly blueprint. ``harness`` is the
    sole accessor for the underlying DeepAgent runtime; do not introduce
    a separate DeepAgent reference here.
    """

    harness: Optional["TeamHarness"] = None
    worktree_manager: Optional["WorktreeManager"] = None
    memory_manager: Optional["TeamMemoryManager"] = None
    first_iter_gate: Optional["FirstIterationGate"] = None
    model_allocator: Optional["ModelAllocator"] = None  # leader-only


__all__ = ["PrivateAgentResources"]
