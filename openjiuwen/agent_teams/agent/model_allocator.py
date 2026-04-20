# coding: utf-8
"""Round-robin model config allocator for teammate spawning."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig


class ModelAllocator:
    """Allocates teammate model configs in round-robin order.

    Extracts all non-leader keys from ``TeamAgentSpec.agents``,
    sorts them, and hands out the corresponding ``TeamModelConfig``
    on each ``allocate()`` call.
    """

    def __init__(self, spec: TeamAgentSpec) -> None:
        self._spec = spec
        self._keys = sorted(k for k in spec.agents if k != "leader")
        self._index = 0

    def allocate(self) -> Optional[TeamModelConfig]:
        """Return the next TeamModelConfig, or None if empty."""
        if not self._keys:
            return None
        key = self._keys[self._index % len(self._keys)]
        self._index += 1
        agent_spec = self._spec.agents.get(key)
        if agent_spec and agent_spec.model:
            return agent_spec.model
        return None
