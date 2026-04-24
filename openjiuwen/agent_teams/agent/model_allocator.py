# coding: utf-8
"""Model config allocators for team member spawning.

When ``TeamSpec.model_pool`` is non-empty, ``RoundRobinModelAllocator``
distributes pool entries across leader and teammates so concurrent calls
spread across endpoints instead of saturating a single one. When the
pool is empty (default), no allocator is built — every member resolves
its model from ``TeamAgentSpec.agents`` via the regular per-agent
fallback in ``TeamAgent._setup_agent``.

Custom allocation strategies (weighted, least-recently-used, ...) only
need to satisfy the ``ModelAllocator`` protocol.
"""

from __future__ import annotations

from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
    from openjiuwen.agent_teams.schema.team import ModelPoolEntry, TeamSpec


@runtime_checkable
class ModelAllocator(Protocol):
    """Allocates one ``TeamModelConfig`` per call.

    Implementations encapsulate the policy for picking the next model
    (round-robin, weighted, least-recently-used, ...). Returning ``None``
    signals "no model available from this allocator" — callers fall back
    to the agent's own model config.
    """

    def allocate(self) -> Optional["TeamModelConfig"]:
        """Return the next allocated model config, or None when unavailable."""
        ...


class RoundRobinModelAllocator:
    """Round-robin allocator over a ``TeamSpec.model_pool``.

    Each call to ``allocate`` returns the next entry in pool order and
    wraps when the end is reached, so a team with N members and M pool
    entries spreads members evenly across endpoints.
    """

    def __init__(self, pool: list["ModelPoolEntry"]) -> None:
        """Initialize with the pool entries to rotate over."""
        self._pool = list(pool)
        self._index = 0

    def allocate(self) -> Optional["TeamModelConfig"]:
        """Return the next pool entry as a TeamModelConfig.

        Returns:
            The next ``TeamModelConfig`` materialized from the pool, or
            ``None`` if the pool is empty.
        """
        if not self._pool:
            return None
        entry = self._pool[self._index % len(self._pool)]
        self._index += 1
        return entry.to_team_model_config()


def build_model_allocator(
    spec: "TeamAgentSpec",
    team_spec: "TeamSpec",
) -> Optional[ModelAllocator]:
    """Build a model allocator for a team, or return ``None``.

    Pool-based allocation is only enabled when ``team_spec.model_pool``
    is non-empty. Without a pool the function returns ``None`` so every
    member resolves its model from ``TeamAgentSpec.agents`` as before
    and behavior matches the legacy code path exactly.

    Args:
        spec: Team agent specification (reserved for future allocator
            policies that need access to per-agent metadata).
        team_spec: Resolved team identity carrying ``model_pool``.

    Returns:
        A ``ModelAllocator`` instance when a pool is configured,
        otherwise ``None``.
    """
    del spec  # reserved for future policies that need agent metadata
    if team_spec.model_pool:
        return RoundRobinModelAllocator(team_spec.model_pool)
    return None


__all__ = [
    "ModelAllocator",
    "RoundRobinModelAllocator",
    "build_model_allocator",
]
