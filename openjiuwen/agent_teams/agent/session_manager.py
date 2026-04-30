# coding: utf-8
"""Session lifecycle and persistence for TeamAgent."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Optional,
)

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.session.agent_team import Session as AgentTeamSession

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
    from openjiuwen.agent_teams.agent.recovery_manager import RecoveryManager


class SessionManager:
    """Manages session lifecycle and persistence.

    Responsibilities:
    - Session ID management
    - Team session persistence
    - Session recovery
    """

    def __init__(
        self,
        configurator: AgentConfigurator,
        recovery_manager: RecoveryManager,
    ):
        self._configurator = configurator
        self._recovery_manager = recovery_manager

        self._session_id: Optional[str] = None
        self._team_session: Optional[AgentTeamSession] = None

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @session_id.setter
    def session_id(self, value: Optional[str]) -> None:
        self._session_id = value

    @property
    def team_session(self) -> Optional[AgentTeamSession]:
        return self._team_session

    @team_session.setter
    def team_session(self, value: Optional[AgentTeamSession]) -> None:
        self._team_session = value

    async def _switch_session(self, session) -> None:
        """Apply the new session id, contextvar, and per-session DB tables."""
        from openjiuwen.agent_teams.spawn.context import set_session_id

        self._session_id = session.get_session_id()
        set_session_id(self._session_id)
        self._team_session = session if isinstance(session, AgentTeamSession) else None

        team_backend = self._configurator.team_backend
        if team_backend:
            await team_backend.db.create_cur_session_tables()

        spec = self._configurator.spec
        if spec and self._configurator.role == TeamRole.LEADER:
            self._recovery_manager.persist_leader_config(session)

    async def resume_for_new_session(self, session) -> None:
        """Switch to a new session and rebind live teammate runtimes.

        Persistent teams keep team rows and old session data intact across
        sessions; only the live runtime needs rebinding so it picks up the
        new session_id.
        """
        recoverable_members = await self._recovery_manager.collect_live_teammates_for_session_switch()
        await self._switch_session(session)

        team_backend = self._configurator.team_backend
        if self._configurator.role != TeamRole.LEADER or not team_backend:
            return

        await self._recovery_manager.restart_for_session_switch(
            recoverable_members,
            cleanup_first=True,
        )

    async def recover_for_existing_session(self, session) -> None:
        """Rebind to a checkpoint-restored session without cleanup.

        Caller must have already torn down coordination (which already
        cleared the live handles) and validated the checkpoint.
        """
        recoverable_members = await self._recovery_manager.collect_live_teammates_for_session_switch()
        await self._switch_session(session)

        team_backend = self._configurator.team_backend
        if self._configurator.role != TeamRole.LEADER or not team_backend:
            return

        await self._recovery_manager.restart_for_session_switch(
            recoverable_members,
            cleanup_first=False,
        )
