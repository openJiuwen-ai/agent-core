# coding: utf-8
"""Factory for creating TeamAgent instances."""
from __future__ import annotations

from typing import Optional, Union

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
from openjiuwen.agent_teams.worktree.models import WorktreeConfig
from openjiuwen.core.session.agent_team import Session as AgentTeamSession


def create_agent_team(
    agents: dict[str, DeepAgentSpec],
    *,
    team_name: str = "agent_team",
    lifecycle: str = "temporary",
    teammate_mode: str = "build_mode",
    spawn_mode: str = "process",
    leader: Optional[LeaderSpec] = None,
    predefined_members: list[TeamMemberSpec] | None = None,
    transport: Optional[TransportSpec] = None,
    storage: Optional[StorageSpec] = None,
    worktree: Optional[WorktreeConfig] = None,
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
            "build_mode" (complete tasks directly) or "plan_mode"
            (require leader approval). Defaults to "build_mode".
        spawn_mode: How teammates are launched — "process" (child
            subprocess) or "inprocess" (asyncio coroutine in the
            same event loop).
        leader: Leader identity specification (persona, etc.).
        predefined_members: Pre-configured team members. When provided,
            leader skips ``spawn_member`` and ``build_team`` registers
            all members automatically.
        transport: Pluggable transport layer for inter-agent messaging.
        storage: Pluggable storage layer for task/state persistence.
        worktree: Optional worktree isolation config for team members.
        metadata: Arbitrary metadata attached to the team config.
    """
    spec = TeamAgentSpec(
        agents=agents,
        team_name=team_name,
        lifecycle=lifecycle,
        teammate_mode=teammate_mode,
        spawn_mode=spawn_mode,
        leader=leader or LeaderSpec(),
        predefined_members=predefined_members or [],
        transport=transport,
        storage=storage,
        worktree=worktree,
        metadata=metadata or {},
    )
    return spec.build()


async def recover_agent_team(
    session: Union[str, AgentTeamSession],
    db_config: Optional[DatabaseConfig] = None,
) -> TeamAgent:
    """Recover a leader TeamAgent after full team restart.

    Restores the leader from persisted session state, then re-launches
    all non-shutdown teammates from the database.

    Requires PersistenceCheckpointer to be configured so that session
    state survives across process restarts.

    Args:
        session: The original session ID from the previous run or an
            existing prepared team session.
        db_config: Database config (used only if session state is unavailable).
    """
    if isinstance(session, str):
        from openjiuwen.core.session.agent_team import create_agent_team_session

        session = create_agent_team_session(session_id=session)
        await session.pre_run()  # triggers checkpointer.recover()

    agent = TeamAgent.recover_from_session(session)
    await agent.recover_team()
    return agent


async def resume_persistent_team(
    agent: TeamAgent,
    session: Union[str, AgentTeamSession],
) -> TeamAgent:
    """Resume a persistent team in a new session.

    Creates a fresh session, initializes new dynamic tables for
    tasks and messages, and returns the same agent ready for a
    new ``invoke()`` / ``stream()`` call.

    Args:
        agent: A configured persistent-team leader that has
            completed at least one round.
        session: Session ID for the new round or an existing prepared
            team session.
    """
    if isinstance(session, str):
        from openjiuwen.core.session.agent_team import create_agent_team_session

        session = create_agent_team_session(session_id=session)
        await session.pre_run()

    await agent.resume_for_new_session(session)
    return agent


async def recover_for_existing_session(
    agent: TeamAgent,
    session: Union[str, AgentTeamSession],
) -> TeamAgent:
    """Recover an existing session on a running TeamAgent.

    Reuses the current agent and restores the session state from checkpoint.
    This avoids creating a new agent when switching between sessions of the
    same team while the leader process is still running.

    Args:
        agent: A running TeamAgent (leader) that belongs to the same team.
        session: Session ID for the existing session or an existing prepared
            team session (must have checkpoint).
    """
    if isinstance(session, str):
        from openjiuwen.core.session.agent_team import create_agent_team_session

        session = create_agent_team_session(session_id=session)
        await session.pre_run()

    await agent.recover_for_existing_session(session)
    return agent


__all__ = [
    "create_agent_team",
    "recover_agent_team",
    "recover_for_existing_session",
    "resume_persistent_team",
]
