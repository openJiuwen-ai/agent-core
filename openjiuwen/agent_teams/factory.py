# coding: utf-8
"""Factory for creating TeamAgent instances."""
from __future__ import annotations

from typing import Optional

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    LeaderSpec,
    StorageSpec,
    TeamAgentSpec,
    TransportSpec,
)
from openjiuwen.agent_teams.tools.database import DatabaseConfig


def create_agent_team(
    agents: dict[str, DeepAgentSpec],
    *,
    team_name: str = "agent_team",
    objective: str = "Coordinate a multi-agent task",
    lifecycle: str = "temporary",
    teammate_mode: str = "plan_mode",
    leader: Optional[LeaderSpec] = None,
    transport: Optional[TransportSpec] = None,
    storage: Optional[StorageSpec] = None,
    metadata: Optional[dict] = None,
) -> TeamAgent:
    """Create a leader-configured TeamAgent.

    Args:
        agents: Per-role DeepAgentSpec configs. Keys are role names
            ("leader", "teammate"). The "leader" key is required;
            "teammate" is optional and falls back to the leader model.
        team_name: Name of the agent team.
        objective: High-level objective for team coordination.
        lifecycle: Team lifecycle mode — "temporary" (disband after
            completion) or "persistent" (retain team across sessions).
        teammate_mode: Default execution mode for spawned teammates —
            "plan_mode" (require leader approval) or "build_mode"
            (complete tasks directly).
        leader: Leader identity specification (persona, domain, etc.).
        transport: Pluggable transport layer for inter-agent messaging.
        storage: Pluggable storage layer for task/state persistence.
        metadata: Arbitrary metadata attached to the team config.
    """
    spec = TeamAgentSpec(
        agents=agents,
        team_name=team_name,
        objective=objective,
        lifecycle=lifecycle,
        teammate_mode=teammate_mode,
        leader=leader or LeaderSpec(),
        transport=transport,
        storage=storage,
        metadata=metadata or {},
    )
    return spec.build()


async def recover_agent_team(session_id: str, db_config: Optional[DatabaseConfig] = None) -> TeamAgent:
    """Recover a leader TeamAgent after full team restart.

    Restores the leader from persisted session state, then re-launches
    all non-shutdown teammates from the database.

    Requires PersistenceCheckpointer to be configured so that session
    state survives across process restarts.

    Args:
        session_id: The original session ID from the previous run.
        db_config: Database config (used only if session state is unavailable).
    """
    from openjiuwen.core.session.agent_team import create_agent_team_session

    session = create_agent_team_session(session_id=session_id)
    await session.pre_run()  # triggers checkpointer.recover()
    agent = TeamAgent.recover_from_session(session)
    await agent.recover_team()
    return agent


__all__ = ["create_agent_team", "recover_agent_team"]
