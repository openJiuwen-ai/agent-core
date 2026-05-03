# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team-runtime surface mixed into the Runner.

Keeps every ``TeamAgentSpec``-oriented hook in one place so ``runner.py``
stays focused on the workflow / single-agent core.

* :class:`_TeamRunnerMixin` carries the instance-side coroutines exposed
  by ``_RunnerImpl``: ``run_agent_team`` / ``run_agent_team_streaming`` /
  ``interact_agent_team`` / ``pause_agent_team`` / ``stop_agent_team`` /
  ``delete_agent_team`` plus the helpers they need.
* :class:`_TeamRunnerClassMixin` carries the matching classmethod facade
  reused by :class:`Runner`; each call is a thin proxy back to the
  ``GLOBAL_RUNNER`` instance.

The mixin reads two attributes initialised by the host class:

* ``_team_runtime_manager`` (Optional[TeamRuntimeManager]) — created
  lazily on first use to avoid pulling ``openjiuwen.agent_teams`` during
  child-process bootstrap.
* ``_resource_manager`` (ResourceMgr) — used by ``_prepare_agent_team``
  to resolve team_id strings into ``BaseTeam`` instances.

Plus one method:

* ``_root_task_group_scope()`` — context manager used to scope the
  underlying anyio task group around each run.
"""

from __future__ import annotations

from typing import (
    Any,
    AsyncIterator,
    Optional,
    TYPE_CHECKING,
    Union,
)

from openjiuwen.core.common.logging import runner_logger as logger
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.multi_agent import (
    BaseTeam,
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
    Session as AgentSession,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.runtime import (
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

    # ------------------------------------------------------------------
    # public coroutines
    # ------------------------------------------------------------------
    async def run_agent_team(
        self,
        agent_team: Union[str, "BaseTeam", BaseAgent, "TeamAgentSpec"],
        inputs: Any,
        *,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        envs: Optional[dict[str, Any]] = None,
    ):
        """Execute a team of agents with given inputs.

        ``TeamAgent`` (a ``BaseAgent`` subclass) is also accepted; pass it
        directly instead of using ``run_agent`` to get proper
        ``AgentTeamSession`` lifecycle.
        """
        with self._root_task_group_scope():
            if self._is_team_agent_spec(agent_team):
                activation = await self._get_team_runtime_manager().activate(agent_team, session, inputs)
                try:
                    action = activation.action
                    if _is_team_reject_kind(action.kind):
                        logger.warning(
                            "run_agent_team rejected for team/session "
                            "({}, {}), kind={}, reason={}",
                            agent_team.team_name,
                            activation.session.get_session_id(),
                            action.kind.value,
                            action.reason or "",
                        )
                        return None
                    return await activation.agent.invoke(inputs, session=activation.session)
                finally:
                    await self._close_team_interact_gate(
                        team_name=agent_team.team_name,
                        session_id=activation.session.get_session_id(),
                    )
                    await activation.session.post_run()
            agent_team_instance = await self._prepare_agent_team(agent_team)
            agent_team_session = self._create_agent_team_session(agent_team_instance, session)
            await agent_team_session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            team_runtime = getattr(agent_team_instance, "runtime", None)
            if team_runtime is not None:
                team_runtime.bind_team_session(agent_team_session)
            try:
                return await agent_team_instance.invoke(inputs, session=agent_team_session)
            finally:
                if team_runtime is not None:
                    team_runtime.unbind_team_session(agent_team_session.get_session_id())
                await agent_team_session.post_run()

    async def run_agent_team_streaming(
        self,
        agent_team: Union[str, "BaseTeam", BaseAgent, "TeamAgentSpec"],
        inputs: Any,
        *,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        stream_modes: Optional[list[BaseStreamMode]] = None,
        envs: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """Execute a team of agents with streaming output support.

        ``TeamAgent`` (a ``BaseAgent`` subclass) is also accepted; pass it
        directly instead of using ``run_agent_streaming`` to get proper
        ``AgentTeamSession`` lifecycle and checkpointing.
        """
        with self._root_task_group_scope():
            if self._is_team_agent_spec(agent_team):
                activation = await self._get_team_runtime_manager().activate(agent_team, session, inputs)
                try:
                    action = activation.action
                    if _is_team_reject_kind(action.kind):
                        logger.warning(
                            "run_agent_team_streaming rejected for team/session "
                            "({}, {}), kind={}, reason={}",
                            agent_team.team_name,
                            activation.session.get_session_id(),
                            action.kind.value,
                            action.reason or "",
                        )
                        return
                    yield self._build_team_runtime_ready_chunk(
                        team_name=agent_team.team_name,
                        session_id=activation.session.get_session_id(),
                        action_kind=action.kind,
                    )
                    async for chunk in activation.agent.stream(inputs, session=activation.session):
                        yield chunk
                finally:
                    await self._close_team_interact_gate(
                        team_name=agent_team.team_name,
                        session_id=activation.session.get_session_id(),
                    )
                    await activation.session.post_run()
                return
            agent_team_instance = await self._prepare_agent_team(agent_team)
            agent_team_session = self._create_agent_team_session(agent_team_instance, session)
            await agent_team_session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            team_runtime = getattr(agent_team_instance, "runtime", None)
            if team_runtime is not None:
                team_runtime.bind_team_session(agent_team_session)
            try:
                async for chunk in agent_team_instance.stream(inputs, session=agent_team_session):
                    yield chunk
            finally:
                if team_runtime is not None:
                    team_runtime.unbind_team_session(agent_team_session.get_session_id())
                await agent_team_session.post_run()

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
        channel (``GodViewMessage(body=...)``).

        Returns a ``DeliverResult``. Missing ``team_name`` or
        ``session_id`` returns ``DeliverResult.failure("missing_target")``.
        """
        from openjiuwen.agent_teams.interaction.payload import (
            DeliverResult,
            GodViewMessage,
        )

        if team_name is None or session_id is None:
            return DeliverResult.failure("missing_target")
        if isinstance(payload, str):
            payload = GodViewMessage(body=payload)
        return await self._get_team_runtime_manager().interact(
            payload,
            team_name=team_name,
            session_id=session_id,
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

        metadata = await TeamRuntimeManager.resolve_team_session_metadata(session_id)
        if metadata is None:
            return False
        await self._get_team_runtime_manager().release_session(session_id, force=force)
        await CheckpointerFactory.get_checkpointer().release(session_id)
        return True

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    @staticmethod
    def _is_team_agent_spec(agent_team: object) -> bool:
        try:
            from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
        except ImportError:
            return False
        return isinstance(agent_team, TeamAgentSpec)

    async def _prepare_agent_team(self, agent_team: Union[str, BaseTeam]):
        if isinstance(agent_team, str):
            return await self._resource_manager.get_agent_team(team_id=agent_team)
        return agent_team

    @staticmethod
    def _create_agent_team_session(
        agent_team: BaseTeam,
        session: Optional[Union[str, AgentTeamSession, AgentSession]],
    ):
        if isinstance(session, AgentTeamSession):
            return session
        team_id = (
            getattr(agent_team.card, "id", None)
            or getattr(agent_team.card, "name", "agent_team")
        )
        if isinstance(session, AgentSession):
            return create_agent_team_session(
                session_id=session.get_session_id(),
                envs=session.get_envs(),
                team_id=team_id,
            )
        if isinstance(session, str):
            return create_agent_team_session(session_id=session, team_id=team_id)
        return create_agent_team_session(team_id=team_id)

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
    ) -> OutputSchema:
        return OutputSchema(
            type="message",
            index=0,
            payload={
                "event_type": "team.runtime_ready",
                "team_name": team_name,
                "session_id": session_id,
                "activation_kind": action_kind.value,
            },
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
        agent_team: Union[str, "BaseTeam", BaseAgent, "TeamAgentSpec"],
        inputs: Any,
        *,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        envs: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Execute a team of agents with given inputs."""
        return await _global_runner().run_agent_team(
            agent_team=agent_team,
            inputs=inputs,
            session=session,
            context=context,
            envs=envs,
        )

    @classmethod
    async def run_agent_team_streaming(
        cls,
        agent_team: Union[str, "BaseTeam", BaseAgent, "TeamAgentSpec"],
        inputs: Any,
        *,
        session: Optional[Union[str, AgentTeamSession]] = None,
        context: Optional[ModelContext] = None,
        stream_modes: Optional[list[BaseStreamMode]] = None,
        envs: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        """Execute a team of agents with streaming output support."""
        async for chunk in _global_runner().run_agent_team_streaming(
            agent_team=agent_team,
            inputs=inputs,
            session=session,
            context=context,
            stream_modes=stream_modes,
            envs=envs,
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
    async def pause_agent_team(
        cls,
        *,
        team_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Pause the active TeamAgent runtime for ``(team_name, session_id)``."""
        return await _global_runner().pause_agent_team(team_name=team_name, session_id=session_id)

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
