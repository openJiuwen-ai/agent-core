# coding: utf-8
"""Internal runtime owner for TeamAgent sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Optional,
    TYPE_CHECKING,
)

from openjiuwen.agent_teams.tools.database import DatabaseConfig
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


@dataclass(slots=True)
class TeamSessionMetadata:
    """Resolved metadata for an agent team session."""

    team_name: str
    db_config: DatabaseConfig


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
    ) -> bool:
        """Delete team runtime state, checkpoints, and persisted team metadata."""
        if self._active_team_name == team_name:
            await self._deactivate_active_runtime()
            self._clear_active()

        # Resolve db_config from session state BEFORE releasing checkpoint
        db_config = None
        if session_ids:
            metadata = await self.resolve_team_session_metadata(session_ids[0])
            if metadata is None:
                raise RuntimeError(
                    f"Cannot resolve team session metadata for {session_ids[0]}, "
                    f"aborting delete_team"
                )
            db_config = metadata.db_config

        # Get shared db and drop dynamic tables for each session
        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db

        db = get_shared_db(db_config)
        await db.initialize()
        for session_id in session_ids:
            await db.drop_session_tables_by_id(session_id)

        # Release checkpoints
        checkpointer = CheckpointerFactory.get_checkpointer()
        for session_id in session_ids:
            await checkpointer.release(session_id)

        # Delete team row from static table
        return await db.team.delete_team(team_name)

    @staticmethod
    async def resolve_team_session_metadata(session_id: str) -> Optional[TeamSessionMetadata]:
        """Resolve metadata for an agent team session.

        Returns None if the session is not an agent team session (missing team_name,
        context, or spec). Returns TeamSessionMetadata if it is a valid team session.

        Raises RuntimeError if session appears to be team session but db_config
        cannot be resolved - this prevents cleaning wrong database.

        Args:
            session_id: Session identifier to resolve.

        Returns:
            TeamSessionMetadata if valid agent team session, None otherwise.

        Raises:
            RuntimeError: If session has team_name/context/spec but db_config cannot be obtained.
        """
        if not session_id:
            return None

        session = create_agent_team_session(session_id=session_id)
        try:
            await session.pre_run()
        except Exception as e:
            team_logger.warning("Failed to restore session state for %s: %s", session_id, e)
            return None

        state = session.get_state()
        if not isinstance(state, dict):
            return None

        # Check if this is an agent team session (must have team_name, context, and spec)
        # agent_teams always persists these three together in recovery_manager
        team_name = state.get("team_name")
        context_data = state.get("context")
        spec_data = state.get("spec")
        if team_name is None or context_data is None or spec_data is None:
            return None

        # Validate spec has a valid team_name to confirm it's a TeamAgentSpec-like structure
        from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
        try:
            spec = TeamAgentSpec.model_validate(spec_data)
            if spec.team_name != team_name:
                team_logger.warning(
                    "Session %s has mismatched team_name in spec vs state: %s vs %s",
                    session_id,
                    spec.team_name,
                    team_name,
                )
                raise RuntimeError(
                    f"Session {session_id} has mismatched team_name: spec={spec.team_name}, state={team_name}"
                )
        except Exception as e:
            team_logger.warning("Failed to parse spec for session %s: %s", session_id, e)
            raise RuntimeError(
                f"Session {session_id} appears to be team session (team_name={team_name}) "
                f"but spec parsing failed: {e}"
            ) from e

        # Parse context to get db_config
        from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
        try:
            context = TeamRuntimeContext.model_validate(context_data)
            db_config = context.db_config
        except Exception as e:
            team_logger.warning("Failed to parse context for session %s: %s", session_id, e)
            raise RuntimeError(
                f"Session {session_id} is team session (team_name={team_name}) "
                f"but context parsing failed: {e}"
            ) from e

        if db_config is None:
            raise RuntimeError(
                f"Session {session_id} is team session (team_name={team_name}) "
                f"but db_config is missing"
            )

        return TeamSessionMetadata(team_name=team_name, db_config=db_config)

    async def release_session(self, session_id: str) -> None:
        """Release per-session dynamic tables for an agent team session.

        If the session is currently active, stops the coordination loop first.
        Then restores the session state from checkpointer to obtain the db_config,
        and drops the dynamic tables (tasks, messages, etc.) for that session.

        Raises on failure to ensure caller does not proceed with checkpoint release.

        Args:
            session_id: Session identifier to clean up.

        Raises:
            RuntimeError: If db_config cannot be obtained from session state.
        """
        if not session_id:
            return

        # If releasing the currently active session, stop coordination and clear
        if self._active_session_id == session_id:
            await self._deactivate_active_runtime()
            self._clear_active()

        # Resolve metadata - raises RuntimeError if team session but db_config missing
        metadata = await self.resolve_team_session_metadata(session_id)
        if metadata is None:
            raise RuntimeError(f"Cannot resolve team session metadata for {session_id}")

        # Get shared database instance and drop session tables
        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db

        db = get_shared_db(metadata.db_config)
        await db.initialize()
        await db.drop_session_tables_by_id(session_id)

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

    @staticmethod
    async def _resolve_session_checkpoint(
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
