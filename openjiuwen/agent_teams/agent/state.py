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

import asyncio
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


def _empty_task_set() -> "set[asyncio.Task]":
    """Default factory for the swarmflow background-task registry."""
    return set()


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
    # ``StreamController._run_one_round``'s finally block reads it as the
    # highest-priority terminal condition so a TEMPORARY-team leader closes
    # its stream after the round that cleaned the team instead of hanging on
    # the ``None`` sentinel forever. Cross-operator (written from the
    # tool/clean path, read from the stream round-end path), so it belongs
    # in TeamAgentState per the four-quadrant rule.
    team_cleaned: bool = False

    # Swarmflow spectator sub-mode. ``swarmflow_active`` is raised by the
    # ``swarmflow()`` tool while a workflow runs and read by ``TeamPolicyRail``
    # to inject the spectator prompt section (the leader narrates phase
    # progress instead of coordinating). ``swarmflow_tasks`` holds the
    # in-flight background run task(s) so they are not garbage-collected mid
    # run and can be cancelled on teardown. Cross-operator (written from the
    # tool path, read from the rail / teardown path), so both belong here.
    swarmflow_active: bool = False
    swarmflow_tasks: "set[asyncio.Task]" = field(default_factory=_empty_task_set)


__all__ = ["TeamAgentState"]
