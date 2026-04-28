# coding: utf-8
"""Internal runtime owner for TeamAgent sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Optional,
    TYPE_CHECKING,
)

from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    TeamDatabase,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import (
    Session as AgentTeamSession,
    create_agent_team_session,
)
from openjiuwen.core.session.checkpointer import CheckpointerFactory

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent


@dataclass(slots=True)
class TeamRuntimeActivation:
    """Resolved team runtime and activation metadata."""

    agent: "TeamAgent"
    session: AgentTeamSession
    activation_kind: Optional[str]


@dataclass(slots=True)
class TeamSessionResolution:
    """Checkpoint resolution for a target team session."""

    kind: str
    reason: Optional[str] = None


class TeamRuntimeManager:
    """Owns the in-process active TeamAgent runtime."""

    def __init__(self) -> None:
        self._active_team_name: Optional[str] = None
        self._active_session_id: Optional[str] = None
        self._active_agent: Optional["TeamAgent"] = None
        self._active_paused: bool = False

    @property
    def active_team_name(self) -> Optional[str]:
        return self._active_team_name

    @property
    def active_session_id(self) -> Optional[str]:
        return self._active_session_id

    @property
    def active_agent(self) -> Optional["TeamAgent"]:
        return self._active_agent

    async def activate(
        self,
        spec: "TeamAgentSpec",
        session: str | AgentTeamSession | None,
        inputs: object = None,
    ) -> TeamRuntimeActivation:
        """Resolve the TeamAgent to run for the target team/session."""
        team_session = TeamRuntimeManager._build_session(spec, session)
        session_id = team_session.get_session_id()
        team_name = spec.team_name

        if self._active_agent is not None and self._active_team_name == team_name:
            if self._active_session_id == session_id:
                if self._active_paused:
                    await team_session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
                    self._active_paused = False
                    return TeamRuntimeActivation(
                        agent=self._active_agent,
                        session=team_session,
                        activation_kind="resume_paused",
                    )
                team_logger.warning(
                    "run_agent_team_streaming called with active team/session "
                    "({}, {}); prefer interact_agent_team for same-session follow-up",
                    team_name,
                    session_id,
                )
                return TeamRuntimeActivation(
                    agent=self._active_agent,
                    session=team_session,
                    activation_kind="same_session",
                )

            session_resolution = await self._resolve_session_checkpoint(team_session, team_name)
            if session_resolution.kind == "recoverable":
                from openjiuwen.agent_teams.factory import recover_for_existing_session

                await recover_for_existing_session(self._active_agent, team_session)
                self._set_active(team_name, session_id, self._active_agent)
                return TeamRuntimeActivation(
                    agent=self._active_agent,
                    session=team_session,
                    activation_kind="recover",
                )
            if session_resolution.kind == "invalid":
                team_logger.warning(
                    "Refusing to resume team {} on existing invalid session {}: {}",
                    team_name,
                    session_id,
                    session_resolution.reason or "unknown checkpoint mismatch",
                )
                return TeamRuntimeActivation(
                    agent=self._active_agent,
                    session=team_session,
                    activation_kind="invalid_session",
                )

            from openjiuwen.agent_teams.factory import resume_persistent_team

            await team_session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            await resume_persistent_team(self._active_agent, team_session)
            self._set_active(team_name, session_id, self._active_agent)
            return TeamRuntimeActivation(
                agent=self._active_agent,
                session=team_session,
                activation_kind="resume",
            )

        if self._active_agent is not None:
            await self._deactivate_active_runtime()

        session_resolution = await self._resolve_session_checkpoint(team_session, team_name)
        if session_resolution.kind == "recoverable":
            from openjiuwen.agent_teams.factory import recover_agent_team

            agent = await recover_agent_team(team_session)
            activation_kind = "recover"
        elif session_resolution.kind == "invalid":
            team_logger.warning(
                "Refusing to create team {} on existing invalid session {}: {}",
                team_name,
                session_id,
                session_resolution.reason or "unknown checkpoint mismatch",
            )
            return TeamRuntimeActivation(
                agent=self._active_agent,
                session=team_session,
                activation_kind="invalid_session",
            )
        else:
            await team_session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            agent = spec.build()
            activation_kind = "create"

        self._set_active(team_name, session_id, agent)
        return TeamRuntimeActivation(agent=agent, session=team_session, activation_kind=activation_kind)

    async def pause(
        self,
        *,
        team_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Pause the current active team runtime."""
        if not self._matches_active(team_name=team_name, session_id=session_id):
            return False
        if self._active_agent is None:
            return False
        await self._active_agent.pause_coordination()
        self._active_paused = True
        return True

    async def interact(
        self,
        user_input: str,
        *,
        team_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Deliver user input to the current active team runtime."""
        if not self._matches_active(team_name=team_name, session_id=session_id):
            return False
        if self._active_agent is None:
            return False
        await self._active_agent.interact(user_input)
        return True

    async def delete_team(
        self,
        team_name: str,
        session_ids: list[str],
        *,
        db_config: Optional[DatabaseConfig] = None,
    ) -> bool:
        """Delete team runtime state, checkpoints, and persisted team metadata."""
        if self._active_team_name == team_name:
            await self._deactivate_active_runtime()
            self._clear_active()

        checkpointer = CheckpointerFactory.get_checkpointer()
        for session_id in session_ids:
            await checkpointer.release(session_id)

        database = TeamDatabase(db_config or DatabaseConfig())
        await database.initialize()
        try:
            return await database.delete_team(team_name)
        finally:
            await database.close()

    def clear_if_matches(self, *, team_name: str, session_id: str) -> None:
        """Drop active pointers when the current runtime is no longer valid."""
        if self._active_team_name == team_name and self._active_session_id == session_id:
            self._clear_active()

    @staticmethod
    def _build_session(
        spec: "TeamAgentSpec",
        session: str | AgentTeamSession | None,
    ) -> AgentTeamSession:
        if isinstance(session, AgentTeamSession):
            return session
        if isinstance(session, str):
            return create_agent_team_session(session_id=session, team_id=spec.team_name)
        return create_agent_team_session(team_id=spec.team_name)

    async def _resolve_session_checkpoint(
        self,
        session: AgentTeamSession,
        team_name: str,
    ) -> TeamSessionResolution:
        checkpointer = CheckpointerFactory.get_checkpointer()
        if not await checkpointer.session_exists(session.get_session_id()):
            await session.pre_run()
            return TeamSessionResolution(kind="missing")

        await session.pre_run()
        checkpoint_team_name = session.get_state("team_name")
        if checkpoint_team_name is None:
            return TeamSessionResolution(
                kind="invalid",
                reason="checkpoint has no persisted team_name",
            )
        if checkpoint_team_name != team_name:
            return TeamSessionResolution(
                kind="invalid",
                reason=f"checkpoint team_name={checkpoint_team_name!r}",
            )
        return TeamSessionResolution(kind="recoverable")

    async def _deactivate_active_runtime(self) -> None:
        if self._active_agent is None:
            return
        try:
            await self._active_agent.stop_coordination()
        except Exception as exc:
            team_logger.warning("Failed to stop active team runtime: {}", exc)

    def _matches_active(
        self,
        *,
        team_name: Optional[str],
        session_id: Optional[str],
    ) -> bool:
        if self._active_agent is None:
            return False
        if team_name is not None and team_name != self._active_team_name:
            return False
        if session_id is not None and session_id != self._active_session_id:
            return False
        return True

    def _set_active(self, team_name: str, session_id: str, agent: "TeamAgent") -> None:
        self._active_team_name = team_name
        self._active_session_id = session_id
        self._active_agent = agent
        self._active_paused = False

    def _clear_active(self) -> None:
        self._active_team_name = None
        self._active_session_id = None
        self._active_agent = None
        self._active_paused = False
