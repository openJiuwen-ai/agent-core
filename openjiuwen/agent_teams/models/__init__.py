# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Multi-model deployment primitives for team agents.

Pool data structures (``ModelPoolEntry``, ``ModelRouterConfig``,
``IntelliRouterConfig``, ``inherit_pool_ids``) plus the allocator
subsystem (``Allocation``, ``ModelAllocator`` protocol, the four shipped
strategies, ``build_model_allocator`` factory, and the positional
resolver ``resolve_member_model``) live here so multi-model concerns stay
independent from the runtime ``agent/`` and pure ``schema/`` layers.
"""

from openjiuwen.agent_teams.models.allocator import (
    Allocation,
    ByModelNameAllocator,
    IntelliRouterAllocator,
    ModelAllocator,
    RoundRobinModelAllocator,
    RouterAllocator,
    build_model_allocator,
    resolve_member_model,
)
from openjiuwen.agent_teams.models.pool import (
    INTELLI_ROUTER_PROVIDER,
    INTELLI_ROUTER_UNIFIED_MODEL,
    IntelliRouterConfig,
    IntelliRouterDeployment,
    ModelPoolEntry,
    ModelRouterConfig,
    inherit_pool_ids,
)

__all__ = [
    "INTELLI_ROUTER_PROVIDER",
    "INTELLI_ROUTER_UNIFIED_MODEL",
    "Allocation",
    "ByModelNameAllocator",
    "IntelliRouterAllocator",
    "IntelliRouterConfig",
    "IntelliRouterDeployment",
    "ModelAllocator",
    "ModelPoolEntry",
    "ModelRouterConfig",
    "RoundRobinModelAllocator",
    "RouterAllocator",
    "build_model_allocator",
    "inherit_pool_ids",
    "resolve_member_model",
]
