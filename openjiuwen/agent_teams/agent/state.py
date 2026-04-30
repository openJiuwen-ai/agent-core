# coding: utf-8
"""TeamAgent mutable runtime state.

The second quadrant of the four-quadrant TeamAgent decomposition:
runtime-mutable values shared across operators. Operator-internal state
(e.g. SpawnManager.spawned_handles, CoordinationManager.subscribed_topics)
stays inside the owning operator — only put values here when they cross
operator boundaries.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from typing import (
    TYPE_CHECKING,
    Optional,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.member import TeamMember
    from openjiuwen.core.session.agent_team import Session as AgentTeamSession


def _empty_listener_list() -> list:
    """Default factory for the event listener registry."""
    return []


@dataclass
class TeamAgentState:
    """Mutable runtime state shared across TeamAgent operators."""

    session_id: Optional[str] = None
    team_session: Optional["AgentTeamSession"] = None
    team_member: Optional["TeamMember"] = None
    pending_user_query: str = ""
    event_listeners: list = field(default_factory=_empty_listener_list)


__all__ = ["TeamAgentState"]
