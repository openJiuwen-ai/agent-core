# coding: utf-8
"""Runner-scoped owner of the active TeamAgent runtime.

Holds the in-process single-active-team singleton state and dispatches
between the four recovery paths exposed by ``agent_teams.factory``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import (
    Optional,
    TYPE_CHECKING,
)

from openjiuwen.agent_teams.factory import (
    recover_agent_team,
    recover_for_existing_session,
    resume_persistent_team,
)
from openjiuwen.agent_teams.runtime.metadata import (
    read_team_namespace,
    read_teams_bucket,
)
from openjiuwen.agent_teams.runtime.pool import (
    ActiveTeam,
    RuntimeState,
    TeamRuntimePool,
)
from openjiuwen.agent_teams.tools.database import DatabaseConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import (
    Session as AgentTeamSession,
    create_agent_team_session,
)
from openjiuwen.core.session.checkpointer import CheckpointerFactory

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent


class TeamActivationKind(str, Enum):
    """Outcome of resolving a TeamAgent runtime for a (team, session) pair.

    Inherits from ``str`` so existing payload consumers that compare against
    raw strings (e.g. ``chunk.payload["activation_kind"] == "create"``)
    keep working without conversion.
    """

    CREATE = "create"
    RECOVER = "recover"
    RESUME = "resume"
    RESUME_PAUSED = "resume_paused"
    SAME_SESSION = "same_session"
    INVALID_SESSION = "invalid_session"

    @property
    def is_short_circuit(self) -> bool:
        """Whether the caller should skip invoke / stream after activation."""
        return self in (TeamActivationKind.SAME_SESSION, TeamActivationKind.INVALID_SESSION)


@dataclass(slots=True)
class TeamRuntimeActivation:
    """Resolved team runtime and activation metadata."""

    agent: "TeamAgent"
    session: AgentTeamSession
    activation_kind: Optional[TeamActivationKind]


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
        self._pool: TeamRuntimePool = TeamRuntimePool()

    @property
    def pool(self) -> TeamRuntimePool:
        """Process-local TeamRuntimePool tracking active team runtimes."""
        return self._pool

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
                    await self._pre_run_with_inputs(team_session, inputs)
                    self._active_paused = False
                    return TeamRuntimeActivation(
                        agent=self._active_agent,
                        session=team_session,
                        activation_kind=TeamActivationKind.RESUME_PAUSED,
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
                    activation_kind=TeamActivationKind.SAME_SESSION,
                )

            session_resolution = await self._resolve_session_checkpoint(team_session, team_name)
            if session_resolution.kind == "recoverable":
                await recover_for_existing_session(self._active_agent, team_session)
                await self._set_active(team_name, session_id, self._active_agent)
                return TeamRuntimeActivation(
                    agent=self._active_agent,
                    session=team_session,
                    activation_kind=TeamActivationKind.RECOVER,
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
                    activation_kind=TeamActivationKind.INVALID_SESSION,
                )

            await self._pre_run_with_inputs(team_session, inputs)
            await resume_persistent_team(self._active_agent, team_session)
            await self._set_active(team_name, session_id, self._active_agent)
            return TeamRuntimeActivation(
                agent=self._active_agent,
                session=team_session,
                activation_kind=TeamActivationKind.RESUME,
            )

        if self._active_agent is not None:
            await self._deactivate_active_runtime()

        session_resolution = await self._resolve_session_checkpoint(team_session, team_name)
        if session_resolution.kind == "recoverable":
            agent = await recover_agent_team(team_session, team_name=team_name)
            activation_kind = TeamActivationKind.RECOVER
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
                activation_kind=TeamActivationKind.INVALID_SESSION,
            )
        else:
            await self._pre_run_with_inputs(team_session, inputs)
            agent = spec.build()
            activation_kind = TeamActivationKind.CREATE

        await self._set_active(team_name, session_id, agent)
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
        entry = await self._pool.get(self._active_team_name)
        if entry is not None:
            entry.state = RuntimeState.PAUSED
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

    async def stop_team(
        self,
        *,
        team_name: str,
        session_id: str,
    ) -> bool:
        """Tear down the active TeamAgent runtime for ``(team_name, session_id)``.

        Stops coordination (closes the event bus, shuts down spawned
        teammates) and drops the entry from the pool. Persisted data
        (checkpoint, dynamic tables, team static row) is left intact —
        the next ``run_agent_team_streaming`` for this team goes through
        the cold-recover path.

        Returns ``False`` when the requested ``(team_name, session_id)``
        does not match the currently active runtime; otherwise ``True``.
        """
        if not self._matches_active(team_name=team_name, session_id=session_id):
            return False
        if self._active_agent is None:
            return False
        try:
            await self._active_agent.stop_coordination()
        except Exception as exc:
            team_logger.warning(
                "Failed to stop team {} on session {}: {}",
                team_name,
                session_id,
                exc,
            )
        await self._clear_active()
        return True

    async def delete_team(
        self,
        team_name: str,
        session_ids: list[str],
    ) -> bool:
        """Delete team runtime state, checkpoints, and persisted team metadata.

        Refuses to run while the team has an active runtime in the pool —
        callers must stop_team / pause_team and wait for completion first.
        """
        if await self._pool.has_active(team_name):
            entry = await self._pool.get(team_name)
            active_session = entry.current_session_id if entry else "?"
            raise_error(
                StatusCode.AGENT_TEAM_BUSY_INVALID,
                team_name=team_name,
                session_id=active_session,
                reason="team has an active runtime; stop_team before delete_team",
            )

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

        Returns None if the session has no agent team buckets (i.e. not an
        agent team session). When the session carries one or more team
        buckets, returns metadata derived from the first parseable bucket
        — db_config is shared across teams within a session, so any bucket
        suffices for callers that just need to drop dynamic tables.

        Args:
            session_id: Session identifier to resolve.

        Returns:
            TeamSessionMetadata if at least one valid team bucket exists,
            None otherwise.

        Raises:
            RuntimeError: If a bucket exists but its spec/context cannot be
                parsed, or db_config is missing - this prevents cleaning
                wrong database.
        """
        if not session_id:
            return None

        session = create_agent_team_session(session_id=session_id)
        try:
            await session.pre_run()
        except Exception as e:
            team_logger.warning("Failed to restore session state for %s: %s", session_id, e)
            return None

        teams = read_teams_bucket(session)
        if not teams:
            return None

        # Pick any bucket — db_config is session-wide. Sort for determinism.
        team_name = sorted(teams.keys())[0]
        bucket = teams[team_name]
        spec_data = bucket.get("spec")
        context_data = bucket.get("context")
        if spec_data is None or context_data is None:
            raise RuntimeError(
                f"Session {session_id} has team bucket '{team_name}' "
                f"missing spec or context"
            )

        from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
        from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
        try:
            spec = TeamAgentSpec.model_validate(spec_data)
            if spec.team_name != team_name:
                team_logger.warning(
                    "Session %s bucket key '%s' mismatches spec.team_name '%s'",
                    session_id,
                    team_name,
                    spec.team_name,
                )
        except Exception as e:
            team_logger.warning("Failed to parse spec for session %s: %s", session_id, e)
            raise RuntimeError(
                f"Session {session_id} team bucket '{team_name}' "
                f"spec parsing failed: {e}"
            ) from e

        try:
            context = TeamRuntimeContext.model_validate(context_data)
            db_config = context.db_config
        except Exception as e:
            team_logger.warning("Failed to parse context for session %s: %s", session_id, e)
            raise RuntimeError(
                f"Session {session_id} team bucket '{team_name}' "
                f"context parsing failed: {e}"
            ) from e

        if db_config is None:
            raise RuntimeError(
                f"Session {session_id} team bucket '{team_name}' "
                f"db_config is missing"
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

        # Static precondition: any team active on this session blocks release.
        active_teams = await self._pool.teams_for_session(session_id)
        if active_teams:
            blocked_names = ", ".join(t.team_name for t in active_teams)
            raise_error(
                StatusCode.AGENT_TEAM_BUSY_INVALID,
                team_name=blocked_names,
                session_id=session_id,
                reason="team(s) active on this session; stop_team or pause_team first",
            )

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
    async def _pre_run_with_inputs(session: AgentTeamSession, inputs: object) -> None:
        """Run ``session.pre_run`` only forwarding ``inputs`` when it's a dict."""
        await session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)

    @staticmethod
    async def _resolve_session_checkpoint(
        session: AgentTeamSession,
        team_name: str,
    ) -> TeamSessionResolution:
        checkpointer = CheckpointerFactory.get_checkpointer()
        session_exists = await checkpointer.session_exists(session.get_session_id())
        await session.pre_run()
        if not session_exists:
            return TeamSessionResolution(kind="missing")

        bucket = read_team_namespace(session, team_name)
        if bucket is None:
            other_teams = sorted(read_teams_bucket(session).keys())
            if other_teams:
                return TeamSessionResolution(
                    kind="missing",
                    reason=f"session has teams {other_teams!r}, not {team_name!r}",
                )
            return TeamSessionResolution(
                kind="invalid",
                reason="checkpoint has no persisted team buckets",
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

    async def _set_active(self, team_name: str, session_id: str, agent: "TeamAgent") -> None:
        self._active_team_name = team_name
        self._active_session_id = session_id
        self._active_agent = agent
        self._active_paused = False
        await self._pool.add(
            ActiveTeam(
                team_name=team_name,
                agent=agent,
                current_session_id=session_id,
                state=RuntimeState.RUNNING,
            )
        )

    async def _clear_active(self) -> None:
        team_name = self._active_team_name
        self._active_team_name = None
        self._active_session_id = None
        self._active_agent = None
        self._active_paused = False
        if team_name is not None:
            await self._pool.remove(team_name)
