# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runner-scoped owner of the active TeamAgent runtime pool.

Holds the in-process ``TeamRuntimePool`` and dispatches each
``run_agent_team_streaming`` call to one of the four recovery paths
exposed by ``agent_teams.factory`` (or rejects the call when the pool /
checkpoint state forbids it). Pool entries are the sole source of truth
for "which teams are currently active"; the manager itself holds no
parallel ``_active_*`` mirror.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Optional,
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
from openjiuwen.agent_teams.interaction.router import parse_interact_str
from openjiuwen.agent_teams.monitor import (
    TeamMonitor,
    create_monitor,
)
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.runtime.dispatch import (
    RunAction,
    RunActionKind,
    decide_run_action,
)
from openjiuwen.agent_teams.runtime.metadata import (
    read_team_names_in_session,
    read_team_namespace,
    read_teams_bucket,
)
from openjiuwen.agent_teams.runtime.pool import (
    ActiveTeam,
    ActiveTeamInfo,
    RuntimeState,
    TeamRuntimePool,
)
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.tools.database import DatabaseConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import (
    Session as AgentTeamSession,
)
from openjiuwen.core.session.agent_team import (
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
class TeamSessionReleaseInfo:
    """Resolved session-scoped release info for one or more persisted teams."""

    team_names: list[str]
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
        # Session switch policy: any pool entry on a different session is
        # torn down before dispatch. This collapses the old WARM_RECOVER /
        # NEW_TEAM_IN_SESSION_WARM paths (which reused the same TeamAgent
        # across sessions) into stop+remove+cold-rebuild, so every
        # cross-session run starts from a freshly built agent on the new
        # session and stale contextvars / state cannot leak across.
        if pool_entry is not None and pool_entry.current_session_id != target_session_id:
            team_logger.info(
                "activate: stale pool entry for team {} on session {}; "
                "stop+remove before rebuilding on session {}",
                team_name,
                pool_entry.current_session_id,
                target_session_id,
            )
            await self.stop_team(
                team_name=team_name,
                session_id=pool_entry.current_session_id,
            )
            pool_entry = None
        team_in_session, team_in_db = await self._inspect_session(
            spec,
            team_session,
            team_name,
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

    async def finalize(
        self,
        *,
        team_name: str,
        session_id: str,
    ) -> None:
        """Settle a leader run cycle: choose pause vs stop, sync pool entry.

        Called from the Runner's stream/invoke finally for the leader path.
        Owns the pause-vs-stop decision so ``CoordinationKernel.finalize_round``
        can stay a pure round-end cleanup hook and external ``stop_team``
        calls cannot be silently overridden by a finally-time re-pause.

        Decision rule:
          - shutdown_requested (teammate explicitly asked to leave) → stop
          - ``agent.lifecycle != "persistent"`` → stop
          - otherwise → pause

        Idempotent: when the pool entry is already gone (an external
        ``stop_team`` ran first), this is a no-op. Re-entry against an
        already-stopped/paused kernel is also safe thanks to the lifecycle
        state-machine guards in :class:`CoordinationKernel`.
        """
        entry = await self._resolve_entry(team_name=team_name, session_id=session_id)
        if entry is None:
            return
        agent = entry.agent
        try:
            if await agent.is_shutdown_requested() or agent.lifecycle != "persistent":
                await agent.stop_coordination()
                await self._pool.remove(team_name)
            else:
                await agent.pause_coordination()
                entry.state = RuntimeState.PAUSED
        except Exception as exc:
            team_logger.warning(
                "Failed to finalize team {} on session {}: {}",
                team_name,
                session_id,
                exc,
            )

    # team_member statuses that already encode a finalize-side outcome
    # written by some other party (leader stop/pause marks or shutdown_self).
    # Manager.finalize_member must not overwrite these because doing so would
    # silently undo ``shutdown_self``'s SHUTDOWN write or the leader's
    # STOPPED/PAUSED marks set just before tearing down the in-process task.
    _MEMBER_FINALIZED_STATUSES = frozenset(
        {
            MemberStatus.STOPPED,
            MemberStatus.PAUSED,
            MemberStatus.SHUTDOWN,
        }
    )

    @staticmethod
    async def finalize_member(agent: "TeamAgent") -> None:
        """Settle a non-leader run cycle (teammate / human-agent).

        Spawned teammates do not enter the pool; the run cycle still has
        to consume a pending shutdown request or pause the kernel for the
        next assignment.

        Persisted ``team_member`` status is owned here for the natural
        round-end path:
          - SHUTDOWN_REQUESTED -> stop + mark SHUTDOWN.
          - otherwise -> pause + mark READY (next assignment can pick it up).

        When ``team_member`` is already in
        :data:`_MEMBER_FINALIZED_STATUSES` someone else has written the
        outcome (leader's ``_mark_live_teammates`` or ``shutdown_self``),
        so we only tear down the kernel and skip the status write entirely.
        """
        member = agent.team_member
        try:
            current_status: Optional[MemberStatus] = None
            if member is not None:
                try:
                    current_status = await member.status()
                except Exception as exc:
                    team_logger.debug(
                        "Failed to read team_member status during finalize_member: {}",
                        exc,
                    )
            already_finalized = (
                current_status is not None
                and current_status in TeamRuntimeManager._MEMBER_FINALIZED_STATUSES
            )
            if already_finalized:
                # External party (leader stop/pause, shutdown_self) already
                # wrote a terminal/quiescent status. Just close the kernel.
                await agent.stop_coordination()
                return
            if current_status == MemberStatus.SHUTDOWN_REQUESTED:
                await agent.stop_coordination()
                if member is not None:
                    await member.update_status(MemberStatus.SHUTDOWN)
                return
            await agent.pause_coordination()
            if member is not None:
                await member.update_status(MemberStatus.READY)
        except Exception as exc:
            team_logger.warning(
                "Failed to finalize team member {}: {}",
                getattr(agent, "member_name", "?"),
                exc,
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
        payload: InteractPayload | str,
        *,
        team_name: str,
        session_id: str,
    ) -> DeliverResult:
        """Route an interact payload through the active team's gate.

        ``payload`` accepts either an :class:`InteractPayload` (one of
        ``GodViewMessage`` / ``OperatorMessage`` / ``HumanAgentMessage``)
        or a free-form ``str``. String inputs are parsed by
        :func:`parse_interact_str` exactly once at this layer:

        - ``# body`` → :class:`GodViewMessage` (leader DeepAgent).
        - ``$<name> body`` → :class:`HumanAgentMessage` driving that
          avatar.
        - Either form may be followed by one or more ``@<member>``
          recipients (e.g. ``# @m1 @m2 hi``); each named recipient
          becomes its own bus message and ``@all`` / ``@*`` collapses
          into a single broadcast.
        - No recognised prefix → :class:`GodViewMessage(body=payload)`.

        Multi-recipient inputs fan out to multiple
        ``_dispatch_payload`` calls under the same gate ticket. The
        first failure short-circuits and is returned verbatim; on
        all-success the result carries the last message id (callers
        that need per-recipient ids should send recipients
        individually).

        Returns:
            ``DeliverResult.success(...)`` when the payload was handed off
            to the team. ``DeliverResult.failure("not_active")`` when no
            pool entry matches; ``DeliverResult.failure("gate_closed")``
            when the runtime is shutting down. Other failure reasons
            propagate from the underlying inbox.
        """
        if isinstance(payload, str):
            parsed = parse_interact_str(payload)
            payloads: list[InteractPayload] = parsed or [GodViewMessage(body=payload)]
        else:
            payloads = [payload]

        entry = await self._resolve_entry(team_name=team_name, session_id=session_id)
        if entry is None:
            return DeliverResult.failure("not_active")
        ticket = await entry.interact_gate.admit()
        if ticket is None:
            return DeliverResult.failure("gate_closed")
        try:
            last_result: DeliverResult = DeliverResult.success(None)
            for entry_payload in payloads:
                last_result = await self._dispatch_payload(entry.agent, entry_payload)
                if not last_result.ok:
                    return last_result
            return last_result
        finally:
            await entry.interact_gate.consume_done(ticket)

    @staticmethod
    async def _dispatch_payload(agent: "TeamAgent", payload: InteractPayload) -> DeliverResult:
        backend = agent.team_backend
        if backend is None and not isinstance(payload, GodViewMessage):
            return DeliverResult.failure("no_team_backend")

        if isinstance(payload, GodViewMessage):
            # GodView is the explicit "talk straight to the leader's
            # DeepAgent" channel — no mention parsing here. Routing
            # decisions (``@<member>`` / ``# body`` / ``$<avatar>``)
            # live in ``parse_interact_str`` on the str-input boundary
            # and surface as concrete payload types of their own.
            return await UserInbox.deliver_to_leader(agent.deliver_input, payload.body)
        if isinstance(payload, OperatorMessage):
            inbox = UserInbox(backend.message_manager)
            if payload.target is None:
                return await inbox.broadcast(payload.body)
            return await inbox.direct(payload.target, payload.body)
        if isinstance(payload, HumanAgentMessage):
            try:
                inbox = HumanAgentInbox(
                    backend,
                    backend.message_manager,
                    agent_lookup=agent.lookup_human_agent_runtime,
                )
                return await inbox.send(
                    payload.body,
                    to=payload.target,
                    sender=payload.sender,
                )
            except HumanAgentNotEnabledError:
                return DeliverResult.failure("human_agent_not_enabled")
            except UnknownHumanAgentError:
                return DeliverResult.failure("unknown_human_agent")
        return DeliverResult.failure(f"unknown_payload:{type(payload).__name__}")

    async def register_human_agent_inbound(
        self,
        *,
        team_name: str,
        session_id: str,
        member_name: str,
        callback: object,
    ) -> bool:
        """Register a team→user notification callback for one human agent.

        ``callback`` may be sync or async; the dispatcher awaits it when
        it returns an awaitable. Pass ``None`` to clear a prior
        registration. Returns ``False`` when no active runtime matches
        ``(team_name, session_id)``; raises ``KeyError`` (propagated
        from the backend) when ``member_name`` is not a registered
        human-agent member.
        """
        entry = await self._resolve_entry(team_name=team_name, session_id=session_id)
        if entry is None:
            return False
        backend = entry.agent.team_backend
        if backend is None:
            return False
        backend.register_human_agent_inbound(member_name, callback)
        return True

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

    async def get_monitor(
        self,
        *,
        team_name: str,
        session_id: str,
        hide_dm: bool = False,
    ) -> Optional[TeamMonitor]:
        """Return a TeamMonitor for the active runtime bound to ``(team_name, session_id)``.

        ``hide_dm`` forwards to :func:`create_monitor`; see ``TeamMonitor``
        for filtering semantics.
        """
        entry = await self._resolve_entry(team_name=team_name, session_id=session_id)
        if entry is None:
            return None
        return create_monitor(entry.agent, hide_dm=hide_dm)

    async def list_active_teams(self) -> list["ActiveTeamInfo"]:
        """Return read-only snapshots of every team currently in the pool.

        Excludes the live ``TeamAgent`` and ``InteractGate`` references so
        SDK / CLI callers cannot mutate runtime state through the result.
        """
        return await self._pool.list_all_info()

    async def delete_team(
        self,
        team_name: str,
        session_ids: list[str],
        *,
        force: bool = False,
    ) -> bool:
        """Delete team runtime state, checkpoints, persisted metadata, and filesystem directory.

        Default ``force=False`` refuses while the team has an active
        runtime in the pool — callers must stop_team / pause_team first.
        ``force=True`` stops the active runtime in-line before tearing
        down persisted state, equivalent to ``stop_team`` then
        ``delete_team`` but skips the busy precondition.

        Cleanup steps (in order):
        1. Drop session dynamic tables
        2. Release checkpoints
        3. Delete team_info row (cascade removes members/tasks/messages)
        4. Remove ``team_home(team_name)`` filesystem directory

        The filesystem cleanup uses ``team_home`` from ``paths.py``, which
        covers the standard layout including shared workspace and member
        workspaces. User-customized workspace paths outside ``team_home``
        are not removed here.
        """
        if await self._pool.has_active(team_name):
            entry = await self._pool.get(team_name)
            active_session = entry.current_session_id if entry else "?"
            if not force:
                raise_error(
                    StatusCode.AGENT_TEAM_BUSY_INVALID,
                    team_name=team_name,
                    session_id=active_session,
                    reason="team has an active runtime; stop_team before delete_team or pass force=True",
                )
            if entry is not None:
                team_logger.info(
                    "delete_team(force=True) stopping active runtime team={} session={}",
                    team_name,
                    entry.current_session_id,
                )
                await self.stop_team(
                    team_name=team_name,
                    session_id=entry.current_session_id,
                )

        checkpointer = CheckpointerFactory.get_checkpointer()
        if session_ids:
            existing_session_ids: list[str] = []
            for session_id in session_ids:
                if await checkpointer.session_exists(session_id):
                    existing_session_ids.append(session_id)
            if not existing_session_ids:
                team_logger.info(
                    "delete_team: supplied sessions already released team={} sessions={} checkpointer={}",
                    team_name,
                    session_ids,
                    type(checkpointer).__name__,
                )
                return True
        else:
            existing_session_ids = []

        db_config: Optional[DatabaseConfig] = None
        if session_ids:
            release_info = await self._resolve_any_team_session_release_info(existing_session_ids)
            if release_info is None:
                raise RuntimeError(
                    "Cannot resolve team session release info for any supplied sessions: "
                    f"{session_ids}, aborting delete_team"
                )
            db_config = release_info.db_config

        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db

        db = get_shared_db(db_config)
        await db.initialize()
        for session_id in session_ids:
            await db.drop_session_tables_by_id(session_id)

        for session_id in session_ids:
            await checkpointer.release(session_id)

        deleted = await db.team.delete_team(team_name)

        # Remove team filesystem directory (team_home) after database cleanup.
        # This covers the case where the caller has already stopped the runtime
        # (removing the pool entry) before calling delete_team, so we cannot
        # access TeamBackend._remove_cleanup_paths().
        team_dir = team_home(team_name)
        if team_dir.is_dir():
            try:
                await asyncio.to_thread(shutil.rmtree, str(team_dir))
                team_logger.info("Removed team directory: {}", team_dir)
            except Exception as exc:
                team_logger.warning("Failed to remove team directory {}: {}", team_dir, exc)

        return deleted

    async def release_session(
        self,
        session_id: str,
        *,
        force: bool = False,
    ) -> None:
        """Release per-session dynamic tables for an agent team session.

        Default ``force=False`` refuses while any team is active on the
        session. ``force=True`` stops every team currently bound to the
        session, then proceeds with cleanup.
        """
        if not session_id:
            return

        active_teams = await self._pool.teams_for_session(session_id)
        if active_teams:
            if not force:
                blocked_names = ", ".join(t.team_name for t in active_teams)
                raise_error(
                    StatusCode.AGENT_TEAM_BUSY_INVALID,
                    team_name=blocked_names,
                    session_id=session_id,
                    reason="team(s) active on this session; stop_team or pause_team first, or pass force=True",
                )
            for team in active_teams:
                team_logger.info(
                    "release_session(force=True) stopping active team={} session={}",
                    team.team_name,
                    session_id,
                )
                await self.stop_team(team_name=team.team_name, session_id=session_id)

        release_info = await self.resolve_team_session_release_info(session_id)
        if release_info is None:
            raise RuntimeError(f"Cannot resolve team session release info for {session_id}")

        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db

        db = get_shared_db(release_info.db_config)
        await db.initialize()
        await db.drop_session_tables_by_id(session_id)

    @staticmethod
    async def _resolve_any_team_session_release_info(
        session_ids: list[str],
    ) -> Optional[TeamSessionReleaseInfo]:
        """Return the first parseable release info from the supplied sessions.

        Sessions that fail to parse or carry no team bucket are skipped and
        logged at warning level. ``None`` is returned only when every session
        was unusable; callers decide whether that should raise.
        """
        if not session_ids:
            return None

        for session_id in session_ids:
            try:
                release_info = await TeamRuntimeManager.resolve_team_session_release_info(session_id)
            except RuntimeError as exc:
                team_logger.warning(
                    "Skipping session {} during release-info resolution: {}",
                    session_id,
                    exc,
                )
                continue
            if release_info is None:
                continue
            return release_info

        return None

    @staticmethod
    async def resolve_team_session_release_info(session_id: str) -> Optional[TeamSessionReleaseInfo]:
        """Resolve session-scoped release info for persisted teams.

        Returns None when the session has no persisted team buckets. When
        one or more buckets exist, returns a release info object with all
        discovered team names and the first parseable db_config.

        Raises:
            RuntimeError: Team buckets exist but none can be parsed into a
                usable db_config.
        """
        if not session_id:
            return None

        from openjiuwen.agent_teams.schema.team import TeamRuntimeContext

        session = create_agent_team_session(session_id=session_id)
        try:
            await session.pre_run()
        except Exception as e:
            team_logger.warning("Failed to restore session state for %s: %s", session_id, e)
            return None

        teams = read_teams_bucket(session)
        if not teams:
            return None

        team_names = read_team_names_in_session(session)
        parse_errors: list[str] = []
        db_config: Optional[DatabaseConfig] = None

        for team_name, bucket in sorted(teams.items()):
            context_data = bucket.get("context")
            if context_data is None:
                parse_errors.append(f"team bucket '{team_name}' missing context")
                continue

            try:
                context = TeamRuntimeContext.model_validate(context_data)
            except Exception as e:
                parse_errors.append(f"team bucket '{team_name}' context parsing failed: {e}")
                continue

            if context.db_config is None:
                parse_errors.append(f"team bucket '{team_name}' db_config is missing")
                continue

            db_config = context.db_config
            break

        if db_config is None:
            details = "; ".join(parse_errors) if parse_errors else "no parseable team bucket found"
            raise RuntimeError(
                f"Cannot resolve team session release info for {session_id}: {details}"
            )

        return TeamSessionReleaseInfo(
            team_names=sorted(team_names),
            db_config=db_config,
        )

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
        spec: "TeamAgentSpec",
        team_session: AgentTeamSession,
        team_name: str,
    ) -> tuple[bool, bool]:
        """Inspect the session checkpoint and team table.

        ``team_in_session`` comes from the checkpoint bucket; ``team_in_db``
        comes from the static team table queried via the spec's resolved
        ``DatabaseConfig``. Both reads are required by the dispatch truth
        table — the team-table query is what distinguishes CREATE from
        NEW_TEAM_IN_SESSION when the session is fresh and no warm pool
        entry exists.
        """
        from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db

        checkpointer = CheckpointerFactory.get_checkpointer()
        session_exists = await checkpointer.session_exists(team_session.get_session_id())
        await team_session.pre_run()
        if not session_exists:
            team_in_session = False
        else:
            team_in_session = read_team_namespace(team_session, team_name) is not None

        db = get_shared_db(spec.resolve_db_config())
        await db.initialize()
        team_in_db = await db.team.team_exists(team_name)
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
            if pool_entry is None:
                raise RuntimeError(f"{kind.value} requires an active pool entry")
            await self._pre_run_with_inputs(team_session, inputs)
            pool_entry.state = RuntimeState.RUNNING
            await pool_entry.interact_gate.reset()
            return TeamRuntimeActivation(agent=pool_entry.agent, session=team_session, action=action)

        # Cold paths — no pool entry. ``activate`` has already torn down
        # any stale entry from a different session before reaching here.
        if kind is RunActionKind.COLD_RECOVER:
            # Lazy import: TeamAgent's module pulls in heavy deps (rails,
            # prompts) that the rest of manager.py doesn't need at import time.
            from openjiuwen.agent_teams.agent.team_agent import TeamAgent

            agent = TeamAgent.recover_from_session(team_session, team_name, runtime_spec=spec)
            await agent.recover_team()
        elif kind is RunActionKind.NEW_TEAM_IN_SESSION:
            await self._pre_run_with_inputs(team_session, inputs)
            agent = spec.build()
            await agent.resume_for_new_session(team_session)
            # team_in_db is True at this point — the team row exists, so
            # there may be teammate rows left over from before the stop
            # (status STOPPED / PAUSED / etc). Replay them onto the new
            # session so "recover with a fresh session" actually brings
            # the original members back, not just an empty leader. Safe
            # on never-built teams: recover_team iterates DB members and
            # is a no-op when none exist.
            await agent.recover_team()
            await self._flush_team_manifest(agent, team_session)
        elif kind is RunActionKind.CREATE:
            await self._pre_run_with_inputs(team_session, inputs)
            agent = spec.build()
            await self._flush_team_manifest(agent, team_session)
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
            return create_agent_team_session(session_id=session)
        return create_agent_team_session()

    @staticmethod
    async def _pre_run_with_inputs(session: AgentTeamSession, inputs: object) -> None:
        """Run ``session.pre_run`` only forwarding ``inputs`` when it's a dict."""
        await session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)

    @staticmethod
    async def _flush_team_manifest(agent: "TeamAgent", session: AgentTeamSession) -> None:
        """Persist the minimum team manifest before exposing runtime_ready."""
        agent.persist_session_manifest(session)
        await session.flush_checkpoint()


_REJECT_KINDS = frozenset(
    {
        RunActionKind.REJECT_RUNNING,
        RunActionKind.REJECT_ORPHANED,
        RunActionKind.REJECT_INCONSISTENT,
    }
)
