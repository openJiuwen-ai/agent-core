# coding: utf-8
"""Model config allocators for team member spawning.

When ``TeamSpec.model_pool`` is non-empty, an allocator distributes pool
entries across leader and teammates so concurrent calls spread across
endpoints instead of saturating a single one. Two strategies ship:

* ``RoundRobinModelAllocator`` — linear rotation through every entry,
  ignoring ``model_name``. Good for pools where every entry is an
  interchangeable endpoint of the same logical model.
* ``ByModelNameAllocator`` — partitions entries by ``model_name`` and
  rotates over the partitions at the outer level while round-robining
  endpoints within each partition. Each declared model name receives
  an equal share of allocations regardless of how many endpoints back
  it; useful when the pool mixes tiers (cheap vs expensive) and you
  want fair distribution per tier rather than per raw endpoint.

When the pool is empty no allocator is built — every member resolves
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


class ByModelNameAllocator:
    """Round-robin allocator that groups by ``model_name``.

    Pool entries are partitioned by their ``model_name`` field while
    preserving insertion order. Each ``allocate`` call:

    1. Picks the next ``model_name`` group in outer round-robin order.
    2. Returns the next entry from that group's inner round-robin
       counter, advancing the inner counter for that group only.

    Result: each distinct model name receives an equal share of
    allocations (1/N where N is the number of distinct names),
    independent of how many endpoints back each name. Within a group,
    endpoints rotate in pool order. Compared with
    ``RoundRobinModelAllocator`` this matters when the pool mixes
    cheap and expensive models — pure round-robin would over-allocate
    to whichever name has more entries.
    """

    def __init__(self, pool: list["ModelPoolEntry"]) -> None:
        """Initialize from the pool, partitioning entries by model_name."""
        self._groups: dict[str, list["ModelPoolEntry"]] = {}
        for entry in pool:
            self._groups.setdefault(entry.model_name, []).append(entry)
        self._names: list[str] = list(self._groups.keys())
        self._name_index = 0
        self._inner_indexes: dict[str, int] = {name: 0 for name in self._names}

    def allocate(self) -> Optional["TeamModelConfig"]:
        """Return the next allocated TeamModelConfig.

        Advances the outer name rotation and the inner endpoint
        rotation of the picked group. Returns ``None`` if the pool was
        empty at construction time.
        """
        if not self._names:
            return None
        name = self._names[self._name_index % len(self._names)]
        self._name_index += 1
        group = self._groups[name]
        idx = self._inner_indexes[name] % len(group)
        self._inner_indexes[name] += 1
        return group[idx].to_team_model_config()


def build_model_allocator(
    spec: "TeamAgentSpec",
    team_spec: "TeamSpec",
) -> Optional[ModelAllocator]:
    """Build a model allocator for a team, or return ``None``.

    Pool-based allocation is only enabled when ``team_spec.model_pool``
    is non-empty. Without a pool the function returns ``None`` so every
    member resolves its model from ``TeamAgentSpec.agents`` as before
    and behavior matches the legacy code path exactly. The strategy
    selected by ``team_spec.model_pool_strategy`` decides which
    concrete allocator is built.

    Args:
        spec: Team agent specification (reserved for future allocator
            policies that need access to per-agent metadata).
        team_spec: Resolved team identity carrying ``model_pool`` and
            ``model_pool_strategy``.

    Returns:
        A ``ModelAllocator`` instance when a pool is configured,
        otherwise ``None``.

    Raises:
        ValueError: when ``model_pool_strategy`` is not a recognized
            strategy name.
    """
    del spec  # reserved for future policies that need agent metadata
    if not team_spec.model_pool:
        return None
    strategy = team_spec.model_pool_strategy
    if strategy == "round_robin":
        return RoundRobinModelAllocator(team_spec.model_pool)
    if strategy == "by_model_name":
        return ByModelNameAllocator(team_spec.model_pool)
    raise ValueError(
        f"Unknown model_pool_strategy '{strategy}'; "
        f"expected one of: round_robin, by_model_name"
    )


__all__ = [
    "ByModelNameAllocator",
    "ModelAllocator",
    "RoundRobinModelAllocator",
    "build_model_allocator",
]
