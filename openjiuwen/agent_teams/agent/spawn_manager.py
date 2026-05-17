# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Teammate process lifecycle management for TeamAgent."""

from __future__ import annotations

import asyncio
import contextlib
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Optional,
)

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import (
    TeamRole,
    TeamRuntimeContext,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.runner.spawn.process_manager import SpawnConfig

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
    from openjiuwen.agent_teams.agent.state import TeamAgentState
    from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
    from openjiuwen.core.runner.spawn.process_manager import SpawnedProcessHandle
    from openjiuwen.core.session.stream.base import OutputSchema


class SpawnManager:
    """Manages teammate process lifecycle and health monitoring.

    Responsibilities:
    - Process spawning (subprocess and in-process)
    - Health check coordination
    - Process cleanup and restart
    - Spawn configuration building
    """

    def __init__(
        self,
        *,
        state: "TeamAgentState",
        configurator: AgentConfigurator,
        team_agent_getter: Callable[[], Any],
    ):
        self._state = state
        self._configurator = configurator
        self._get_team_agent = team_agent_getter

        self.spawned_handles: dict[str, SpawnedProcessHandle] = {}
        self.recovery_tasks: set[asyncio.Task] = set()

    async def spawn_teammate(
        self,
        ctx: TeamRuntimeContext,
        *,
        initial_message: Optional[str] = None,
        session: Optional[Any] = None,
        spawn_config: Optional[SpawnConfig] = None,
    ) -> SpawnedProcessHandle:
        member_name = ctx.member_name
        team_logger.info("[{}] spawning teammate: {}", self._configurator.member_name or "?", member_name)

        spec = self._configurator.spec
        if spec and spec.spawn_mode == "inprocess":
            from openjiuwen.agent_teams.spawn.inprocess_spawn import inprocess_spawn

            handle = await inprocess_spawn(
                team_agent=self._get_team_agent(),
                ctx=ctx,
                initial_message=initial_message,
                session_id=get_session_id() or session,
            )
            # Wire chunk fan-out so the teammate's stream chunks reach
            # the leader's stream_queue. Subprocess teammates skip this
            # because they live in a different process; their chunks
            # would need a messager-bus equivalent (future work).
            self._wire_inprocess_chunk_forward(handle)
        else:
            handle = await Runner.spawn_agent(
                self._configurator.build_spawn_config(ctx),
                self._configurator.build_spawn_payload(
                    ctx,
                    initial_message=initial_message,
                ),
                session=session,
                spawn_config=spawn_config,
            )

        self.spawned_handles[member_name] = handle

        def _trigger_unhealthy_recovery() -> asyncio.Task:
            task = asyncio.ensure_future(self.on_teammate_unhealthy(member_name))
            self.recovery_tasks.add(task)
            task.add_done_callback(self.recovery_tasks.discard)
            return task

        handle.on_unhealthy = _trigger_unhealthy_recovery
        return handle

    def _wire_inprocess_chunk_forward(self, handle: "InProcessSpawnHandle") -> None:
        """Forward an in-process teammate's stream chunks into leader's queue.

        Same event loop, same process — leader holds a direct reference
        to the teammate ``TeamAgent`` and can plug an observer onto its
        ``StreamController`` that re-publishes each tagged chunk into
        the leader's own ``stream_queue``. The forwarder reference is
        stashed on the handle so :meth:`cleanup_teammate` can detach it.
        """
        leader = self._get_team_agent()
        if leader is None or handle.agent_ref is None:
            return
        leader_sc = leader.stream_controller
        teammate_sc = handle.agent_ref.stream_controller

        async def _forward(chunk: "OutputSchema") -> None:
            # Drop silently if the leader's queue is not set up yet or
            # has already been torn down — buffering would invert the
            # data-flow ownership and leak chunks across rounds.
            queue = leader_sc.stream_queue
            if queue is None:
                return
            await queue.put(chunk)

        teammate_sc.add_chunk_observer(_forward)
        handle.chunk_forward = _forward

    def lookup_inprocess_agent(self, member_name: str) -> Optional[Any]:
        """Return the live ``TeamAgent`` for an inprocess-spawned member.

        Returns ``None`` for subprocess-spawned members (they live in a
        different process and cannot be addressed by direct method call)
        or when no handle is registered for ``member_name``.
        """
        handle = self.spawned_handles.get(member_name)
        if handle is None:
            return None
        return getattr(handle, "agent_ref", None)

    async def cleanup_teammate(self, member_name: str) -> None:
        handle = self.spawned_handles.pop(member_name, None)
        if handle is None:
            return
        # Detach chunk forwarder before tearing the task down so a
        # late-arriving chunk cannot land in the leader queue after we
        # have considered the teammate gone.
        forward = getattr(handle, "chunk_forward", None)
        agent_ref = getattr(handle, "agent_ref", None)
        if forward is not None and agent_ref is not None:
            with contextlib.suppress(Exception):
                agent_ref.stream_controller.remove_chunk_observer(forward)
            handle.chunk_forward = None
        try:
            await handle.stop_health_check()
            if handle.is_alive:
                await handle.force_kill()
        except Exception as e:
            team_logger.error("Error cleaning up teammate {}: {}", member_name, e)

    async def restart_teammate(self, member_name: str, max_retries: int = 3) -> bool:
        await self.cleanup_teammate(member_name)

        ctx = await self.build_context_from_db(member_name)
        if ctx is None:
            team_logger.error("Cannot recover spawn config for {}", member_name)
            return False

        team_backend = self._configurator.team_backend
        if team_backend is None:
            return False

        teammate = await team_backend.get_member(member_name)
        initial_message = teammate.prompt if teammate else None
        spawn_config = SpawnConfig(health_check_timeout=30, health_check_interval=50)

        for attempt in range(1, max_retries + 1):
            try:
                team_logger.info("Restarting teammate {} (attempt {}/{})", member_name, attempt, max_retries)
                await self.spawn_teammate(
                    ctx,
                    initial_message=initial_message,
                    session=get_session_id() or None,
                    spawn_config=spawn_config,
                )
                await self.publish_restart_event(member_name, attempt)
                team_logger.info("Teammate {} restarted successfully", member_name)
                return True
            except Exception as e:
                team_logger.error("Restart attempt {} for {} failed: {}", attempt, member_name, e)
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)

        if team_backend:
            team_name = self._configurator.team_name
            if team_name:
                await team_backend.db.member.update_member_status(member_name, team_name, MemberStatus.ERROR.value)
        return False

    async def on_teammate_unhealthy(self, member_name: str) -> None:
        team_logger.warning("Teammate {} detected as unhealthy, initiating restart", member_name)
        await self.cleanup_teammate(member_name)
        team_backend = self._configurator.team_backend
        team_name = self._configurator.team_name
        if team_backend and team_name:
            await team_backend.db.member.update_member_status(
                member_name,
                team_name,
                MemberStatus.RESTARTING.value,
            )
        await self.restart_teammate(member_name)

    async def build_context_from_db(self, member_name: str) -> Optional[TeamRuntimeContext]:
        import json

        from openjiuwen.agent_teams.models.allocator import resolve_member_model

        team_backend = self._configurator.team_backend
        if team_backend is None:
            return None

        teammate = await team_backend.get_member(member_name)
        if teammate is None:
            team_logger.error("Teammate {} not found in database", member_name)
            return None

        ref_json = teammate.model_ref_json
        member_model = None
        if ref_json:
            try:
                ref = json.loads(ref_json)
            except (json.JSONDecodeError, TypeError) as e:
                team_logger.warning(
                    "[{}] malformed model_ref_json on DB record; ignoring: {}",
                    self._configurator.member_name or "?",
                    e,
                )
                ref = None

            if isinstance(ref, dict):
                team_spec = self._configurator.team_spec
                if team_spec is not None:
                    member_model = resolve_member_model(
                        team_spec,
                        model_name=ref.get("model_name"),
                        model_index=ref.get("model_index"),
                    )

        ctx = self._configurator.ctx
        # Role is persisted on the member row (``TeamMember.role``) so
        # cold recovery picks up dynamically-spawned humans too. Legacy
        # DB files without the column get a backfilled ``teammate``
        # default via ``database.engine._ensure_team_member_role_column``.
        role = TeamRole(teammate.role)
        return TeamRuntimeContext(
            role=role,
            member_name=teammate.member_name,
            persona=teammate.desc or "",
            team_spec=ctx.team_spec if ctx else None,
            messager_config=self._configurator.build_member_messager_config(teammate.member_name),
            db_config=ctx.db_config if ctx else None,
            member_model=member_model,
        )

    async def publish_restart_event(self, member_name: str, restart_count: int) -> None:
        messager = self._configurator.messager
        team_backend = self._configurator.team_backend
        if not messager or not team_backend:
            return
        from openjiuwen.agent_teams.schema.events import (
            EventMessage,
            MemberRestartedEvent,
            TeamTopic,
        )

        try:
            await messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), team_backend.team_name),
                message=EventMessage.from_event(
                    MemberRestartedEvent(
                        team_name=team_backend.team_name,
                        member_name=member_name,
                        restart_count=restart_count,
                    )
                ),
            )
        except Exception as e:
            team_logger.error("Failed to publish restart event for {}: {}", member_name, e)

    async def shutdown_all_handles(self) -> None:
        # Route through cleanup_teammate so chunk forwarders are
        # detached the same way as the single-member teardown path —
        # otherwise inprocess observers would leak across team shutdowns.
        for member_name in list(self.spawned_handles.keys()):
            try:
                await self.cleanup_teammate(member_name)
            except Exception as e:
                team_logger.error("Error shutting down teammate {}: {}", member_name, e)
        self.spawned_handles.clear()

    async def cancel_recovery_tasks(self) -> None:
        if self.recovery_tasks:
            pending = list(self.recovery_tasks)
            for task in pending:
                if not task.done():
                    task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self.recovery_tasks.clear()
