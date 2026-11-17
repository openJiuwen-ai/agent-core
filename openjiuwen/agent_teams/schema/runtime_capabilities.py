# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Hot-updatable capability overlay for ``TeamAgentSpec``.

Session persistence and the in-memory pool keep a mounted ``TeamAgentSpec`` as
the runtime authority for members (``configure(leader.spec, …)``). Platform
callers, however, re-enrich a tip spec on every request. Without a reconcile
step, cold recover and resume-from-pause keep stale ``model`` / ``skills`` /
pool fields even when the tip already carries the new values.

:func:`apply_runtime_capabilities` is that reconcile step: identity and layout
stay on the mounted base; capability fields are taken from the live tip.

:func:`resolve_effective_model_pool` mirrors ``TeamAgentSpec.build`` expansion
of ``model_router`` / ``model_intelli_router`` into the flat ``TeamSpec`` pool
view so resume / recover can refresh allocators without a full rebuild.
"""

from __future__ import annotations

from typing import Any, Literal

from openjiuwen.agent_teams.models.pool import ModelPoolEntry
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

# Team-level model sourcing must move as a unit: pool / router / intelli-router
# are mutually exclusive on ``TeamAgentSpec``.
_TEAM_MODEL_FIELDS: tuple[str, ...] = (
    "model_pool",
    "model_pool_strategy",
    "model_router",
    "model_intelli_router",
)

ModelPoolStrategy = Literal["round_robin", "by_model_name", "router", "intelli_router"]


def resolve_effective_model_pool(
    spec: TeamAgentSpec,
) -> tuple[list[ModelPoolEntry], ModelPoolStrategy]:
    """Expand tip model sourcing into the flat pool view used by allocators.

    Matches ``TeamAgentSpec.build``: router / intelli-router configs expand via
    ``to_pool_entries``; otherwise the declared ``model_pool`` is used as-is.
    """
    if spec.model_router is not None:
        return list(spec.model_router.to_pool_entries()), "router"
    if spec.model_intelli_router is not None:
        return list(spec.model_intelli_router.to_pool_entries()), "intelli_router"
    return list(spec.model_pool), spec.model_pool_strategy


def _overlay_agent_capabilities(
    base_agent: DeepAgentSpec,
    runtime_agent: DeepAgentSpec,
) -> DeepAgentSpec:
    """Copy hot-updatable fields from *runtime_agent* onto *base_agent*."""
    model = runtime_agent.model
    updates: dict[str, Any] = {
        "model": model.model_copy(deep=True) if model is not None else None,
        "skills": (
            list(runtime_agent.skills) if runtime_agent.skills is not None else None
        ),
    }
    return base_agent.model_copy(update=updates)


def apply_runtime_capabilities(
    base: TeamAgentSpec,
    runtime: TeamAgentSpec,
) -> TeamAgentSpec:
    """Return *base* with model / skills / pool fields taken from *runtime*.

    Args:
        base: Mounted or checkpoint-restored spec (identity + layout authority).
        runtime: Live tip from the current process (capability authority).

    Returns:
        A new ``TeamAgentSpec``. *base* and *runtime* are not mutated.

    Notes:
        - Agent keys present only on *base* are kept (dynamic member templates).
        - Agent keys present on *runtime* update ``model`` / ``skills`` on the
          matching base entry, or are added when the base has no such key.
        - Team model sourcing (``model_pool`` / routers / strategy) is replaced
          wholesale from *runtime* so mutual-exclusion stays valid.
        - Workspace, spawn mode, roster, transport, and similar layout fields
          stay on *base*.
    """
    merged_agents: dict[str, DeepAgentSpec] = {
        key: agent.model_copy(deep=True) for key, agent in base.agents.items()
    }
    for key, runtime_agent in runtime.agents.items():
        current = merged_agents.get(key)
        if current is None:
            merged_agents[key] = runtime_agent.model_copy(deep=True)
            continue
        merged_agents[key] = _overlay_agent_capabilities(current, runtime_agent)

    team_updates: dict[str, Any] = {
        "agents": merged_agents,
        "model_pool": [entry.model_copy(deep=True) for entry in runtime.model_pool],
        "model_pool_strategy": runtime.model_pool_strategy,
        "model_router": (
            runtime.model_router.model_copy(deep=True)
            if runtime.model_router is not None
            else None
        ),
        "model_intelli_router": (
            runtime.model_intelli_router.model_copy(deep=True)
            if runtime.model_intelli_router is not None
            else None
        ),
    }
    return base.model_copy(update=team_updates)


def apply_runtime_capabilities_inplace(
    target: TeamAgentSpec,
    runtime: TeamAgentSpec,
) -> TeamAgentSpec:
    """Apply :func:`apply_runtime_capabilities` and write the result into *target*.

    Used when a frozen blueprint holds a stable reference to the mounted spec
    object (``TeamAgentBlueprint.spec``): callers must mutate that object rather
    than replace the blueprint field.

    Returns:
        The same *target* instance after its capability fields were updated.
    """
    merged = apply_runtime_capabilities(target, runtime)
    target.agents.clear()
    target.agents.update(merged.agents)
    for field in _TEAM_MODEL_FIELDS:
        setattr(target, field, getattr(merged, field))
    return target


__all__ = [
    "apply_runtime_capabilities",
    "apply_runtime_capabilities_inplace",
    "resolve_effective_model_pool",
]
