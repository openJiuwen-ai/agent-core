# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

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
    """Mutable runtime state shared across TeamAgent operators.

    Note: session_id is intentionally NOT a field here. The single source of
    truth for the current session_id is the agent_teams contextvar exposed by
    ``openjiuwen.agent_teams.context.get_session_id``. Holding a cached string
    on the state would re-introduce the "two truths" problem that previously
    required complex Token bookkeeping in SessionManager.
    """

    team_session: Optional["AgentTeamSession"] = None
    team_member: Optional["TeamMember"] = None
    pending_user_query: str = ""
    event_listeners: list = field(default_factory=_empty_listener_list)

    # One-shot latch raised on the ``clean_team`` success path, wired via
    # ``TeamBackend.on_team_cleaned`` -> ``TeamAgent._mark_team_cleaned``.
    # ``StreamController._on_idle_settled`` reads it as the
    # highest-priority terminal condition so a TEMPORARY-team leader closes
    # its stream after the round that cleaned the team instead of hanging on
    # the ``None`` sentinel forever. Cross-operator (written from the
    # tool/clean path, read from the stream round-end path), so it belongs
    # in TeamAgentState per the four-quadrant rule.
    team_cleaned: bool = False

    # One-shot latch raised when the leader's ``build_team`` tool call fails,
    # wired via ``TeamBackend.on_team_build_failed`` ->
    # ``TeamAgent._note_team_build_failed``. The tool boundary digests the
    # exception into an LLM-facing tool message, so without this latch a
    # failed build ends the round "normally" for a team that does not exist
    # . ``StreamController._on_idle_settled`` reads (and
    # clears) it at round-chain end to emit a terminal task_failed chunk —
    # surfaced downstream as chat.error. ``_mark_team_built`` clears it on a
    # successful (re)build so a retry suppresses the report. Cross-operator
    # (written from the tool/build path, read from the stream round-end
    # path), so it belongs in TeamAgentState per the four-quadrant rule.
    team_build_error: Optional[str] = None

    # ``time.monotonic()`` stamp of the moment this member last settled into
    # runtime IDLE (MemberStatus.READY); ``None`` while it is mid-round
    # (BUSY) or has never settled. Written by ``StreamController._map_state``
    # on the READY/BUSY edge, read via ``TeamAgent.idle_seconds()`` by the
    # coordination stale-task sweep — cross-operator, hence a state field.
    #
    # Deliberately process-local, in-memory and NOT persisted, and NOT
    # derived from the database ``task.updated_at``: pausing a team freezes
    # ``updated_at`` while the wall clock keeps running, so any
    # ``now - updated_at`` staleness measure reports a huge false stall right
    # after a long pause -> resume. A monotonic stamp taken at idle-entry
    # measures only time the member actually spent idle *while running*, and
    # ``TeamAgent.refresh_idle_baseline()`` re-bases it on the resume path so
    # the pause window itself never counts.
    idle_since: Optional[float] = None


__all__ = ["TeamAgentState"]
