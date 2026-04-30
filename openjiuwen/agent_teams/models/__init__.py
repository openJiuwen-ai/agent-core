# coding: utf-8
"""Multi-model deployment primitives for team agents.

Pool data structures (``ModelPoolEntry``, ``inherit_pool_ids``) plus the
allocator subsystem (``Allocation``, ``ModelAllocator`` protocol, the
two shipped strategies, ``build_model_allocator`` factory, and the
positional resolver ``resolve_member_model``) live here so multi-model
concerns stay independent from the runtime ``agent/`` and pure ``schema/``
layers.
"""

from openjiuwen.agent_teams.models.allocator import (
    Allocation,
    ByModelNameAllocator,
    ModelAllocator,
    RoundRobinModelAllocator,
    build_model_allocator,
    resolve_member_model,
)
from openjiuwen.agent_teams.models.pool import (
    ModelPoolEntry,
    inherit_pool_ids,
)

__all__ = [
    "Allocation",
    "ByModelNameAllocator",
    "ModelAllocator",
    "ModelPoolEntry",
    "RoundRobinModelAllocator",
    "build_model_allocator",
    "inherit_pool_ids",
    "resolve_member_model",
]
