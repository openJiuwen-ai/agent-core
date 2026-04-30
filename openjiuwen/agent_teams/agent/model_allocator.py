# coding: utf-8
"""Model config allocators for team member spawning.

When ``TeamSpec.model_pool`` is non-empty, an allocator distributes pool
entries across leader and teammates so concurrent calls spread across
endpoints instead of saturating a single one. Two strategies ship:

* ``RoundRobinModelAllocator`` — linear rotation through every entry,
  ignoring ``model_name``. Good for pools where every entry is an
  interchangeable endpoint of the same logical model. Always allocates.
* ``ByModelNameAllocator`` — looks up the named group and round-robins
  within it. The caller must pass ``model_name``; the allocator returns
  ``None`` when the name is missing or absent from the pool, in which
  case the caller falls back to its per-agent model.

Identity model: every assignment is referenced as
``(model_name, group_index)`` — the entry's position within its
same-name group in the pool at allocation time. The DB persists only
this lightweight reference; the live config (credentials, endpoint
URL, request knobs) is rehydrated from the in-session pool via
``resolve_member_model``. Pool updates therefore reach all members on
their next resolution without DB writes.

When the pool is empty no allocator is built — every member resolves
its model from ``TeamAgentSpec.agents`` via the regular per-agent
fallback in ``TeamAgent._setup_agent``.

Custom allocation strategies (weighted, least-recently-used, ...) only
need to satisfy the ``ModelAllocator`` protocol.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
    from openjiuwen.agent_teams.schema.team import ModelPoolEntry, TeamSpec


@dataclass(frozen=True, slots=True)
class Allocation:
    """One allocator result.

    Carries the picked pool entry plus the position needed to persist a
    DB reference. Helpers materialize the runtime config and the DB ref
    so call sites never touch the entry directly.
    """

    entry: "ModelPoolEntry"
    group_index: int

    def to_team_model_config(self) -> "TeamModelConfig":
        """Materialize the live ``TeamModelConfig`` for runtime use."""
        return self.entry.to_team_model_config()

    def to_db_ref(self) -> dict:
        """Produce the lightweight ``{model_name, model_index}`` ref for DB persistence."""
        return {"model_name": self.entry.model_name, "model_index": self.group_index}


@runtime_checkable
class ModelAllocator(Protocol):
    """Allocates one pool entry per call.

    Implementations encapsulate the policy for picking the next entry
    (round-robin, weighted, least-recently-used, ...). Returning
    ``None`` signals "no entry available" — callers fall back to the
    member's per-agent model config.

    Allocators must also expose ``state_dict`` / ``load_state_dict`` so
    rotation counters survive full-restart recovery. The pool itself
    lives on ``TeamSpec`` and is rebuilt from there on recovery; only
    the volatile counters and a pool digest need to ride along inside
    the session. ``load_state_dict`` automatically resets counters
    when the persisted digest no longer matches the current pool, so a
    pool composition change between save and load doesn't carry stale
    indexes into the new layout.
    """

    def allocate(self, model_name: Optional[str] = None) -> Optional[Allocation]:
        """Return the next allocation, or None when unavailable.

        Args:
            model_name: Optional model-name hint. Allocators that
                select by name (``ByModelNameAllocator``) require it
                and return ``None`` when missing or unknown. Allocators
                that ignore name (``RoundRobinModelAllocator``) accept
                it for signature compatibility and discard it.
        """
        ...

    def state_dict(self) -> dict:
        """Return a JSON-friendly snapshot of allocator counters.

        The returned dict must round-trip through ``json.dumps`` /
        ``json.loads`` and through ``load_state_dict`` of the same
        allocator class so a freshly-built allocator can resume the
        previous rotation.
        """
        ...

    def load_state_dict(self, state: dict) -> None:
        """Restore counters previously produced by ``state_dict``.

        Implementations should be defensive: tolerate missing keys
        (resume from zero), unknown keys (ignore), and pool-composition
        changes between save and load (skip stale group entries, leave
        new ones at zero, optionally reset everything when the pool
        digest differs).
        """
        ...


def _pool_digest(pool: list["ModelPoolEntry"]) -> str:
    """Stable digest of a pool's structural shape.

    Captures (model_name, api_base_url) per entry in order. Changes to
    credentials or metadata don't bump the digest — those refresh
    in-place without invalidating allocator counters. Reordering or
    add/remove of entries does change the digest, triggering a counter
    reset on the next ``load_state_dict``.
    """
    h = hashlib.sha1(usedforsecurity=False)
    for entry in pool:
        h.update(entry.model_name.encode("utf-8"))
        h.update(b"\x00")
        h.update(entry.api_base_url.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def _group_index_of(entry: "ModelPoolEntry", group: list["ModelPoolEntry"]) -> int:
    """Return ``entry``'s position within ``group`` by reference identity."""
    for i, candidate in enumerate(group):
        if candidate is entry:
            return i
    return 0


class RoundRobinModelAllocator:
    """Round-robin allocator over a ``TeamSpec.model_pool``.

    Each call to ``allocate`` returns the next entry in pool order and
    wraps when the end is reached, so a team with N members and M pool
    entries spreads members evenly across endpoints.
    """

    def __init__(self, pool: list["ModelPoolEntry"]) -> None:
        """Initialize with the pool entries to rotate over."""
        self._pool = list(pool)
        self._pool_digest = _pool_digest(self._pool)
        self._index = 0
        # Pre-compute name → list-of-entries so group_index lookups
        # don't rescan the pool on every allocation.
        self._groups: dict[str, list["ModelPoolEntry"]] = {}
        for entry in self._pool:
            self._groups.setdefault(entry.model_name, []).append(entry)

    def allocate(self, model_name: Optional[str] = None) -> Optional[Allocation]:
        """Return the next pool entry as an Allocation.

        ``model_name`` is accepted for protocol compatibility but
        ignored — round-robin is name-agnostic and rotates across
        every entry in pool order.
        """
        del model_name  # round-robin is name-agnostic
        if not self._pool:
            return None
        entry = self._pool[self._index % len(self._pool)]
        self._index += 1
        group = self._groups.get(entry.model_name) or [entry]
        return Allocation(entry=entry, group_index=_group_index_of(entry, group))

    def state_dict(self) -> dict:
        """Snapshot counter + pool digest for session persistence."""
        return {"index": self._index, "pool_digest": self._pool_digest}

    def load_state_dict(self, state: dict) -> None:
        """Restore counter, resetting if the persisted digest mismatches."""
        if state.get("pool_digest") != self._pool_digest:
            self._index = 0
            return
        try:
            self._index = int(state.get("index", 0))
        except (TypeError, ValueError):
            self._index = 0


class ByModelNameAllocator:
    """Lookup-by-name allocator with intra-group round-robin.

    Pool entries are partitioned by their ``model_name`` field while
    preserving insertion order. Each ``allocate(model_name=...)`` call
    looks up the named group and returns the next endpoint from that
    group's round-robin counter. The caller is responsible for picking
    which model name a member should use (via ``LeaderSpec.model_name``,
    ``TeamMemberSpec.model_name``, or the ``model_name`` parameter on
    ``spawn_member``).

    Returning ``None`` when the requested name is missing or unknown
    keeps the fallback chain in ``TeamAgent._setup_agent`` intact: the
    member then resolves its model from its per-agent spec instead.
    """

    def __init__(self, pool: list["ModelPoolEntry"]) -> None:
        """Initialize from the pool, partitioning entries by model_name."""
        self._groups: dict[str, list["ModelPoolEntry"]] = {}
        for entry in pool:
            self._groups.setdefault(entry.model_name, []).append(entry)
        self._pool_digest = _pool_digest(list(pool))
        self._inner_indexes: dict[str, int] = {name: 0 for name in self._groups}

    def allocate(self, model_name: Optional[str] = None) -> Optional[Allocation]:
        """Return the next entry in the requested name's group.

        Args:
            model_name: Group key to look up. Required — a missing or
                unknown name yields ``None`` so callers can fall back
                to their per-agent model.
        """
        if not model_name or model_name not in self._groups:
            return None
        group = self._groups[model_name]
        idx = self._inner_indexes[model_name] % len(group)
        self._inner_indexes[model_name] += 1
        return Allocation(entry=group[idx], group_index=idx)

    def state_dict(self) -> dict:
        """Snapshot per-group counters + pool digest for session persistence."""
        return {
            "inner_indexes": dict(self._inner_indexes),
            "pool_digest": self._pool_digest,
        }

    def load_state_dict(self, state: dict) -> None:
        """Restore counters; reset all to zero on pool-digest mismatch.

        A different pool digest means the persisted counters refer to a
        layout that no longer matches reality. Returning to zero is the
        only safe default — keeping stale counters would bias the new
        rotation.
        """
        if state.get("pool_digest") != self._pool_digest:
            self._inner_indexes = {name: 0 for name in self._groups}
            return
        inner = state.get("inner_indexes") or {}
        if not isinstance(inner, dict):
            return
        for name, raw in inner.items():
            if name not in self._inner_indexes:
                continue
            try:
                self._inner_indexes[name] = int(raw)
            except (TypeError, ValueError):
                self._inner_indexes[name] = 0


def resolve_member_model(
    team_spec: "TeamSpec",
    *,
    model_name: Optional[str],
    model_index: Optional[int],
) -> Optional["TeamModelConfig"]:
    """Resolve a member's model from a stored reference against the live pool.

    Pure positional lookup — does NOT touch the allocator and does NOT
    advance any rotation counter. Resolution order:

    1. Group for ``model_name`` exists, ``model_index`` is in range →
       return that entry's config (picks up any credential / endpoint
       refresh because the entry is read live from the session pool).
    2. Group exists but ``model_index`` is out of range (group shrank)
       → return entry at index ``0`` for a deterministic fallback.
    3. Group missing or pool empty → ``None`` so the caller falls back
       to the per-agent model declared in ``TeamAgentSpec.agents``.

    Args:
        team_spec: Resolved team identity carrying the current pool.
        model_name: Reference produced at spawn time via
            ``Allocation.to_db_ref``.
        model_index: Reference produced at spawn time via
            ``Allocation.to_db_ref``.

    Returns:
        A live ``TeamModelConfig`` when the reference can be resolved
        against the current pool, otherwise ``None``.
    """
    if not team_spec.model_pool or not model_name:
        return None
    group = [e for e in team_spec.model_pool if e.model_name == model_name]
    if not group:
        return None
    idx = model_index if isinstance(model_index, int) and 0 <= model_index < len(group) else 0
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
    "Allocation",
    "ByModelNameAllocator",
    "ModelAllocator",
    "RoundRobinModelAllocator",
    "build_model_allocator",
    "resolve_member_model",
]
