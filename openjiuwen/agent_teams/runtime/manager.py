# coding: utf-8
"""Runner-scoped owner of the active TeamAgent runtime pool.

Holds the in-process ``TeamRuntimePool`` and dispatches each
``run_agent_team_streaming`` call to one of the four recovery paths
exposed by ``agent_teams.factory`` (or rejects the call when the pool /
checkpoint state forbids it). Pool entries are the sole source of truth
for "which teams are currently active"; the manager itself holds no
parallel ``_active_*`` mirror.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Optional,
    TYPE_CHECKING,
)

from openjiuwen.agent_teams.factory import (
    recover_agent_team,
    recover_for_existing_session,
    resume_persistent_team,
)
from openjiuwen.agent_teams.interaction import (
    DeliverResult,
    GodViewMessage,
    HumanAgentInbox,
    HumanAgentMessage,
    HumanAgentNotEnabledError,
    InteractPayload,
    OperatorMessage,
    UnknownHumanAgentError,
    UserInbox,
)
from openjiuwen.agent_teams.runtime.dispatch import (
    RunAction,
    RunActionKind,
    decide_run_action,
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
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec


@dataclass(slots=True)
class TeamRuntimeActivation:
    """Resolved team runtime and the action that produced it."""

    agent: Optional["TeamAgent"]
    session: AgentTeamSession
    action: RunAction


@dataclass(slots=True)
class TeamSessionMetadata:
    """Resolved metadata for an agent team session."""

    team_name: str
    db_config: DatabaseConfig


class TeamRuntimeManager:
    """Owns the in-process ``TeamRuntimePool`` and runs the dispatch + side-effect cycle."""

    def __init__(self) -> None:
        self._pool: TeamRuntimePool = TeamRuntimePool()

    @property
    def pool(self) -> TeamRuntimePool:
        """Process-local TeamRuntimePool tracking active team runtimes."""
        return self._pool

    async def activate(
        self,
        spec: "TeamAgentSpec",
        session: str | AgentTeamSession | None,
        inputs: object = None,
    ) -> TeamRuntimeActivation:
        """Resolve the TeamAgent to run for the target team/session."""
        team_session = TeamRuntimeManager._build_session(spec, session)
        target_session_id = team_session.get_session_id()
        team_name = spec.team_name

        pool_entry = await self._pool.get(team_name)
        team_in_session, team_in_db = await self._inspect_session(
            team_session, team_name, pool_entry,
        )
        action = decide_run_action(
            team_in_db=team_in_db,
            team_in_session=team_in_session,
            pool_entry=pool_entry,
            target_session_id=target_session_id,
            target_team_name=team_name,
        )
        return await self._apply_action(
            action,
            spec=spec,
            team_session=team_session,
            pool_entry=pool_entry,
            inputs=inputs,
        )

    async def pause(
        self,
        *,
        team_name: str,
        session_id: str,
    ) -> bool:
        """Pause the active runtime for ``(team_name, session_id)``.

        Returns ``False`` when no matching pool entry is found; the call
        is otherwise idempotent — pausing an already-PAUSED entry is a
        no-op success.
        """
        entry = await self._resolve_entry(team_name=team_name, session_id=session_id)
        if entry is None:
            return False
        await entry.agent.pause_coordination()
        entry.state = RuntimeState.PAUSED
        return True

    async def interact(
        self,
        payload: InteractPayload,
        *,
        team_name: str,
        session_id: str,
    ) -> DeliverResult:
        """Route an interact payload through the active team's gate.

        Returns:
            ``DeliverResult.success(...)`` when the payload was handed off
            to the team. ``DeliverResult.failure("not_active")`` when no
            pool entry matches; ``DeliverResult.failure("gate_closed")``
            when the runtime is shutting down. Other failure reasons
            propagate from the underlying inbox.
        """
        entry = await self._resolve_entry(team_name=team_name, session_id=session_id)
        if entry is None:
            return DeliverResult.failure("not_active")
        ticket = await entry.interact_gate.admit()
        if ticket is None:
            return DeliverResult.failure("gate_closed")
        try:
            return await self._dispatch_payload(entry.agent, payload)
        finally:
            await entry.interact_gate.consume_done(ticket)

    @staticmethod
    async def _dispatch_payload(agent: "TeamAgent", payload: InteractPayload) -> DeliverResult:
        backend = agent.team_backend
        if backend is None and not isinstance(payload, GodViewMessage):
            return DeliverResult.failure("no_team_backend")

        if isinstance(payload, GodViewMessage):
            return await UserInbox.deliver_to_leader(agent.deliver_input, payload.body)
        if isinstance(payload, OperatorMessage):
            inbox = UserInbox(backend.message_manager)
            if payload.target is None:
                return await inbox.broadcast(payload.body)
            return await inbox.direct(payload.target, payload.body)
        if isinstance(payload, HumanAgentMessage):
            try:
                return await HumanAgentInbox(backend, backend.message_manager).send(
                    payload.body,
                    to=payload.target,
                    sender=payload.sender,
                )
            except HumanAgentNotEnabledError:
                return DeliverResult.failure("human_agent_not_enabled")
            except UnknownHumanAgentError:
                return DeliverResult.failure("unknown_human_agent")
        return DeliverResult.failure(f"unknown_payload:{type(payload).__name__}")

    async def stop_team(
        self,
        *,
        team_name: str,
        session_id: str,
    ) -> bool:
        """Tear down the active TeamAgent runtime; preserve persisted data."""
        entry = await self._resolve_entry(team_name=team_name, session_id=session_id)
        if entry is None:
            return False
        try:
            await entry.agent.stop_coordination()
        except Exception as exc:
            team_logger.warning(
                "Failed to stop team {} on session {}: {}",
                team_name,
                session_id,
                exc,
            )
        await self._pool.remove(team_name)
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

        db_config: Optional[DatabaseConfig] = None
        if session_ids:
            metadata = await self.resolve_team_session_metadata(session_ids[0])
            if metadata is None:
                raise RuntimeError(
                    f"Cannot resolve team session metadata for {session_ids[0]}, "
                    f"aborting delete_team"
                )
            db_config = metadata.db_config

        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db

        db = get_shared_db(db_config)
        await db.initialize()
        for session_id in session_ids:
            await db.drop_session_tables_by_id(session_id)

        checkpointer = CheckpointerFactory.get_checkpointer()
        for session_id in session_ids:
            await checkpointer.release(session_id)

        return await db.team.delete_team(team_name)

    async def release_session(self, session_id: str) -> None:
        """Release per-session dynamic tables for an agent team session."""
        if not session_id:
            return

        active_teams = await self._pool.teams_for_session(session_id)
        if active_teams:
            blocked_names = ", ".join(t.team_name for t in active_teams)
            raise_error(
                StatusCode.AGENT_TEAM_BUSY_INVALID,
                team_name=blocked_names,
                session_id=session_id,
                reason="team(s) active on this session; stop_team or pause_team first",
            )

        metadata = await self.resolve_team_session_metadata(session_id)
        if metadata is None:
            raise RuntimeError(f"Cannot resolve team session metadata for {session_id}")

        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db

        db = get_shared_db(metadata.db_config)
        await db.initialize()
        await db.drop_session_tables_by_id(session_id)

    @staticmethod
    async def resolve_team_session_metadata(session_id: str) -> Optional[TeamSessionMetadata]:
        """Resolve metadata for an agent team session.

        Returns None if the session has no agent team buckets. When one or
        more team buckets exist, returns metadata derived from the first
        parseable bucket — db_config is shared across teams within a
        session, so any bucket suffices for callers that only need to drop
        dynamic tables.

        Raises:
            RuntimeError: A bucket exists but its spec/context cannot be
                parsed, or db_config is missing.
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

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _resolve_entry(
        self,
        *,
        team_name: str,
        session_id: str,
    ) -> Optional[ActiveTeam]:
        """Return the pool entry for the exact ``(team_name, session_id)`` pair."""
        entry = await self._pool.get(team_name)
        if entry is None or entry.current_session_id != session_id:
            return None
        return entry

    async def _inspect_session(
        self,
        team_session: AgentTeamSession,
        team_name: str,
        pool_entry: Optional[ActiveTeam],
    ) -> tuple[bool, bool]:
        """Inspect the session checkpoint to derive (team_in_session, team_in_db).

        ``team_in_db`` is approximated as ``team_in_session OR pool_entry``.
        A clean implementation would query the static team table directly,
        but that requires a materialised db_config which may not be available
        before the leader is built. The approximation only loses precision
        for the "DB has team but no session has tracked it yet" case, which
        the caller can cover by passing a fresh spec — same effect as CREATE.
        """
        checkpointer = CheckpointerFactory.get_checkpointer()
        session_exists = await checkpointer.session_exists(team_session.get_session_id())
        await team_session.pre_run()
        if not session_exists:
            team_in_session = False
        else:
            team_in_session = read_team_namespace(team_session, team_name) is not None
        team_in_db = team_in_session or pool_entry is not None
        return team_in_session, team_in_db

    async def _apply_action(
        self,
        action: RunAction,
        *,
        spec: "TeamAgentSpec",
        team_session: AgentTeamSession,
        pool_entry: Optional[ActiveTeam],
        inputs: object,
    ) -> TeamRuntimeActivation:
        """Execute the side effects implied by ``action`` and update the pool."""
        team_name = spec.team_name
        session_id = team_session.get_session_id()
        kind = action.kind

        if kind in _REJECT_KINDS:
            agent = pool_entry.agent if pool_entry is not None else None
            team_logger.warning(
                "run_agent_team_streaming rejected for team {} session {}: {}",
                team_name,
                session_id,
                action.reason or kind.value,
            )
            return TeamRuntimeActivation(agent=agent, session=team_session, action=action)

        if kind is RunActionKind.RESUME_FROM_PAUSE:
            assert pool_entry is not None
            await self._pre_run_with_inputs(team_session, inputs)
            pool_entry.state = RuntimeState.RUNNING
            await pool_entry.interact_gate.reset()
            return TeamRuntimeActivation(agent=pool_entry.agent, session=team_session, action=action)

        if kind is RunActionKind.WARM_RECOVER:
            assert pool_entry is not None
            await recover_for_existing_session(pool_entry.agent, team_session)
            pool_entry.current_session_id = session_id
            pool_entry.state = RuntimeState.RUNNING
            await pool_entry.interact_gate.reset()
            return TeamRuntimeActivation(agent=pool_entry.agent, session=team_session, action=action)

        if kind is RunActionKind.NEW_TEAM_IN_SESSION_WARM:
            assert pool_entry is not None
            await self._pre_run_with_inputs(team_session, inputs)
            await resume_persistent_team(pool_entry.agent, team_session)
            pool_entry.current_session_id = session_id
            pool_entry.state = RuntimeState.RUNNING
            await pool_entry.interact_gate.reset()
            return TeamRuntimeActivation(agent=pool_entry.agent, session=team_session, action=action)

        # Cold paths — no pool entry. Make sure the pool stays clean.
        if kind is RunActionKind.COLD_RECOVER:
            agent = await recover_agent_team(team_session, team_name=team_name)
        elif kind is RunActionKind.NEW_TEAM_IN_SESSION:
            await self._pre_run_with_inputs(team_session, inputs)
            agent = spec.build()
            await agent.resume_for_new_session(team_session)
        elif kind is RunActionKind.CREATE:
            await self._pre_run_with_inputs(team_session, inputs)
            agent = spec.build()
        else:
            raise RuntimeError(f"Unhandled RunActionKind: {kind!r}")

        await self._pool.add(
            ActiveTeam(
                team_name=team_name,
                agent=agent,
                current_session_id=session_id,
                state=RuntimeState.RUNNING,
            )
        )
        return TeamRuntimeActivation(agent=agent, session=team_session, action=action)

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


_REJECT_KINDS = frozenset(
    {
        RunActionKind.REJECT_RUNNING,
        RunActionKind.REJECT_ORPHANED,
        RunActionKind.REJECT_INCONSISTENT,
    }
)
