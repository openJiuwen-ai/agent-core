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
from openjiuwen.agent_teams.schema.team import TeamMemberSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig


def create_agent_team(
    agents: dict[str, DeepAgentSpec],
    *,
    team_name: str = "agent_team",
    lifecycle: str = "temporary",
    teammate_mode: str = "plan_mode",
    leader: Optional[LeaderSpec] = None,
    predefined_members: list[TeamMemberSpec] | None = None,
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
        lifecycle: Team lifecycle mode — "temporary" (disband after
            completion) or "persistent" (retain team across sessions).
        teammate_mode: Default execution mode for spawned teammates —
            "plan_mode" (require leader approval) or "build_mode"
            (complete tasks directly).
        leader: Leader identity specification (persona, domain, etc.).
        predefined_members: Pre-configured team members. When provided,
            leader skips ``spawn_member`` and ``build_team`` registers
            all members automatically.
        transport: Pluggable transport layer for inter-agent messaging.
        storage: Pluggable storage layer for task/state persistence.
        metadata: Arbitrary metadata attached to the team config.
    """
    spec = TeamAgentSpec(
        agents=agents,
        team_name=team_name,
        lifecycle=lifecycle,
        teammate_mode=teammate_mode,
        leader=leader or LeaderSpec(),
        predefined_members=predefined_members or [],
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


async def resume_persistent_team(
    agent: TeamAgent,
    new_session_id: str,
) -> TeamAgent:
    """Resume a persistent team in a new session.

    Creates a fresh session, initializes new dynamic tables for
    tasks and messages, and returns the same agent ready for a
    new ``invoke()`` / ``stream()`` call.

    Args:
        agent: A configured persistent-team leader that has
            completed at least one round.
        new_session_id: Session ID for the new round.
    """
    from openjiuwen.core.session.agent_team import create_agent_team_session

    session = create_agent_team_session(session_id=new_session_id)
    await session.pre_run()
    await agent.resume_for_new_session(session)
    return agent


__all__ = ["create_agent_team", "recover_agent_team", "resume_persistent_team"]
