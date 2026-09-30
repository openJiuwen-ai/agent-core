# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offline L2/L3 reflection memory: cross-episode extraction of pairwise
collaboration notes (L2) and team-composition/spawning-strategy guidance
(L3), rendered back into a team member's system prompt.

Independent of ``agent_teams.evolving_memory`` (ACE/ReMe/ReasoningBank-style
embedding retrieval, wrapping ``extensions.context_evolver``) — this
subsystem does deterministic, unconditional rendering from a plain
YAML/JSONL bank with no vector store, and is written offline (a separate
batch step over completed episodes) rather than during a live run. The two
can be enabled together or separately.

Public API re-exports for simplified imports:
    from openjiuwen.extensions.context_evolver.offline_memory import reflect_pair, propose_actions, apply_actions
"""

from openjiuwen.extensions.context_evolver.offline_memory import bank_io
from openjiuwen.extensions.context_evolver.offline_memory.l2 import L2ReflectionResult, reflect_pair
from openjiuwen.extensions.context_evolver.offline_memory.l3 import (
    L3ActionsResult,
    L3MergeGroup,
    L3MergesResult,
    apply_actions,
    apply_deprecation_pass,
    apply_merges,
    format_items_for_dedup,
    propose_actions,
    propose_merges,
)
from openjiuwen.extensions.context_evolver.offline_memory.role_normalizer import (
    RoleClassificationResult,
    RoleNormalizer,
    RosterMemberLike,
    SEED_TAXONOMY,
)

__all__ = [
    "bank_io",
    "L2ReflectionResult",
    "reflect_pair",
    "L3ActionsResult",
    "L3MergeGroup",
    "L3MergesResult",
    "apply_actions",
    "apply_deprecation_pass",
    "apply_merges",
    "format_items_for_dedup",
    "propose_actions",
    "propose_merges",
    "compare_modes",
    "format_for_agent",
    "format_for_leader",
    "RoleClassificationResult",
    "RoleNormalizer",
    "RosterMemberLike",
    "SEED_TAXONOMY",
]


def __getattr__(name: str):
    """Load renderer exports on demand so its ``python -m`` CLI runs cleanly."""
    if name in {"compare_modes", "format_for_agent", "format_for_leader"}:
        from openjiuwen.extensions.context_evolver.offline_memory import render_for_injection

        value = getattr(render_for_injection, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
