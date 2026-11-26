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
from openjiuwen.agent_teams.tools.member_options import (
    get_member_model_ref,
)
from openjiuwen.agent_teams.worktree import TeammateWorktreeLifecycle
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.runner.spawn.process_manager import SpawnConfig

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
    from openjiuwen.agent_teams.agent.state import TeamAgentState
    from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
    from openjiuwen.core.runner.spawn.process_manager import SpawnedProcessHandle
    from openjiuwen.core.session.stream.base import OutputSchema


def _task_cancelling_count() -> int:
    """Return the current task's cumulative cancellation-request count.

    A ``CancelledError`` observed inside the cleanup chain is ambiguous: it
    may come from the member task / health-check task being awaited (the
    caller already decided to tear that chain down, so swallowing is
    correct), or from the cleanup caller itself being cancelled (teardown
    must still finish, but the cancellation has to be re-raised — asyncio
    delivers it only once, so swallowing would keep a ``wait_for`` timeout
    waiting until the task ends naturally).

    ``Task.cancelling()`` alone is not enough: the count is cumulative and
    stays > 0 after an earlier cancellation was already handled (even on
    purpose), so a *successful* cleanup running later in the same task would
    be misreported as cancelled. Callers therefore snapshot the count on
    entry and only treat a *new* request (count grew during the cleanup) as
    a caller cancellation; an inner-task cancellation never touches this
    task's counter.
    """
    task = asyncio.current_task()
    return task.cancelling() if task is not None else 0


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
        # Member names whose spawn is in flight (latched synchronously at the
        # top of ``spawn_teammate`` before its first await). Guards against a
        # duplicate spawn of the same member when several auto-start triggers
        # (e.g. repeated leader ``send_message`` calls firing ``startup()``)
        # land before the member's status flips — a second in-process shell
        # would re-bind the member's transport address and crash.
        self._spawning: set[str] = set()
        self.recovery_tasks: set[asyncio.Task] = set()
        self.worktree_lifecycle = TeammateWorktreeLifecycle(configurator)

    async def spawn_teammate(
            self,
            ctx: TeamRuntimeContext,
            *,
            initial_message: Optional[str] = None,
            session: Optional[Any] = None,
            spawn_config: Optional[SpawnConfig] = None,
            resume_external_backend: bool = False,
    ) -> Optional[SpawnedProcessHandle]:
        member_name = ctx.member_name
        # Idempotency: skip a duplicate spawn of an already-spawned or
        # in-flight member. ``startup()`` re-reads UNSTARTED rows on every
        # auto-start trigger, so the same member can be requested several
        # times before its status flips; spawning twice would create a second
        # shell that re-binds the member's transport address.
        existing = self.spawned_handles.get(member_name)
        if existing is not None:
            team_logger.debug(
                "[{}] teammate {} already spawned; skip duplicate",
                self._configurator.member_name or "?",
                member_name,
            )
            return existing
        if member_name in self._spawning:
            team_logger.debug(
                "[{}] teammate {} spawn already in flight; skip duplicate",
                self._configurator.member_name or "?",
                member_name,
            )
            return None
        self._spawning.add(member_name)
        try:
            return await self._spawn_teammate_inner(
                ctx,
                initial_message=initial_message,
                session=session,
                spawn_config=spawn_config,
                resume_external_backend=resume_external_backend,
            )
        finally:
            self._spawning.discard(member_name)

    async def _spawn_teammate_inner(
            self,
            ctx: TeamRuntimeContext,
            *,
            initial_message: Optional[str] = None,
            session: Optional[Any] = None,
            spawn_config: Optional[SpawnConfig] = None,
            resume_external_backend: bool = False,
    ) -> SpawnedProcessHandle:
        member_name = ctx.member_name
        team_logger.info("[{}] spawning teammate: {}", self._configurator.member_name or "?", member_name)

        spec = self._configurator.spec
        if ctx.cli_agent:
            # External CLI member: TeamAgent shell runs in-process while the
            # third-party CLI binary is the subprocess driven via stdin.
            from openjiuwen.agent_teams.spawn.external_cli_spawn import external_cli_spawn

            team_backend = self._configurator.team_backend
            if team_backend is not None:
                hitt_enabled = team_backend.hitt_enabled()
            else:
                hitt_enabled = False
            handle = await external_cli_spawn(
                spec=spec,
                ctx=ctx,
                hitt_enabled=hitt_enabled,
                initial_message=initial_message,
                session_id=get_session_id() or session,
                resume_external_backend=resume_external_backend,
            )
            self._wire_inprocess_chunk_forward(handle)
        elif spec and spec.spawn_mode == "inprocess":
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

    def has_live_handle(self, member_name: str) -> bool:
        """Return whether ``member_name`` already has a registered spawn handle.

        A present handle means the member was spawned in this runtime and has
        not been cleaned up; ``recover_team`` uses this to skip re-spawning a
        teammate that is already running (unhealthy members are restarted via
        the ``on_unhealthy`` callback, not by ``recover_team``).
        """
        return self.spawned_handles.get(member_name) is not None

    @staticmethod
    def _log_cleanup_cancellation(
            member_name: str,
            phase: str,
            cancelling_before: int,
    ) -> None:
        """Log a cancellation observed inside one cleanup phase and continue.

        A ``CancelledError`` raised by an awaited member/heartbeat task says
        nothing about the caller: teardown must continue. Whether the caller
        *itself* was cancelled is judged by the ``Task.cancelling()`` delta
        since cleanup started, and restored once every phase has run.

        Args:
            member_name: Member whose cleanup observed the cancellation.
            phase: ``health check`` / ``force_kill`` / ``dispose coordination``.
            cancelling_before: Counter snapshot taken when cleanup started.
        """
        team_logger.warning(
            "[SpawnManager] [{}] cleanup {} observed cancellation "
            "(caller cancelled during cleanup: {}); continuing teardown",
            member_name,
            phase,
            _task_cancelling_count() > cancelling_before,
        )

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
        # 取消判据取「本次清理期间的增量」而非存量计数：调用方可能在进入
        # 本函数前就已被取消过一次并被上层有意消化（Task.cancelling() 计数
        # 不清零），那种情况下本次清理即使全部成功也不得误报为被取消。
        cancelling_before = _task_cancelling_count()
        # 两个阶段各自隔离：心跳停等被取消/失败不代表成员已停，必须继续走
        # force_kill——跳过它会把成员任务连同其 EventBus 留在进程里。
        try:
            await handle.stop_health_check()
        except asyncio.CancelledError:
            self._log_cleanup_cancellation(member_name, "health check", cancelling_before)
        except Exception as e:
            team_logger.error(
                "[SpawnManager] [{}] cleanup health check failed: {}",
                member_name,
                e,
            )
        try:
            if handle.is_alive:
                await handle.force_kill()
        except asyncio.CancelledError:
            self._log_cleanup_cancellation(member_name, "force_kill", cancelling_before)
        except Exception as e:
            team_logger.error(
                "[SpawnManager] [{}] cleanup force_kill failed: {}",
                member_name,
                e,
            )
        finally:
            # 成员事件平面不随 asyncio 任务取消而回收（EventBus 的 run loop
            # 与两个 poll task 是独立任务）：pause/stop/restart 每次都要显式
            # dispose，否则留下每 30s 空转 + 邮箱/任务板 DB 读的僵尸轮询。
            # 放在 finally：handle 已在上面 pop，这是唯一一次清理机会——
            # force_kill 的 await 在成员任务带异常收尾时会重抛，
            # stop_health_check 对子进程句柄也可能抛 I/O 错，都不能跳过拆除。
            dispose = getattr(agent_ref, "dispose_coordination", None)
            if dispose is not None:
                try:
                    await dispose()
                except asyncio.CancelledError:
                    # 内核 dispose 在延迟取消路径上会重抛 CancelledError
                    # （各步已跑完、仅未 latch）。来源仍按增量判断：调用方
                    # 本次清理期间被取消→末尾补回抛；否则（成员侧取消）吞掉。
                    self._log_cleanup_cancellation(
                        member_name, "dispose coordination", cancelling_before
                    )
                except Exception as e:
                    team_logger.error(
                        "[SpawnManager] [{}] dispose coordination failed: {}",
                        member_name,
                        e,
                    )
        if _task_cancelling_count() > cancelling_before:
            # 拆完再把本次清理期间新到的调用方取消补回抛：CancelledError
            # 只投递一次，吞掉会让 wait_for 超时/进程 shutdown 的取消一直
            # 等到 leader 自然结束。内层取消（成员任务/心跳）不改本任务
            # 计数，不在此列；清理前已存在的存量取消也不在此列。
            raise asyncio.CancelledError()

    async def restart_teammate(self, member_name: str, max_retries: int = 3) -> bool:
        try:
            await self.cleanup_teammate(member_name)
        except asyncio.CancelledError:
            # 调用方被取消：recover/unhealthy 调用方在进来前已置 RESTARTING，
            # 取消展开必须把它归一到可恢复状态，否则成员永卡 RESTARTING。
            await self.mark_restart_aborted(member_name)
            raise

        team_backend = self._configurator.team_backend
        try:
            # ``build_context_from_db`` 会 await DB（含成员/worktree 查询）：
            # 取消落在这里与落在 spawn/退避处同样会把成员留在 RESTARTING，
            # 因此上下文构建与整个重试循环同在一层取消归一之内。
            ctx = await self.build_context_from_db(member_name)
            if ctx is None:
                team_logger.error("Cannot recover spawn config for {}", member_name)
                return False

            if team_backend is None:
                return False

            # Restart / recover must not replay the member's first-start
            # instruction: ``initial_message`` stays None so no harness.send is
            # triggered. The member re-subscribes and recovers via its mailbox;
            # only real pending messages drive a round. The member's existence
            # is already validated by ``build_context_from_db`` above.
            initial_message = None
            spawn_config = SpawnConfig(health_check_timeout=30, health_check_interval=50)

            for attempt in range(1, max_retries + 1):
                try:
                    team_logger.info("Restarting teammate {} (attempt {}/{})", member_name, attempt, max_retries)
                    await self.spawn_teammate(
                        ctx,
                        initial_message=initial_message,
                        session=get_session_id() or None,
                        spawn_config=spawn_config,
                        resume_external_backend=True,
                    )
                    await self.publish_restart_event(member_name, attempt)
                    team_logger.info("Teammate {} restarted successfully", member_name)
                    return True
                except Exception as e:
                    team_logger.exception("Restart attempt {} for {} failed: {}", attempt, member_name, e)
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** attempt)

            team_name = self._configurator.team_name
            if team_name:
                # set_member_status：DAO 直写补发 MemberStatusChangedEvent，
                # 前端不再只能等快照轮询才发现成员失败。与取消归一同样走
                # 条件写——本路径跑在 recovery 任务上，leader 可能并发写入
                # PAUSED/STOPPED，那是权威结论，不得被 ERROR 覆盖。
                normalized = await team_backend.set_member_status(
                    member_name,
                    MemberStatus.ERROR,
                    allowed_from=self._RESTART_ABORT_OVERWRITABLE,
                )
                if not normalized:
                    # 并发权威写（leader pause/stop）优先：PAUSED/STOPPED 是
                    # “为什么运行时不在”的结论，不再归档成 ERROR；显式记一笔
                    # 便于区分「真失败」与被并发状态覆盖。
                    team_logger.info(
                        "[SpawnManager] [{}] restart retries exhausted; "
                        "status changed concurrently or is no longer a "
                        "runtime state; left untouched",
                        member_name,
                    )
            return False
        except asyncio.CancelledError:
            # 上下文构建 / 退避 sleep / spawn await / 失败归一处被取消：
            # 同样不得留在 RESTARTING。
            await self.mark_restart_aborted(member_name)
            raise

    # 这些状态代表本运行时的成员；restart 被取消时应归一到 ERROR。
    # PAUSED/STOPPED/SHUTDOWN 是 leader pause/stop 或成员退场的权威结论，
    # 不得被取消展开覆盖（否则 leader 刚写的 PAUSED 会被改回 ERROR）。
    _RESTART_ABORT_OVERWRITABLE = frozenset(
        {
            MemberStatus.STARTING.value,
            MemberStatus.READY.value,
            MemberStatus.BUSY.value,
            MemberStatus.RESTARTING.value,
        }
    )

    async def mark_restart_aborted(self, member_name: str) -> None:
        """Normalize the persisted status after a cancelled restart.

        ``recover_team`` / ``on_teammate_unhealthy`` set ``RESTARTING``
        before calling :meth:`restart_teammate`. If cancellation unwinds the
        restart, the member has no live handle and no restart in flight; left
        as ``RESTARTING`` it would look endless to the UI and to any status
        consumer. ``ERROR`` is the closest state that ``MEMBER_TRANSITIONS``
        allows back into ``RESTARTING`` on the next recovery.

        Only "this runtime's member" states are overwritten: a concurrent
        leader pause/stop may have just written ``PAUSED``/``STOPPED`` (the
        authority on *why* the runtime went away), and a terminal
        ``SHUTDOWN`` must stay terminal. Anything else is left untouched.

        A live spawn handle means the restart actually produced a running
        member before the cancellation landed (e.g. cancelled while
        publishing the restart event): rewriting ``ERROR`` would mark a
        healthy member as failed. Its own boot path owns the status from
        there, so leave everything alone.

        Best-effort: a failing write must not mask the original cancellation.
        """
        if self.has_live_handle(member_name):
            team_logger.info(
                "[SpawnManager] [{}] restart already produced a live handle; "
                "cancellation leaves its status untouched",
                member_name,
            )
            return
        team_backend = self._configurator.team_backend
        team_name = self._configurator.team_name
        if team_backend is None or team_name is None:
            return
        try:
            current = await team_backend.db.member.get_member_status(
                team_name, member_name,
            )
        except asyncio.CancelledError:
            team_logger.warning(
                "[SpawnManager] [{}] status read after aborted restart "
                "cancelled again; leaving status unchanged",
                member_name,
            )
            return
        except Exception as e:
            team_logger.warning(
                "[SpawnManager] [{}] failed to read status after aborted "
                "restart: {}", member_name, e,
            )
            return
        if current not in self._RESTART_ABORT_OVERWRITABLE:
            team_logger.info(
                "[SpawnManager] [{}] restart aborted; keeping status {}",
                member_name,
                current or "unknown",
            )
            return
        try:
            # 条件写（CAS）而非直接 set：本归一可能在 recovery_tasks 的独立
            # 任务上执行（on_teammate_unhealthy），与 leader 的 pause/stop
            # 标记并发——上面读到的状态在写之前可能已被改掉，只有仍落在
            # 本运行时状态集合内才允许写 ERROR。
            normalized = await team_backend.set_member_status(
                member_name,
                MemberStatus.ERROR,
                allowed_from=self._RESTART_ABORT_OVERWRITABLE,
            )
        except asyncio.CancelledError:
            team_logger.warning(
                "[SpawnManager] [{}] status normalization after aborted "
                "restart cancelled again; leaving status unchanged",
                member_name,
            )
            return
        except Exception as e:
            team_logger.warning(
                "[SpawnManager] [{}] failed to normalize status after "
                "aborted restart: {}",
                member_name,
                e,
            )
            return
        if not normalized:
            team_logger.info(
                "[SpawnManager] [{}] restart aborted; status changed "
                "concurrently or is no longer a runtime state; left untouched",
                member_name,
            )

    async def on_teammate_unhealthy(self, member_name: str) -> None:
        team_logger.warning("Teammate {} detected as unhealthy, initiating restart", member_name)
        try:
            await self.cleanup_teammate(member_name)
        except asyncio.CancelledError:
            # cleanup 的取消回抛约定：取消展开时句柄已 pop，状态若仍是
            # READY/BUSY/RESTARTING 必须归一，否则持久态指向不存在的运行时。
            await self.mark_restart_aborted(member_name)
            raise
        team_backend = self._configurator.team_backend
        team_name = self._configurator.team_name
        if team_backend and team_name:
            member = await team_backend.db.member.get_member(member_name, team_name)
            if member is not None:
                try:
                    status = MemberStatus(member.status)
                except ValueError:
                    status = MemberStatus.ERROR
                if status in {MemberStatus.SHUTDOWN_REQUESTED, MemberStatus.SHUTDOWN}:
                    team_logger.info(
                        "Teammate {} is {}; skip unhealthy restart",
                        member_name,
                        status.value,
                    )
                    return
            await team_backend.db.member.update_member_status(
                member_name,
                team_name,
                MemberStatus.RESTARTING.value,
            )
        await self.restart_teammate(member_name)

    async def build_context_from_db(self, member_name: str) -> Optional[TeamRuntimeContext]:
        from openjiuwen.agent_teams.models.allocator import resolve_member_model
        from openjiuwen.agent_teams.tools.member_options import get_member_permissions_override

        team_backend = self._configurator.team_backend
        if team_backend is None:
            return None

        teammate = await team_backend.get_member(member_name)
        if teammate is None:
            team_logger.error("Teammate {} not found in database", member_name)
            return None

        model_ref = get_member_model_ref(teammate)
        member_model = None
        if model_ref is not None:
            team_spec = self._configurator.team_spec
            if team_spec is not None:
                member_model = resolve_member_model(
                    team_spec,
                    model_name=model_ref.model_name,
                    model_index=model_ref.model_index,
                )

        ctx = self._configurator.ctx
        # Role is persisted on the member row (``TeamMember.role``) so
        # cold recovery picks up dynamically-spawned avatars (human and
        # bridge) too — both spawn paths write their ``TeamRole`` through
        # ``spawn_member(role=...)``. Legacy DB files without the column
        # get a backfilled ``teammate`` default via
        # ``database.engine._ensure_team_member_role_column``.
        role = TeamRole(teammate.role)
        try:
            member_status = MemberStatus(teammate.status)
        except ValueError:
            member_status = MemberStatus.ERROR
        worktree_path = None
        if member_status not in {MemberStatus.SHUTDOWN_REQUESTED, MemberStatus.SHUTDOWN}:
            worktree_path = await self.worktree_lifecycle.ensure_member_worktree(teammate, role)
        # External-CLI members carry no DeepAgent: the backend registry says
        # which CLI adapter drives them, routing spawn to external_cli_spawn.
        cli_agent = team_backend.get_external_cli_agent(teammate.member_name)

        permissions_override = get_member_permissions_override(teammate)

        return TeamRuntimeContext(
            role=role,
            member_name=teammate.member_name,
            display_name=teammate.display_name or "",
            desc=teammate.desc or "",
            prompt=teammate.prompt or "",
            team_spec=ctx.team_spec if ctx else None,
            messager_config=self._configurator.build_member_messager_config(teammate.member_name),
            db_config=ctx.db_config if ctx else None,
            member_model=member_model,
            worktree_path=worktree_path,
            cli_agent=cli_agent,
            permissions_override=permissions_override,
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
        cancelling = False
        for member_name in list(self.spawned_handles.keys()):
            try:
                await self.cleanup_teammate(member_name)
            except asyncio.CancelledError:
                # cleanup_teammate 只在调用方任务自身被取消时回抛。记下并
                # 继续拆剩余成员（handle 已在 cleanup 入口 pop），全部拆完
                # 再统一回抛，既不丢取消也不留半拆的僵尸 bus。
                cancelling = True
                team_logger.warning(
                    "[SpawnManager] [{}] teardown cancelled; "
                    "continuing remaining members",
                    member_name,
                )
            except Exception as e:
                team_logger.error(
                    "[SpawnManager] [{}] shutdown failed: {}", member_name, e
                )
        self.spawned_handles.clear()
        if cancelling:
            raise asyncio.CancelledError()

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
