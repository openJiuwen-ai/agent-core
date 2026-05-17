# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team-runtime surface mixed into the Runner.

The public ``Runner`` facade exposes a single team entry pair:

* ``Runner.run_agent_team`` / ``run_agent_team_streaming`` — accepts
  ``str | TeamAgentSpec | BaseTeam | BaseAgent`` and routes by two
  keyword-only flags:

  - ``base=False, member=False`` (default) → agent_teams TeamAgent
    path: spec or a ``team_name`` str resolved against an
    already-active pool entry.
  - ``base=True`` → multi_agent ``BaseTeam`` path: a ``BaseTeam``
    instance or a ``team_id`` str resolved through ``resource_mgr``.
  - ``member=True`` → spawn-only path: an already-built teammate /
    human-agent ``BaseAgent`` instance produced by ``inprocess_spawn``
    / ``child_process``. Pool entry is NOT created; the leader-only
    pool invariant is preserved.

Internally the facade still delegates to two physically separated
instance methods on ``_RunnerImpl``: ``run_agent_team*`` (handles both
the TeamAgentSpec + activate/pool path and the ``member=True`` spawn
path) and ``_run_base_team*`` (BaseTeam + resource_mgr). The routing
``base`` flag lives only on the facade so SDK users see one method.

The mixin reads two attributes initialised by the host class:

* ``_team_runtime_manager`` (Optional[TeamRuntimeManager]) — created
  lazily on first use to avoid pulling ``openjiuwen.agent_teams`` during
  child-process bootstrap.
* ``_resource_manager`` (ResourceMgr) — used by ``_prepare_base_team``
  to resolve team_id strings into ``BaseTeam`` instances.

Plus one method:

* ``_root_task_group_scope()`` — context manager used to scope the
  underlying anyio task group around each run.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Optional,
    Union,
)

from openjiuwen.core.common.logging import runner_logger as logger
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.multi_agent import (
    BaseTeam,
)
from openjiuwen.core.multi_agent import (
    Session as AgentTeamSession,
)
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.stream import (
    BaseStreamMode,
    OutputSchema,
)
from openjiuwen.core.single_agent import (
    BaseAgent,
)
from openjiuwen.core.single_agent import (
    Session as AgentSession,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.monitor import (
        TeamMonitor,
        TeamStreamLogger,
    )
    from openjiuwen.agent_teams.runtime import (
        ActiveTeamInfo,
        RunActionKind,
        TeamRuntimeManager,
    )
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec


_TEAM_REJECT_KINDS: Optional[frozenset] = None


def _team_reject_kinds() -> frozenset:
    """Return the ``RunActionKind`` values that short-circuit run_agent_team*.

    Wrapped as a function to avoid pulling agent_teams imports at module
    load time — they cause circular imports during child-process bootstrap.
    """
    from openjiuwen.agent_teams.runtime import RunActionKind

    return frozenset(
        {
            RunActionKind.REJECT_RUNNING,
            RunActionKind.REJECT_ORPHANED,
            RunActionKind.REJECT_INCONSISTENT,
        }
    )


def _is_team_reject_kind(kind: object) -> bool:
    """Cache-on-first-use check for short-circuit kinds."""
    global _TEAM_REJECT_KINDS
    if _TEAM_REJECT_KINDS is None:
        _TEAM_REJECT_KINDS = _team_reject_kinds()
    return kind in _TEAM_REJECT_KINDS


class _TeamRunnerMixin:
    """Team-runtime instance methods mixed into ``_RunnerImpl``."""

    # ------------------------------------------------------------------
    # team runtime manager (lazy)
    # ------------------------------------------------------------------
    def _get_team_runtime_manager(self) -> "TeamRuntimeManager":
        """Lazily create ``TeamRuntimeManager`` on first use."""
        if self._team_runtime_manager is None:
            from openjiuwen.agent_teams.runtime import TeamRuntimeManager

            self._team_runtime_manager = TeamRuntimeManager()
        return self._team_runtime_manager

    @staticmethod
    @contextmanager
    def _bind_interact_team_session(session_id: str):
        """Bind the target team session for one interact call."""
        from openjiuwen.agent_teams.context import (
            get_session_id,
            reset_session_id,
            set_session_id,
        )

        token = None
        if session_id and get_session_id() != session_id:
            token = set_session_id(session_id)
        try:
            yield
        finally:
            if token is not None:
                reset_session_id(token)

    # ------------------------------------------------------------------
    # public coroutines
    # ------------------------------------------------------------------
    async def run_agent_team(
        self,
        agent_team: Union[str, "TeamAgentSpec", BaseAgent],
        inputs: Any,
        *,
        member: bool = False,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        envs: Optional[dict[str, Any]] = None,
    ):
        """Run an agent_teams TeamAgent identified by spec or team_name.

        Accepts:
        - ``TeamAgentSpec``: canonical assembly blueprint; goes through
          ``TeamRuntimeManager.activate`` (cold/warm/recover dispatch).
        - ``str`` (team_name): re-uses an already-active pool entry —
          the first call must pass a spec to seed the pool.

        For the multi_agent ``BaseTeam`` path call the
        ``Runner.run_agent_team`` facade with ``base=True``.

        Pass ``member=True`` when ``agent_team`` is an already-built
        teammate / human-agent ``BaseAgent`` instance produced by
        ``inprocess_spawn`` / ``child_process``: the call skips
        activate/dispatch and the leader-only pool invariant
        (see ``runtime/CLAUDE.md``) is preserved.
        """
        if member:
            return await self._run_team_member(agent_team, inputs, session=session)
        spec = await self._resolve_team_agent_spec(agent_team, session=session)
        with self._root_task_group_scope():
            activation = await self._get_team_runtime_manager().activate(spec, session, inputs)
            try:
                action = activation.action
                if _is_team_reject_kind(action.kind):
                    logger.warning(
                        "run_agent_team rejected for team/session ({}, {}), kind={}, reason={}",
                        spec.team_name,
                        activation.session.get_session_id(),
                        action.kind.value,
                        action.reason or "",
                    )
                    return None
                return await activation.agent.invoke(inputs, session=activation.session)
            finally:
                await self._get_team_runtime_manager().finalize(
                    team_name=spec.team_name,
                    session_id=activation.session.get_session_id(),
                )
                await self._close_team_interact_gate(
                    team_name=spec.team_name,
                    session_id=activation.session.get_session_id(),
                )
                await activation.session.post_run()

    async def run_agent_team_streaming(
        self,
        agent_team: Union[str, "TeamAgentSpec", BaseAgent],
        inputs: Any,
        *,
        member: bool = False,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        stream_modes: Optional[list[BaseStreamMode]] = None,
        envs: Optional[dict[str, Any]] = None,
        stream_logger: Optional["TeamStreamLogger"] = None,
    ) -> AsyncIterator[Any]:
        """Stream-run an agent_teams TeamAgent identified by spec or team_name.

        Same input contract as :meth:`run_agent_team`. Pass ``member=True``
        for the spawned-teammate path (``agent_team`` is a ``BaseAgent``).

        When ``stream_logger`` is supplied (a ``TeamStreamLogger`` built by
        the caller), every chunk on the leader path is fed through it for
        aggregated diagnostic logging. The spawned-teammate ``member=True``
        path is not logged here -- in-process teammate chunks already
        surface on the leader stream.
        """
        if member:
            async for chunk in self._run_team_member_streaming(agent_team, inputs, session=session):
                yield chunk
            return
        spec = await self._resolve_team_agent_spec(agent_team, session=session)
        with self._root_task_group_scope():
            activation = await self._get_team_runtime_manager().activate(spec, session, inputs)
            try:
                action = activation.action
                if _is_team_reject_kind(action.kind):
                    logger.warning(
                        "run_agent_team_streaming rejected for team/session ({}, {}), kind={}, reason={}",
                        spec.team_name,
                        activation.session.get_session_id(),
                        action.kind.value,
                        action.reason or "",
                    )
                    return
                blueprint = getattr(activation.agent, "blueprint", None)
                leader_member_name = getattr(blueprint, "member_name", None)
                leader_role = getattr(blueprint, "role", None)
                ready_chunk = self._build_team_runtime_ready_chunk(
                    team_name=spec.team_name,
                    session_id=activation.session.get_session_id(),
                    action_kind=action.kind,
                    leader_member_name=leader_member_name,
                    leader_role=leader_role,
                )
                if stream_logger is not None:
                    stream_logger.feed(ready_chunk)
                yield ready_chunk
                async for chunk in activation.agent.stream(inputs, session=activation.session):
                    if stream_logger is not None:
                        stream_logger.feed(chunk)
                    yield chunk
            finally:
                if stream_logger is not None:
                    stream_logger.flush()
                await self._get_team_runtime_manager().finalize(
                    team_name=spec.team_name,
                    session_id=activation.session.get_session_id(),
                )
                await self._close_team_interact_gate(
                    team_name=spec.team_name,
                    session_id=activation.session.get_session_id(),
                )
                await activation.session.post_run()

    async def _run_base_team(
        self,
        base_team: Union[str, BaseTeam],
        inputs: Any,
        *,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        envs: Optional[dict[str, Any]] = None,
    ):
        """Internal: ``base=True`` branch of ``Runner.run_agent_team``.

        Not part of the public surface — SDK callers go through
        ``Runner.run_agent_team`` with ``base=True``.

        Accepts:
        - ``BaseTeam``: a concrete subclass instance (``HandoffTeam`` /
          ``HierarchicalTeam`` / custom).
        - ``str`` (team_id): resolved through ``Runner.resource_mgr``.
        """
        team_instance = await self._prepare_base_team(base_team)
        with self._root_task_group_scope():
            team_session = self._create_agent_team_session(team_instance, session)
            await team_session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            team_runtime = getattr(team_instance, "runtime", None)
            if team_runtime is not None:
                team_runtime.bind_team_session(team_session)
            try:
                return await team_instance.invoke(inputs, session=team_session)
            finally:
                if team_runtime is not None:
                    team_runtime.unbind_team_session(team_session.get_session_id())
                await team_session.post_run()

    async def _run_base_team_streaming(
        self,
        base_team: Union[str, BaseTeam],
        inputs: Any,
        *,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        stream_modes: Optional[list[BaseStreamMode]] = None,
        envs: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """Internal: ``base=True`` branch of ``Runner.run_agent_team_streaming``.

        Same input contract as :meth:`_run_base_team`.
        """
        team_instance = await self._prepare_base_team(base_team)
        with self._root_task_group_scope():
            team_session = self._create_agent_team_session(team_instance, session)
            await team_session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            team_runtime = getattr(team_instance, "runtime", None)
            if team_runtime is not None:
                team_runtime.bind_team_session(team_session)
            try:
                async for chunk in team_instance.stream(inputs, session=team_session):
                    yield chunk
            finally:
                if team_runtime is not None:
                    team_runtime.unbind_team_session(team_session.get_session_id())
                await team_session.post_run()

    async def _run_team_member(
        self,
        agent: BaseAgent,
        inputs: Any,
        *,
        session: Optional[Union[str, AgentTeamSession]] = None,
    ):
        """Run a spawned teammate / human-agent ``TeamAgent`` instance.

        Internal entry used by ``inprocess_spawn`` and ``child_process``
        for already-built non-leader ``TeamAgent`` instances. Pool entry
        is NOT created — the leader-only pool invariant (see
        ``runtime/CLAUDE.md``) is preserved.
        """
        with self._root_task_group_scope():
            team_session = self._create_agent_team_session(agent, session)
            await team_session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            team_runtime = getattr(agent, "runtime", None)
            if team_runtime is not None:
                team_runtime.bind_team_session(team_session)
            try:
                return await agent.invoke(inputs, session=team_session)
            finally:
                await self._get_team_runtime_manager().finalize_member(agent)
                if team_runtime is not None:
                    team_runtime.unbind_team_session(team_session.get_session_id())
                await team_session.post_run()

    async def _run_team_member_streaming(
        self,
        agent: BaseAgent,
        inputs: Any,
        *,
        session: Optional[Union[str, AgentTeamSession]] = None,
    ) -> AsyncIterator[Any]:
        """Stream-variant of :meth:`_run_team_member` (internal)."""
        with self._root_task_group_scope():
            team_session = self._create_agent_team_session(agent, session)
            await team_session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            team_runtime = getattr(agent, "runtime", None)
            if team_runtime is not None:
                team_runtime.bind_team_session(team_session)
            try:
                async for chunk in agent.stream(inputs, session=team_session):
                    yield chunk
            finally:
                await self._get_team_runtime_manager().finalize_member(agent)
                if team_runtime is not None:
                    team_runtime.unbind_team_session(team_session.get_session_id())
                await team_session.post_run()

    async def interact_agent_team(
        self,
        payload: Any,
        *,
        team_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        """Deliver an interact payload to an active TeamAgent runtime.

        ``payload`` is either an ``InteractPayload`` (one of
        ``GodViewMessage`` / ``OperatorMessage`` / ``HumanAgentMessage``)
        or a bare ``str`` — the latter is shorthand for the god-view
        channel (``GodViewMessage(body=...)``); the conversion happens
        inside ``TeamRuntimeManager.interact``.

        Returns a ``DeliverResult``. Missing ``team_name`` or
        ``session_id`` returns ``DeliverResult.failure("missing_target")``.
        """
        from openjiuwen.agent_teams.interaction.payload import DeliverResult

        if team_name is None or session_id is None:
            return DeliverResult.failure("missing_target")
        with self._bind_interact_team_session(session_id):
            return await self._get_team_runtime_manager().interact(
                payload,
                team_name=team_name,
                session_id=session_id,
            )

    async def register_human_agent_inbound(
        self,
        *,
        team_name: str,
        session_id: str,
        member_name: str,
        callback: object,
    ) -> bool:
        """Register a team→user notification callback for a human agent.

        Phase-2 HITT: every team-side message addressed to ``member_name``
        (point-to-point) or sent as a broadcast (excluding the human
        agent's own broadcasts) fires ``callback`` with a
        ``HumanAgentInboundEvent``. Pass ``None`` to clear.

        Returns ``False`` when no active runtime matches
        ``(team_name, session_id)``. Raises ``KeyError`` when
        ``member_name`` is not a registered human-agent member.
        """
        return await self._get_team_runtime_manager().register_human_agent_inbound(
            team_name=team_name,
            session_id=session_id,
            member_name=member_name,
            callback=callback,
        )

    async def pause_agent_team(
        self,
        *,
        team_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Pause the active TeamAgent runtime for ``(team_name, session_id)``."""
        return await self._get_team_runtime_manager().pause(team_name=team_name, session_id=session_id)

    async def stop_agent_team(
        self,
        *,
        team_name: str,
        session_id: str,
    ) -> bool:
        """Stop the active TeamAgent runtime for ``(team_name, session_id)``.

        Tears down the leader and teammate processes; persisted data
        (checkpoint, dynamic tables, team static row) is preserved so a
        subsequent ``run_agent_team_streaming`` will cold-recover.
        """
        return await self._get_team_runtime_manager().stop_team(
            team_name=team_name,
            session_id=session_id,
        )

    async def get_agent_team_monitor(
        self,
        *,
        team_name: str,
        session_id: str,
        hide_dm: bool = False,
    ) -> Optional["TeamMonitor"]:
        """Return a TeamMonitor for the active TeamAgent runtime, if present.

        ``hide_dm`` forwards to ``TeamMonitor``; when True the monitor
        drops every non-broadcast message from both query results and
        the live event stream.
        """
        return await self._get_team_runtime_manager().get_monitor(
            team_name=team_name,
            session_id=session_id,
            hide_dm=hide_dm,
        )

    async def list_active_teams(self) -> list["ActiveTeamInfo"]:
        """Snapshot every TeamAgent runtime currently held by the pool.

        Returns read-only ``ActiveTeamInfo`` entries (team_name /
        current_session_id / state / gate_closed) — the live ``TeamAgent``
        and ``InteractGate`` are intentionally excluded so callers cannot
        mutate runtime state through the result.
        """
        return await self._get_team_runtime_manager().list_active_teams()

    async def delete_agent_team(
        self,
        *,
        team_name: str,
        session_ids: list[str],
        force: bool = False,
    ) -> bool:
        """Delete a team and release all supplied sessions.

        ``force=True`` stops the team's active runtime in-line; default
        ``force=False`` requires the caller to ``stop_agent_team`` first
        and otherwise raises ``AGENT_TEAM_BUSY_INVALID``.
        """
        return await self._get_team_runtime_manager().delete_team(
            team_name=team_name,
            session_ids=session_ids,
            force=force,
        )

    # ------------------------------------------------------------------
    # release helper called from _RunnerImpl.release
    # ------------------------------------------------------------------
    async def _maybe_release_team_session(
        self,
        session_id: str,
        *,
        force: bool,
    ) -> bool:
        """Release a session through the team runtime if it is a team session.

        Returns ``True`` when the session was a team session and has now
        been cleaned (dynamic tables dropped + checkpoint released); the
        caller should not perform a second release. Returns ``False`` for
        non-team sessions so the caller can fall back to a plain
        checkpoint release.
        """
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        release_info = await TeamRuntimeManager.resolve_team_session_release_info(session_id)
        if release_info is None:
            return False
        await self._get_team_runtime_manager().release_session(session_id, force=force)
        await CheckpointerFactory.get_checkpointer().release(session_id)
        return True

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _resolve_team_agent_spec(
        self,
        agent_team: Union[str, "TeamAgentSpec"],
        *,
        session: Optional[Union[str, AgentTeamSession]] = None,
    ) -> "TeamAgentSpec":
        """Resolve ``run_agent_team`` input into a concrete ``TeamAgentSpec``.

        Input contract for the team runtime:

        * ``TeamAgentSpec`` + new session → CREATE / NEW_TEAM_IN_SESSION.
          Used for first-time starts and for booting a stopped team onto
          a fresh session.
        * ``str`` (``team_name``) + old session → RESUME_FROM_PAUSE (pool
          still holds the entry) or COLD_RECOVER (pool empty — e.g. the
          process restarted, or a pause/stop tore the entry down).

        The ``team_name`` recover path reads the spec from the session
        bucket persisted by ``persist_leader_config`` at the previous
        ``bind_session``; the in-memory pool entry is no longer required.
        First-ever runs (no pool entry AND no session bucket) must pass
        a TeamAgentSpec.

        Other shapes (raw ``BaseAgent``, etc.) raise ``AGENT_TEAM_CONFIG_INVALID``.
        """
        from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
        from openjiuwen.core.common.exception.codes import StatusCode
        from openjiuwen.core.common.exception.errors import raise_error

        if isinstance(agent_team, TeamAgentSpec):
            return agent_team
        if isinstance(agent_team, str):
            entry = await self._get_team_runtime_manager().pool.get(agent_team)
            if entry is not None and entry.agent.spec is not None:
                return entry.agent.spec
            if session is not None:
                spec_from_bucket = await self._resolve_spec_from_session_bucket(
                    team_name=agent_team,
                    session=session,
                )
                if spec_from_bucket is not None:
                    return spec_from_bucket
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason=(
                    f"team '{agent_team}' has no live pool entry and no "
                    f"persisted spec in the supplied session; first-time runs "
                    f"must pass a TeamAgentSpec on a new session"
                ),
            )
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=(
                f"run_agent_team accepts str | TeamAgentSpec; got "
                f"{type(agent_team).__name__}. For BaseTeam pass base=True."
            ),
        )

    @staticmethod
    async def _resolve_spec_from_session_bucket(
        *,
        team_name: str,
        session: Union[str, AgentTeamSession],
    ) -> Optional["TeamAgentSpec"]:
        """Read a persisted ``TeamAgentSpec`` from the session's team bucket.

        Returns ``None`` when the session has no checkpoint, no bucket for
        ``team_name``, or no ``spec`` payload in the bucket — the caller
        decides whether that absence is fatal. Errors during restore are
        swallowed and logged at warning level so the caller can fall
        through to a clearer error message rather than re-raising a
        cryptic checkpoint failure.

        ``agent_customizer`` is dropped by serialization
        (``Field(exclude=True)``) and is not reinjected here because no
        live ``runtime_spec`` is available on the ``name`` path; callers
        relying on a customizer must use the explicit ``spec`` form.
        """
        from openjiuwen.agent_teams.runtime.metadata import read_team_namespace
        from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec

        if isinstance(session, AgentTeamSession):
            team_session = session
        else:
            team_session = create_agent_team_session(session_id=session)
        try:
            await team_session.pre_run()
        except Exception as exc:
            logger.warning(
                "failed to restore session {} for spec lookup: {}",
                team_session.get_session_id(),
                exc,
            )
            return None
        bucket = read_team_namespace(team_session, team_name)
        if not bucket:
            return None
        spec_data = bucket.get("spec")
        if spec_data is None:
            return None
        try:
            return TeamAgentSpec.model_validate(spec_data)
        except Exception as exc:
            logger.warning(
                "failed to parse persisted spec for team {} in session {}: {}",
                team_name,
                team_session.get_session_id(),
                exc,
            )
            return None

    async def _prepare_base_team(self, base_team: Union[str, BaseTeam]) -> BaseTeam:
        """Resolve ``_run_base_team`` input into a concrete ``BaseTeam``.

        ``BaseTeam`` is returned as-is. ``str`` is treated as a
        ``team_id`` and resolved through ``Runner.resource_mgr``.
        Anything else raises ``AGENT_TEAM_CONFIG_INVALID``.
        """
        from openjiuwen.core.common.exception.codes import StatusCode
        from openjiuwen.core.common.exception.errors import raise_error

        if isinstance(base_team, str):
            return await self._resource_manager.get_agent_team(team_id=base_team)
        if isinstance(base_team, BaseTeam):
            return base_team
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=(
                f"run_agent_team(base=True) accepts str | BaseTeam; got "
                f"{type(base_team).__name__}. For TeamAgentSpec drop base=True."
            ),
        )

    @staticmethod
    def _create_agent_team_session(
        agent_team: Any,
        session: Optional[Union[str, AgentTeamSession, AgentSession]],
    ):
        if isinstance(session, AgentTeamSession):
            return session
        if isinstance(session, AgentSession):
            return create_agent_team_session(
                session_id=session.get_session_id(),
                envs=session.get_envs(),
            )
        if isinstance(session, str):
            return create_agent_team_session(session_id=session)
        return create_agent_team_session()

    async def _close_team_interact_gate(
        self,
        *,
        team_name: str,
        session_id: str,
    ) -> None:
        """Close-and-drain the InteractGate for a finished run cycle.

        Run is over: late ``interact_team`` calls must be rejected, and
        any payload that was admitted earlier must finish before the
        stream really ends. Skips silently when the pool entry is gone
        (e.g. ``stop_team`` already removed it) or bound to a different
        session (warm path moved on).
        """
        manager = self._get_team_runtime_manager()
        entry = await manager.pool.get(team_name)
        if entry is None or entry.current_session_id != session_id:
            return
        await entry.interact_gate.close_and_drain()

    @staticmethod
    def _build_team_runtime_ready_chunk(
        *,
        team_name: str,
        session_id: str,
        action_kind: "RunActionKind",
        leader_member_name: Optional[str] = None,
        leader_role: Optional[Any] = None,
    ) -> OutputSchema:
        # Emit a TeamOutputSchema so leader-side ready signals carry the
        # same source_member / role tags as every other chunk in the
        # stream. Lazy import keeps agent_teams off the child-process
        # bootstrap path.
        from openjiuwen.agent_teams.schema.stream import TeamOutputSchema

        return TeamOutputSchema(
            type="message",
            index=0,
            payload={
                "event_type": "team.runtime_ready",
                "team_name": team_name,
                "session_id": session_id,
                "activation_kind": action_kind.value,
            },
            source_member=leader_member_name,
            role=leader_role,
        )


def _global_runner():
    """Indirection so the class mixin doesn't import GLOBAL_RUNNER at definition time."""
    from openjiuwen.core.runner.runner import GLOBAL_RUNNER

    return GLOBAL_RUNNER


class _TeamRunnerClassMixin:
    """Classmethod facade for team-runtime APIs, mixed into ``Runner``.

    Each method delegates to ``GLOBAL_RUNNER`` (the singleton
    ``_RunnerImpl``); ``Runner`` itself stays a static facade.
    """

    @classmethod
    async def run_agent_team(
        cls,
        agent_team: Union[str, "TeamAgentSpec", BaseTeam, BaseAgent],
        inputs: Any,
        *,
        base: bool = False,
        member: bool = False,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        envs: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Run a team. Default routes to the agent_teams TeamAgent path
        (``str | TeamAgentSpec``). Pass ``base=True`` to run a multi_agent
        ``BaseTeam`` (``str | BaseTeam``). Pass ``member=True`` to run an
        already-built teammate / human-agent ``BaseAgent`` instance
        (spawn-only entry; bypasses pool / activate).
        """
        if base:
            # pylint: disable=protected-access — designated internal facade hook, see module docstring.
            return await _global_runner()._run_base_team(
                base_team=agent_team,
                inputs=inputs,
                session=session,
                context=context,
                envs=envs,
            )
        return await _global_runner().run_agent_team(
            agent_team=agent_team,
            inputs=inputs,
            member=member,
            session=session,
            context=context,
            envs=envs,
        )

    @classmethod
    async def run_agent_team_streaming(
        cls,
        agent_team: Union[str, "TeamAgentSpec", BaseTeam, BaseAgent],
        inputs: Any,
        *,
        base: bool = False,
        member: bool = False,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        stream_modes: Optional[list[BaseStreamMode]] = None,
        envs: Optional[dict[str, Any]] = None,
        stream_logger: Optional["TeamStreamLogger"] = None,
    ) -> AsyncIterator[Any]:
        """Stream-run a team. ``base=True`` switches to the multi_agent
        ``BaseTeam`` path (``str | BaseTeam``); default goes through the
        agent_teams TeamAgent path (``str | TeamAgentSpec``). Pass
        ``member=True`` to stream an already-built teammate / human-agent
        ``BaseAgent`` (spawn-only entry; bypasses pool / activate).

        Pass ``stream_logger`` (a ``TeamStreamLogger`` from
        ``openjiuwen.agent_teams.monitor``) to emit aggregated per-chunk
        diagnostic logs via ``team_logger``; honoured on the TeamAgent
        path only (not ``base=True``).
        """
        if base:
            # pylint: disable=protected-access — designated internal facade hook, see module docstring.
            async for chunk in _global_runner()._run_base_team_streaming(
                base_team=agent_team,
                inputs=inputs,
                session=session,
                context=context,
                stream_modes=stream_modes,
                envs=envs,
            ):
                yield chunk
            return
        async for chunk in _global_runner().run_agent_team_streaming(
            agent_team=agent_team,
            inputs=inputs,
            member=member,
            session=session,
            context=context,
            stream_modes=stream_modes,
            envs=envs,
            stream_logger=stream_logger,
        ):
            yield chunk

    @classmethod
    async def interact_agent_team(
        cls,
        payload: Any,
        *,
        team_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        """Deliver an interact payload to an active TeamAgent runtime."""
        return await _global_runner().interact_agent_team(
            payload,
            team_name=team_name,
            session_id=session_id,
        )

    @classmethod
    async def register_human_agent_inbound(
        cls,
        *,
        team_name: str,
        session_id: str,
        member_name: str,
        callback: object,
    ) -> bool:
        """Register a team→user notification callback for a human agent."""
        return await _global_runner().register_human_agent_inbound(
            team_name=team_name,
            session_id=session_id,
            member_name=member_name,
            callback=callback,
        )

    @classmethod
    async def pause_agent_team(
        cls,
        *,
        team_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Pause the active TeamAgent runtime for ``(team_name, session_id)``."""
        return await _global_runner().pause_agent_team(team_name=team_name, session_id=session_id)

    @classmethod
    async def get_agent_team_monitor(
        cls,
        *,
        team_name: str,
        session_id: str,
        hide_dm: bool = False,
    ):
        """Return a TeamMonitor for the active TeamAgent runtime, if present.

        ``hide_dm`` forwards to ``TeamMonitor``; when True the monitor
        drops every non-broadcast message from both query results and
        the live event stream.
        """
        return await _global_runner().get_agent_team_monitor(
            team_name=team_name,
            session_id=session_id,
            hide_dm=hide_dm,
        )

    @classmethod
    async def list_active_teams(cls) -> list["ActiveTeamInfo"]:
        """Snapshot every TeamAgent runtime currently held by the pool."""
        return await _global_runner().list_active_teams()

    @classmethod
    async def stop_agent_team(
        cls,
        *,
        team_name: str,
        session_id: str,
    ) -> bool:
        """Stop the active TeamAgent runtime for ``(team_name, session_id)``."""
        return await _global_runner().stop_agent_team(team_name=team_name, session_id=session_id)

    @classmethod
    async def delete_agent_team(
        cls,
        *,
        team_name: str,
        session_ids: list[str],
        force: bool = False,
    ) -> bool:
        """Delete a team and release all supplied sessions.

        ``force=True`` stops the team's active runtime in-line.
        """
        return await _global_runner().delete_agent_team(
            team_name=team_name,
            session_ids=session_ids,
            force=force,
        )


__all__ = [
    "_TeamRunnerClassMixin",
    "_TeamRunnerMixin",
]
