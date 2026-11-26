# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Coordination lifecycle and transport wiring for TeamAgent."""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Awaitable, Callable
from typing import (
    TYPE_CHECKING,
    Any,
    NamedTuple,
    Optional,
)

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    EventBus,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.round_budget import RoundBudgetLedger
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
from openjiuwen.agent_teams.schema.status import MemberStatus, TaskStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.agent_team import Session as AgentTeamSession

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher
    from openjiuwen.agent_teams.agent.scheduling import TeamScheduler
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent

# 接管提示阶段时长：成员耗尽通报后，给 leader 自主接棒的窗口；
# 超时未接棒注入强制接管提示。与判死兜底阶段合计 ≤ 客户端 600s 预算。
_TAKEOVER_NUDGE_DELAY_SEC = 300.0


def _task_cancelling_count() -> int:
    """Current task's cumulative cancellation-request counter.

    Teardown awaits many inner tasks (EventBus loop/poll tasks, recovery
    tasks, member disposals); a ``CancelledError`` they raise does not mean
    the *caller* was cancelled. Snapshot this counter before a teardown
    segment and compare afterwards: only growth means a new cancellation
    request targeted the caller task. ``Task.cancelling()`` is cumulative
    and does not reset after an earlier (possibly intentionally handled)
    cancellation, so absolute ``> 0`` checks misreport completed teardown
    as cancelled; inner-task cancellations never touch this counter.
    """
    task = asyncio.current_task()
    return task.cancelling() if task is not None else 0


def _leader_reserve_for(limit: Optional[int]) -> int:
    """Leader 预留额度：成员不可推过“总用量阈值”的尾部份额。

    约束（R = 预留，P = limit - R）：
    - ``P >= 1``（limit >= 1）：留至少一次迭代给成员，不能出现 P<=0；
    - ``R <= limit // 2``：小总额度下预留不超过一半，避免反过来饿死成员；
    - ``R <= 30``：预留只用于“成员到顶时 leader 还有在飞轮要收尾 / 收尾前
      交叉校验”，不随 total 线性增长；收尾轮另有 30 次免计数宽限；
    - ``limit <= 1``：整个回合只有 0/1 次迭代，预留没有意义——R=0，
      全部留给成员；leader 仍可用免计数的收尾轮交付。

    预留只影响成员侧停止阈值（总用量口径，`limit - R`），leader 仍可用满
    `limit`。``limit <= 0`` 由上层告警并钳到 1。
    """
    if limit is None:
        return 0
    limit = int(limit)
    if limit <= 1:
        return 0
    reserve = max(20, math.ceil(limit * 0.1))
    return min(reserve, 30, limit // 2)


class TeardownStep(NamedTuple):
    """One isolated step of a teardown chain.

    ``name`` labels the step in the error/warning logs emitted by
    ``CoordinationKernel._run_teardown_steps``; ``run`` is the coroutine to
    execute. A named record instead of a bare ``(name, step)`` pair keeps
    call sites self-describing when a chain grows past two or three steps.
    """

    name: str
    run: Callable[[], Awaitable[None]]


class StopFallbackRelease(NamedTuple):
    """Permanent-resource release steps the ``stop`` fallback replays.

    Grouped into a named record rather than an anonymous six-element tuple:
    the producer and the consumer both stay readable (``release.close_memory``
    instead of "the third element"), and adding or reordering a step cannot
    silently shift positional meaning. ``as_steps()`` yields the ordered
    chain consumed by ``_run_teardown_steps``.
    """

    unsubscribe_transport: TeardownStep
    close_stream: TeardownStep
    close_memory: TeardownStep
    dispose_harness: TeardownStep
    stop_messager: TeardownStep
    release_session: TeardownStep

    def as_steps(self) -> tuple[TeardownStep, ...]:
        """Return the record's steps in execution order."""
        return tuple(self)


class TeardownOutcome(NamedTuple):
    """What ``_run_teardown_steps`` observed while running a chain.

    ``caller_cancelled`` is True when a *new* cancellation request targeted
    the calling task during the run (an inner task's cancellation does not
    set it). ``interrupted_step`` names the step whose await was broken by
    that caller cancellation — such a step did not finish, so a teardown
    that latches a terminal state must not claim its resources are released.
    """

    caller_cancelled: bool
    interrupted_step: Optional[str] = None


class CoordinationKernel:
    """Owns the coordination subsystem: lifecycle, transport wiring, and
    inner event ingress.

    Holds a back-reference to the owning TeamAgent because the lifecycle
    coordinates across most of TeamAgent's collaborators (stream_controller,
    session_manager, recovery_manager, spawn_manager, event bus,
    team_member). Centralizing them here keeps TeamAgent focused on the
    public BaseAgent contract.

    The internal event bus and dispatcher are constructed and owned here;
    callers go through this kernel instead of poking at the bus directly.
    """

    def __init__(self, host: "TeamAgent") -> None:
        self._host = host
        self._subscribed_topics: list[str] = []
        self._event_bus: Optional[EventBus] = None
        self._dispatcher: Optional["EventDispatcher"] = None
        # Leader-only scheduled-dispatch decision engine (F_62). Constructed
        # dormant at setup; armed when the team's effective dispatch mode is
        # "scheduled" (build_team choice / recovery restore).
        self._scheduler: Optional["TeamScheduler"] = None
        # Lifecycle state machine for pause/stop idempotency.
        # Transitions: idle -> running (start) -> paused (pause) -> stopped (stop).
        # ``stopped`` is terminal for this kernel instance; subsequent
        # pause/stop calls become no-ops so the Runner-level finally can
        # safely re-trigger them after an external stop_coordination.
        self._lifecycle_state: str = "idle"
        # Terminal teardown marker for spawned members whose owning asyncio
        # task has ended (see ``dispose``); keeps a late stop/pause no-op.
        # ``_disposing`` guards re-entrancy while the teardown runs. The
        # runner executes every step even when a caller cancellation arrives,
        # but an interrupted step did not finish — ``_disposed`` is latched
        # only when no step was interrupted (then the cancellation is
        # re-raised); a partially torn-down dispose stays retryable.
        self._disposed: bool = False
        self._disposing: bool = False
        # Round-scoped shared iteration budget ledger:
        # leader-only; opened/reset per user question, kept across
        # pause/resume, discarded at round finalize.
        self._round_budget_ledger: Optional["RoundBudgetLedger"] = None
        # 摘牌名册（leader-only）：本回合耗尽成员集合；
        # 成员侧一次性上报闸（本成员本 invoke 只收口一次）。
        self._round_exhausted_members: set = set()
        self._exhaustion_reported = False
        # 成员侧 total 耗尽自助回池的一次性闸（本派发周期一次）
        self._budget_repool_reported = False
        # 接管提示定时器（leader-only）
        self._takeover_nudge_task: Optional[asyncio.Task] = None
        # total 账本耗尽上升沿的 leader 收尾唤醒任务（leader-only）
        self._budget_wake_task: Optional[asyncio.Task] = None
        # 新回合复牌标记：open_round_budget 置位，start() 在 DB 就绪后执行
        self._pending_round_revival = False

    @property
    def round_budget_ledger(self) -> Optional["RoundBudgetLedger"]:
        """Return the current round budget ledger (leader only)."""
        return self._round_budget_ledger

    def open_round_budget(
            self, *, new_round: bool, session: Any = None,
    ) -> None:
        """Open or keep the round budget ledger.

        Leader-only. A new user question (``new_round=True``, including a
        follow-up after a pause) opens a fresh ledger; an empty-query cycle
        (spawn/recover/pure resume) keeps the existing one. Zero-round
        opening is deliberate: the ledger exists even when no first round
        is driven, so consumers never branch on None-existence.
        """
        host = self._host
        if host.role != TeamRole.LEADER:
            return
        # 新回合：上回合摘牌成员复牌（与是否启用 total 预算无关）。
        if new_round and self._round_exhausted_members:
            self._pending_round_revival = True
        # 新回合/追问 = 旧回合收尾：撤掉上一回合的接管提示定时器。
        if new_round:
            self._cancel_takeover_nudge()
        spec = host.spec
        limit = getattr(spec, "max_total_rounds", None) if spec else None
        if limit is None:
            self._round_budget_ledger = None
            return
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            team_logger.warning(
                "[RoundBudget] invalid max_total_rounds=%r; disabling total budget",
                limit,
            )
            self._round_budget_ledger = None
            return
        if limit <= 0:
            team_logger.warning(
                "[RoundBudget] max_total_rounds=%s is not positive; clamping to 1",
                limit,
            )
            limit = 1
        session_id = ""
        if session is not None:
            try:
                session_id = session.get_session_id() or ""
            except Exception:  # noqa: BLE001 - logging must never break start
                session_id = ""
        if self._round_budget_ledger is not None and not new_round:
            team_logger.debug(
                "[CoordinationKernel] [RoundBudget] ledger kept: session=%s used=%d",
                session_id,
                self._round_budget_ledger.used,
            )
            return
        prev = self._round_budget_ledger
        if prev is not None:
            reason = (
                "paused_followup"
                if self._lifecycle_state == "paused"
                else "followup"
            )
            team_logger.info(
                "[CoordinationKernel] [RoundBudget] ledger reset: session=%s reason=%s prev_used=%d",
                session_id,
                reason,
                prev.used,
            )
            prev.close(reason=f"replaced_by_{reason}")
        self._round_budget_ledger = RoundBudgetLedger(
            limit,
            session_id=session_id,
            leader_name=host.member_name or "",
            leader_reserve=_leader_reserve_for(limit),
            on_exhausted=self._on_budget_exhausted,
        )
        team_logger.info(
            "[CoordinationKernel] [RoundBudget] ledger open: session=%s round=%s limit=%d",
            session_id,
            "new" if new_round else "zero_round",
            limit,
        )

    def mark_member_exhausted(self, member_name: str) -> None:
        """Leader 侧登记成员耗尽：进摘牌名册 + 武装接管提示。"""
        if self._host.role != TeamRole.LEADER:
            return
        if member_name in self._round_exhausted_members:
            return
        self._round_exhausted_members.add(member_name)
        team_logger.info(
            "[CoordinationKernel] [MemberExhausted] leader noted exhausted member: %s", member_name,
        )
        self._arm_takeover_nudge(member_name)

    async def on_member_exhausted(self, cause: str) -> None:
        """成员侧耗尽收口：摘牌→回池→通报。

        摘牌载体 = **本回合内存名册**（leader kernel 的
        ``_round_exhausted_members``），不写 ``MemberStatus.ERROR``：
        ERROR 的既有语义是"进程级失败、发消息即 spawn-on-dispatch 复活"
        （message_manager 的 revive 链），复用它会与崩溃恢复互相误伤。

        顺序钉死：登记摘牌先于回池——防耗尽误判时在跑成员的任务被新成员
        捡起的双执行竞态。全程 fail-open。
        """
        if self._host.role == TeamRole.LEADER or self._exhaustion_reported:
            return
        self._exhaustion_reported = True
        host = self._host
        member_name = host.member_name or "?"
        team_logger.warning(
            "[CoordinationKernel] [MemberExhausted] member exhausted: %s cause=%s",
            member_name,
            cause,
        )
        # 1) 摘牌：先登记 leader 名册（inprocess 直挂；驱动派发硬闸），
        #    名册在下一回合 coordination.start 时清空复牌。
        #    （不依赖 team_backend——名册/定时器都在 leader kernel 内存侧。）
        notify = getattr(host, "_exhaustion_notify", None)
        if notify is not None:
            try:
                notify(member_name)
            except Exception:  # noqa: BLE001 - fail-open
                team_logger.warning(
                    "[CoordinationKernel] [MemberExhausted] leader notify failed: %s",
                    member_name,
                    exc_info=True,
                )
        backend = getattr(host.infra, "team_backend", None)
        if backend is None:
            return
        # 2) 任务回池：本成员在途任务全部 IN_PROGRESS/PLANNING/IN_REVIEW
        #    → PENDING 清 assignee（reset_task）
        repooled = await self._repool_member_inflight_tasks(
            member_name, context="MemberExhausted",
        )
        # 3) mailbox 通报 leader（带死因 + 回池任务清单）
        try:
            leader_name = await backend.resolve_leader_member_name()
            if leader_name and leader_name != member_name:
                await backend.message_manager.send_message(
                    content=(
                        f"[成员耗尽] {member_name} 因 {cause} 耗尽退出本回合；"
                        f"其任务已回池待重派：{repooled or '无'}。"
                        "请用 update_task 重新指派给其他成员，或自行接管。"
                    ),
                    to_member_name=leader_name,
                    from_member_name=member_name,
                )
        except Exception:  # noqa: BLE001 - fail-open
            team_logger.warning(
                "[CoordinationKernel] [MemberExhausted] notify leader failed: %s",
                member_name,
                exc_info=True,
            )

    async def _repool_member_inflight_tasks(
            self, member_name: str, *, context: str,
    ) -> list[str]:
        """Reset one member's in-flight tasks to PENDING (fail-open).

        两处共用：成员个体额度/模型重试耗尽的收口链，以及 total 耗尽时
        成员停轮后的自助回池。只做任务回池，不动成员状态/名册/公告。
        """
        backend = getattr(self._host.infra, "team_backend", None)
        if backend is None:
            return []
        repooled: list[str] = []
        try:
            tasks = await backend.db.task.get_tasks_by_assignee(
                backend.team_name, member_name,
            )
            for task in tasks:
                if task.status not in (
                        TaskStatus.IN_PROGRESS.value,
                        TaskStatus.PLANNING.value,
                        TaskStatus.IN_REVIEW.value,
                ):
                    continue
                try:
                    await backend.db.task.reset_task(task.task_id)
                    repooled.append(task.task_id)
                except Exception:  # noqa: BLE001 - per-task fail-open
                    team_logger.warning(
                        "[CoordinationKernel] [%s] repool task %s failed",
                        context,
                        task.task_id,
                        exc_info=True,
                    )
        except Exception:  # noqa: BLE001 - fail-open
            team_logger.warning(
                "[CoordinationKernel] [%s] list tasks failed: %s",
                context,
                member_name,
                exc_info=True,
            )
        return repooled

    async def _on_total_budget_exhausted(self) -> None:
        """成员侧 total 耗尽停轮：只回池自己的在途任务。

        与个体额度耗尽不同：不摘牌、不通报、不武装接管提示——total 耗尽
        是回合级终局，leader 由账本上升沿唤醒进入收尾；回池必须独立于
        leader（否则 leader 唤醒失败时任务永久 IN_PROGRESS）。
        一次性闸：本派发周期内同一成员只回池一次。
        """
        host = self._host
        if host.role == TeamRole.LEADER or self._budget_repool_reported:
            return
        self._budget_repool_reported = True
        member_name = host.member_name or "?"
        repooled = await self._repool_member_inflight_tasks(
            member_name, context="RoundBudget",
        )
        team_logger.info(
            "[CoordinationKernel] [RoundBudget] member repooled in-flight "
            "tasks at total exhaustion: member=%s tasks=%s",
            member_name,
            repooled,
        )

    def _arm_takeover_nudge(self, member_name: str) -> None:
        """接管提示阶段武装：窗口内 leader 未接棒则注入接管提示。

        member_name 仅作触发者留痕——检查口径是全局的（全部未分配
        PENDING），多成员耗尽共享一个闸，后触发者不再重复武装。
        """
        if (
                self._takeover_nudge_task is not None
                and not self._takeover_nudge_task.done()
        ):
            return  # 多成员耗尽共享一个接管提示闸
        try:
            self._takeover_nudge_task = asyncio.create_task(
                self._takeover_nudge_after(_TAKEOVER_NUDGE_DELAY_SEC, member_name)
            )
        except RuntimeError:
            # 无运行中事件循环（进程关闭/测试同步上下文）——让位给
            # jiuwenswarm 侧兜底
            team_logger.debug(
                "[CoordinationKernel] [MemberExhausted] takeover nudge not armed: no running loop",
            )

    async def _takeover_nudge_after(
            self, delay: float, triggered_by: str,
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self._clear_takeover_nudge_slot()
            return
        try:
            host = self._host
            backend = getattr(host.infra, "team_backend", None)
            harness = getattr(host.resources, "harness", None)
            if backend is None or harness is None:
                return
            pending = await backend.db.task.get_team_tasks(
                backend.team_name, status=TaskStatus.PENDING.value,
            )
            unassigned = [t for t in pending if not t.assignee]
            if not unassigned:
                return
            from openjiuwen.agent_teams.harness.state import SystemText

            ids = [t.task_id for t in unassigned]
            await harness.send(
                SystemText(
                    f"[接管提示] 成员耗尽回池的任务仍无人接棒：{ids}。"
                    "成员已不可用，请立即用 update_task 重派给其他成员，"
                    "或由你自行处理——不得搁置。"
                )
            )
            team_logger.warning(
                "[CoordinationKernel] [MemberExhausted] takeover nudge injected: "
                "triggered_by=%s pending=%s",
                triggered_by,
                ids,
            )
        except Exception:  # noqa: BLE001 - 兜底失败只告警
            team_logger.warning(
                "[CoordinationKernel] [MemberExhausted] takeover nudge failed", exc_info=True,
            )
        finally:
            self._clear_takeover_nudge_slot()

    def _clear_takeover_nudge_slot(self) -> None:
        """Clear the nudge slot so a later round can arm a fresh one."""
        if self._takeover_nudge_task is asyncio.current_task():
            self._takeover_nudge_task = None

    async def _revive_round_exhausted_members(self) -> None:
        """回合资复牌：新回合开始，清上回合摘牌名册。

        摘牌载体是本回合内存名册（不写成员状态），复牌=清名册：下一回合
        派发硬闸（task_manager 的环回门控）随之放行。
        """
        if not self._round_exhausted_members:
            self._pending_round_revival = False
            return
        for name in sorted(self._round_exhausted_members):
            team_logger.info(
                "[CoordinationKernel] [MemberExhausted] member revived for new round: %s", name,
            )
        self._round_exhausted_members.clear()
        self._pending_round_revival = False

    def _cancel_takeover_nudge(self) -> None:
        task = self._takeover_nudge_task
        self._takeover_nudge_task = None
        if task is not None and not task.done():
            task.cancel()

    def _cancel_budget_wake(self) -> None:
        task = self._budget_wake_task
        self._budget_wake_task = None
        if task is not None and not task.done():
            task.cancel()

    def _on_budget_exhausted(self) -> None:
        """Shared ledger just crossed its cap: wake an idle leader for wrap-up.

        成员侧的预算回合并不会通知 leader；它跑完自己的结算后直接回 IDLE。
        如果耗尽时 leader 恰好也在 IDLE（最近一轮已结算），没有任何事件能
        把它重新带回收尾分支——前端就会一直"思考中"。这里在账本耗尽上升沿
        主动给空闲 leader 注入一条系统收尾指令，让其起一轮、结算时按
        `budget_wrap_up_pending` 进入收尾轮。
        """
        if self._host.role != TeamRole.LEADER:
            return
        if self._budget_wake_task is not None and not self._budget_wake_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无运行中事件循环（进程关闭/同步测试）——由停摆看门狗兜底。
            # 先取 loop 再建协程，避免抛错时留下未 await 的协程对象。
            team_logger.debug(
                "[CoordinationKernel] [RoundBudget] wrap-up wake not armed: "
                "no running loop",
            )
            return
        self._budget_wake_task = loop.create_task(
            self._wake_leader_for_wrap_up()
        )

    async def _wake_leader_for_wrap_up(self) -> None:
        """Drive an idle leader into the budget wrap-up round (fail-open)."""
        try:
            host = self._host
            ledger = self._round_budget_ledger
            if ledger is None or not ledger.exhausted:
                return
            if self._lifecycle_state != "running":
                return
            harness = getattr(getattr(host, "resources", None), "harness", None)
            if harness is None:
                return
            rail = None
            try:
                from openjiuwen.harness.rails.task_completion_rail import (
                    TaskCompletionRail,
                )

                rails = harness.find_rails(TaskCompletionRail)
            except Exception:  # noqa: BLE001 - capability probe, fail-open
                rails = []
            if rails:
                rail = rails[0]
            if rail is not None:
                # 成员池到顶：leader 仍持有预留，但收尾已欠账——置位后
                # 其当前轮结算即进入收尾轮（轮内可继续用预留做事）。
                mark = getattr(rail, "mark_member_budget_exhausted", None)
                if callable(mark):
                    mark()
                if getattr(rail, "budget_wrap_up_consumed", False):
                    return  # 收尾轮已进入/已消费，无需再唤醒
            in_flight = getattr(host, "has_in_flight_round", None)
            try:
                if callable(in_flight) and in_flight():
                    # 在跑轮会在下个迭代边界被止血，结算时自然进入收尾
                    return
            except Exception:  # noqa: BLE001 - 探测失败按“在跑”放行
                return
            # 与其它框架触发一致，用 <team-event> 注入（系统事件语义，
            # 不是用户提问）。先让 rail 消费收尾槽（停成员/退回待办），
            # 事件 body 才能带上当前任务板详情。
            payload = None
            prepare = getattr(rail, "prepare_wrap_up_round", None)
            if callable(prepare):
                try:
                    # Duck-typed hook: the real rail method is async; a
                    # test double / older rail may be sync.
                    prepared = prepare()
                    if inspect.isawaitable(prepared):
                        prepared = await prepared
                    payload = prepared if isinstance(prepared, dict) else None
                except Exception:  # noqa: BLE001 - 退回通用事件
                    team_logger.warning(
                        "[CoordinationKernel] [RoundBudget] wrap-up "
                        "preparation failed",
                        exc_info=True,
                    )
            from openjiuwen.agent_teams.inbound_render import (
                render_budget_wrap_up_event,
            )

            await harness.send(render_budget_wrap_up_event(payload))
            team_logger.warning(
                "[CoordinationKernel] [RoundBudget] wrap-up wake sent to "
                "idle leader",
            )
        except Exception:  # noqa: BLE001 - 唤醒失败由停摆看门狗兜底
            team_logger.warning(
                "[CoordinationKernel] [RoundBudget] wrap-up wake failed",
                exc_info=True,
            )
        finally:
            if self._budget_wake_task is asyncio.current_task():
                self._budget_wake_task = None

    def _wire_round_budget_rails(self, harness: Any) -> None:
        """Wire the budget tick (and leader wrap-up) onto TaskCompletionRail.

        Called after every harness (re)build in :meth:`start` — the runtime
        is reconstructed per run cycle, so the wiring must be re-applied
        every cycle. Members resolve the leader's CURRENT ledger through the
        getter set at spawn time; the leader resolves its own. All failures
        are logged and swallowed: budget governance must never break a start.
        """
        from openjiuwen.harness.rails.task_completion_rail import (
            TaskCompletionRail,
        )

        host = self._host
        member_name = host.member_name or "?"
        if host.role == TeamRole.LEADER:

            def _leader_ledger_getter() -> Any:
                """Return the leader's own round-budget ledger."""
                return self._round_budget_ledger

            ledger_getter: Optional[Callable[[], Any]] = _leader_ledger_getter
        else:
            ledger_getter = getattr(host, "_budget_ledger_getter", None)
        try:
            rails = harness.find_rails(TaskCompletionRail)
        except Exception:  # noqa: BLE001 - capability probe, fail-open
            team_logger.warning(
                "[CoordinationKernel] [%s] round budget rail wiring probe failed", member_name,
                exc_info=True,
            )
            return

        def _tick(name: str = member_name) -> Optional[int]:
            if ledger_getter is None:
                # 子进程/外部 CLI 成员不共享账本（设计收窄声明）；
                # 个体预算与耗尽收口仍照常工作。
                return None
            ledger = ledger_getter()
            if ledger is None:
                return None
            return ledger.tick(name)

        def _probe(name: str = member_name) -> Optional[int]:
            """Non-mutating remaining-budget probe for settle-time checks."""
            if ledger_getter is None:
                return None
            ledger = ledger_getter()
            if ledger is None:
                return None
            return ledger.remaining(name)

        for rail in rails:
            # 周期边界：main/member 预算与收尾标志在这里清零（每次
            # coordination.start = leader 一条用户消息 / 成员一次派发）。
            # 团队链路的 BEFORE_INVOKE 是每个外层轮触发，不能作重置点。
            rail.reset_budget_cycle()
            if ledger_getter is not None:
                rail.budget_tick = _tick
                rail.budget_probe = _probe
            if host.role == TeamRole.LEADER:
                rail.enable_budget_wrap_up = True
                rail.on_budget_wrap_up = self._on_budget_wrap_up
            else:
                # 成员预算耗尽 → 摘牌/回池/通报。
                rail.on_iteration_cap = self._on_iteration_cap
                # total 耗尽停轮：只回池自己在途任务。
                rail.on_total_budget_exhausted = (
                    self._on_total_budget_exhausted
                )

        # 成员模型重试穷尽（181001 外层重驱耗尽）走同一收口。
        stream_controller = getattr(host, "stream_controller", None)
        if stream_controller is not None:
            stream_controller.on_round_exhausted = self._on_round_exhausted

        # 派发硬闸：耗尽成员本回合不可被派任务（leader 侧名册；
        # 成员侧以自己的上报闸为准）。载体是内存名册而非成员状态，
        # 避免与 ERROR（崩溃/复活）语义冲突。
        task_manager = getattr(host, "task_manager", None)
        set_gate = getattr(task_manager, "set_member_exhausted_gate", None)
        if set_gate is not None:
            if host.role == TeamRole.LEADER:
                set_gate(lambda name: name in self._round_exhausted_members)
            else:
                set_gate(
                    lambda name: bool(
                        self._exhaustion_reported and name == member_name
                    )
                )

        # 评估器链是在 harness 构建期生成的；budget_tick/收尾钩子刚接上，
        # 必须重建一次才能在团队链路真正生效。
        refresh = getattr(harness, "refresh_loop_evaluators", None)
        if refresh is not None:
            try:
                refresh()
            except Exception:  # noqa: BLE001 - 重建失败只告警
                team_logger.warning(
                    "[CoordinationKernel] [%s] round budget evaluator refresh failed",
                    member_name,
                    exc_info=True,
                )
        # 重启/恢复后回挂观测：成员新运行周期开始
        # 时账本已有其计数 = 重启不回扣路径正在生效，留痕可审计
        current = ledger_getter() if ledger_getter is not None else None
        if (
                host.role != TeamRole.LEADER
                and current is not None
                and member_name in current.detail()
        ):
            team_logger.info(
                "[CoordinationKernel] [RoundBudget] carryover: member=%s restarted, "
                "round_used=%d keeps accumulating",
                member_name,
                current.detail()[member_name],
            )

    async def _on_iteration_cap(self) -> None:
        """Rail callback: this member's iteration budget is exhausted."""
        await self.on_member_exhausted("iteration_budget")

    async def _on_round_exhausted(self) -> None:
        """StreamController callback: model-retry budget is exhausted."""
        await self.on_member_exhausted("model_retry")

    async def _on_budget_wrap_up(self) -> dict:
        """Leader budget exhausted.

        Runs BEFORE the leader's granted wrap-up round: stop every member's
        in-flight round first, then put all unfinished tasks back on the
        to-do list (order pinned by design — unassigning before stopping
        would let a mid-flight member be re-dispatched its own task).
        Fail-open per member/task: one failure must not abort the rest.

        Returns:
            ``{"repooled": [ids], "counts": {pending, blocked, completed,
            in_progress}}`` — fed into the wrap-up prompt so the summary
            matches the board.
        """
        empty = {
            "repooled": [],
            "counts": {
                "pending": 0,
                "blocked": 0,
                "completed": 0,
                "in_progress": 0,
            },
        }
        host = self._host
        leader_name = host.member_name or "?"
        # Step 1: stop all in-flight member rounds.
        handles = list(host.spawn_manager.spawned_handles.items())
        for name, handle in handles:
            agent_ref = getattr(handle, "agent_ref", None)
            if agent_ref is None:
                continue
            try:
                controller = agent_ref.stream_controller
                if controller is not None and controller.is_agent_running():
                    await controller.cancel_agent()
                    team_logger.info(
                        "[CoordinationKernel] [RoundBudget] [%s] wrap-up: cancelled in-flight round of %s",
                        leader_name,
                        name,
                    )
            except Exception:  # noqa: BLE001 - per-member fail-open
                team_logger.warning(
                    "[CoordinationKernel] [RoundBudget] [%s] wrap-up: cancel member %s failed",
                    leader_name,
                    name,
                    exc_info=True,
                )
        # Step 2: repool every owned in-flight task (IN_PROGRESS / PLANNING /
        # IN_REVIEW -> PENDING, clear assignee). Same three-state set as the
        # member-exhaustion chain.
        backend = getattr(host.infra, "team_backend", None)
        if backend is None:
            return empty
        owned = {
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.PLANNING.value,
            TaskStatus.IN_REVIEW.value,
        }
        try:
            tasks = await backend.db.task.get_team_tasks(backend.team_name)
        except Exception:  # noqa: BLE001 - repool listing fail-open
            team_logger.warning(
                "[CoordinationKernel] [RoundBudget] [%s] wrap-up: list in-flight tasks failed",
                leader_name,
                exc_info=True,
            )
            return empty
        repooled: list[str] = []
        for task in tasks:
            if task.status not in owned:
                continue
            try:
                await backend.db.task.reset_task(task.task_id)
                repooled.append(task.task_id)
            except Exception:  # noqa: BLE001 - per-task fail-open
                team_logger.warning(
                    "[CoordinationKernel] [RoundBudget] [%s] wrap-up: repool task %s failed",
                    leader_name,
                    task.task_id,
                    exc_info=True,
                )
        if repooled:
            team_logger.info(
                "[CoordinationKernel] [RoundBudget] [%s] wrap-up: repooled %d in-flight tasks",
                leader_name,
                len(repooled),
            )
        # 成员可能已在停轮时自助回池：把当前无主 PENDING
        # 也纳入收尾清单，保证提示里的"回池待重派"完整（幂等去重）。
        already_pending = [
            task.task_id
            for task in tasks
            if task.status == TaskStatus.PENDING.value and not task.assignee
        ]
        merged = list(dict.fromkeys(repooled + already_pending))
        if len(merged) > len(repooled):
            team_logger.info(
                "[CoordinationKernel] [RoundBudget] [%s] wrap-up: "
                "unassigned pending tasks included=%d",
                leader_name,
                len(merged) - len(repooled),
            )
        # 收尾提示用的任务板现状：把刚退回待办的任务按“待办”计，
        # 其余按原状态归类，避免总结把没做完的任务写成“进行中”。
        repooled_set = set(repooled)
        counts = {
            "pending": 0,
            "blocked": 0,
            "completed": 0,
            "in_progress": 0,
        }
        for task in tasks:
            if task.task_id in repooled_set:
                counts["pending"] += 1
            elif task.status == TaskStatus.PENDING.value:
                counts["pending"] += 1
            elif task.status == TaskStatus.BLOCKED.value:
                counts["blocked"] += 1
            elif task.status in (
                    TaskStatus.COMPLETED.value,
                    TaskStatus.CANCELLED.value,
            ):
                counts["completed"] += 1
            else:
                counts["in_progress"] += 1
        return {"repooled": merged, "counts": counts}

    def setup(self, *, role: TeamRole) -> None:
        """Construct the event bus and dispatcher for the given role.

        Called during TeamAgent.configure() once the role is known.
        The bus is built first so it can be passed into the dispatcher
        as the poll controller; ``dispatcher.dispatch`` is bound back
        to the bus as wake callback at ``start()`` time, not here.
        """
        from openjiuwen.agent_teams.agent.coordination.dispatcher import EventDispatcher

        host = self._host
        blueprint = host.blueprint
        infra = host.infra
        if blueprint is None or infra is None:
            raise RuntimeError("CoordinationKernel.setup() requires configured blueprint and infra")
        event_bus = EventBus(role=role)
        # F_62: the dispatch mode is static spec configuration — every role
        # assembles its mode-owned handler variants directly from it.
        dispatcher = EventDispatcher(
            host,
            blueprint=blueprint,
            infra=infra,
            poll_ctrl=event_bus,
            dispatch_mode=blueprint.spec.dispatch_mode,
        )
        self._event_bus = event_bus
        self._dispatcher = dispatcher
        # The scheduler exists only where it has a job: the leader of a
        # scheduled-dispatch team. It stays dormant until the team
        # materializes (build_team / an existing team row at start).
        if role == TeamRole.LEADER and blueprint.spec.dispatch_mode == "scheduled":
            from openjiuwen.agent_teams.agent.scheduling import TeamScheduler

            self._scheduler = TeamScheduler(
                host,
                blueprint=blueprint,
                infra=infra,
                build_context=host.build_context,
            )

    @property
    def event_bus(self) -> Optional[EventBus]:
        """Return the event bus instance, or None before setup()."""
        return self._event_bus

    @property
    def dispatcher(self) -> Optional["EventDispatcher"]:
        """Return the event dispatcher, or None before setup()."""
        return self._dispatcher

    @property
    def scheduler(self) -> Optional["TeamScheduler"]:
        """Return the leader's scheduled-dispatch engine, or None (non-leader)."""
        return self._scheduler

    @property
    def subscribed_topics(self) -> list[str]:
        return self._subscribed_topics

    async def enqueue(self, event: Any) -> None:
        """Forward an event to the internal event bus."""
        if self._event_bus is None:
            return
        await self._event_bus.enqueue(event)

    @property
    def is_running(self) -> bool:
        """Whether the underlying event bus is running."""
        return self._event_bus is not None and self._event_bus.is_running

    async def start(self, session: Any = None) -> None:
        host = self._host
        if self._disposed:
            # ``dispose`` is terminal: it already released the harness, stream
            # and session, and re-starting here would spin up a fresh EventBus
            # whose only reclaim path (``dispose``) now short-circuits on the
            # latch — the exact leak this kernel teardown exists to prevent.
            team_logger.error(
                "[CoordinationKernel] [{}] start() refused: kernel already disposed",
                host.member_name or "?",
            )
            raise RuntimeError(
                "CoordinationKernel.start() after dispose(): "
                "a disposed kernel is terminal"
            )
        if self._event_bus is None:
            return
        member_name = host.member_name or "?"
        team_logger.info("[CoordinationKernel] [{}] coordination starting", member_name)
        # 成员侧耗尽上报闸按"本运行周期"重置（一次 coordination.start =
        # 该成员一次派发周期）；leader 侧名册只在"新回合"复牌（见下方）。
        if host.role != TeamRole.LEADER:
            self._exhaustion_reported = False
            self._budget_repool_reported = False

        sess_mgr = host.session_manager
        infra = host.infra
        resources = host.resources
        blueprint = host.blueprint

        from openjiuwen.core.common.logging.utils import set_member_id

        set_member_id(member_name)
        if session is not None and not isinstance(session, AgentTeamSession):
            team_logger.warning(
                "[CoordinationKernel] [{}] TeamAgent expects AgentTeamSession; got {}. "
                "Please invoke via Runner.run_agent_team_streaming.",
                member_name,
                type(session).__name__,
            )
        if infra.team_backend:
            await infra.team_backend.db.initialize()
        if session is not None:
            await sess_mgr.bind_session(session)
            memory_manager = resources.memory_manager
            if memory_manager is not None:
                memory_manager.bind_session_id(session.get_session_id())
            # Start the member runtime (native supervisor + child session) for
            # this run cycle, then attach the StreamController's output forwarder
            # + status mappers. Order matters: the controller consumes the
            # runtime's outputs, so the runtime must be started first.
            if resources.harness is not None:
                await resources.harness.start(team_session=session)
                await kv_cache_hooks.register_harness_binding(host, resources.harness)
                await host.stream_controller.start()
                self._wire_round_budget_rails(resources.harness)
        else:
            sess_mgr.release_session()
            memory_manager = resources.memory_manager
            if memory_manager is not None:
                memory_manager.bind_session_id(None)

        team_row_present = False
        if host.role == TeamRole.LEADER and infra.team_backend:
            existing = await infra.team_backend.db.team.get_team(infra.team_backend.team_name)
            if existing is not None:
                team_row_present = True
                non_leader_members = await infra.team_backend.list_member_roster()
                if non_leader_members and all(m.status == MemberStatus.SHUTDOWN.value for m in non_leader_members):
                    team_logger.warning(
                        "[CoordinationKernel] [{}] team {} found with all teammates in SHUTDOWN — "
                        "finalizing prior incomplete cleanup",
                        member_name,
                        infra.team_backend.team_name,
                    )
                    await infra.team_backend.clean_team()
                    team_row_present = False
                else:
                    await host.recover_team()

        # 新回合复牌：清除上一回合的耗尽名册（open_round_budget 在 start
        # 之前按"是否新回合"置位；纯恢复/零输入周期保留名册）。
        if host.role == TeamRole.LEADER and self._pending_round_revival:
            await self._revive_round_exhausted_members()

        if infra.workspace_manager and not infra.workspace_initialized:
            spec = blueprint.spec if blueprint else None
            remote_url = spec.workspace.remote_url if spec and spec.workspace else None
            await infra.workspace_manager.initialize(remote_url=remote_url)
            infra.workspace_initialized = True

        # Wire up the team memory toolkit once the harness and workspace
        # are ready. init_toolkit is idempotent; calling it on every start
        # is safe.
        memory_manager = resources.memory_manager
        harness = resources.harness
        if memory_manager and harness:
            success = await memory_manager.init_toolkit()
            if success:
                harness.register_member_tools(memory_manager)
                if memory_manager.extraction_model is None:
                    memory_manager.set_extraction_model(harness.model)
                await harness.inject_member_memory(
                    memory_manager,
                    query=host.state.pending_user_query or "",
                )

        await host.update_status(MemberStatus.READY)
        # Re-base the idle clock before the poll timers come back. A member
        # that was already idle when the team paused keeps its idle stamp
        # while the monotonic clock runs through the entire pause window, and
        # — having no suspended round to resume — never re-enters IDLE to
        # re-stamp itself. Without this the first POLL_TASK of this run cycle
        # would read the whole pause as idle time and fire a bogus stall
        # nudge. No-op on a cold start (idle_since is None) and for a member
        # that paused mid-round (idle_since is None while BUSY).
        host.refresh_idle_baseline()
        if not self._event_bus.is_running:
            if self._dispatcher is None:
                raise RuntimeError("CoordinationKernel.start() requires setup() before start()")
            await self._event_bus.start(wake_callback=self._build_wake_callback())
        if infra.messager:
            team_name = host.team_name
            if team_name and not self._subscribed_topics:
                await self.subscribe_transport(team_name)
        await self._event_bus.enqueue(InnerEventMessage(event_type=InnerEventType.REFRESH_TEAM_CONTEXT))
        # Re-arm the team-completion rising-edge guard on every start (cold
        # start / resume / recover) so each run cycle evaluates completion
        # independently — a resumed persistent team can conclude again.
        if self._dispatcher is not None:
            self._dispatcher.team_completion.rearm()
        # F_62: arm the scheduler when this run cycle starts against an
        # existing team (the scheduler only exists on scheduled-dispatch
        # leaders) — activation runs the recovery sweep (start pending
        # assignments, judge open reviews).
        if self._scheduler is not None and team_row_present:
            await self._scheduler.activate()
        self._lifecycle_state = "running"
        # Warm / cold resume: a lifecycle pause left this member's round
        # suspended at a clean boundary. Now that the session, stream and
        # event bus are back, continue it in place instead of idling until a
        # new message arrives. Runs last, once the kernel is fully live, and
        # is a no-op when there is nothing suspended to resume.
        await self.resume_paused_round()

    def _build_wake_callback(self):
        """Compose the bus wake callback: coordination first, scheduler second.

        Coordination stays a pure wake-up layer; the scheduler makes its
        decisions strictly after handlers observed the same event. Non-leader
        kernels have no scheduler and keep the bare dispatch path.
        """
        dispatcher = self._dispatcher
        scheduler = self._scheduler
        if scheduler is None:
            return dispatcher.dispatch

        async def _dispatch_then_schedule(event) -> None:
            await dispatcher.dispatch(event)
            await scheduler.on_event(event)

        return _dispatch_then_schedule

    async def notify_team_built(self) -> None:
        """Arm the scheduler once ``build_team`` materialized the team.

        Called by the host's ``on_team_built`` hook inside the build_team tool
        call, so a scheduled-dispatch team's scheduler is live before any
        teammate spawn or task creation — matching the prompt's promise that
        the framework starts assignees. No-op when there is no scheduler
        (autonomous teams, non-leader kernels).
        """
        if self._scheduler is None:
            return
        await self._scheduler.activate()

    async def pause(self) -> None:
        # Idempotent: ignore if not currently running. Pause is only a valid
        # transition from running; paused/stopped/idle short-circuit so the
        # Runner-level finally can safely call pause even after an external
        # stop_coordination has already torn things down.
        if self._lifecycle_state != "running":
            # 防御：历史上半途中断的 teardown 可能留下 state 非 running 但
            # EventBus 仍活的残留态——stop 幂等，顺手收掉周期轮询
            await self._stop_event_bus_safely(context="pause early-return")
            return
        host = self._host
        team_logger.info("[CoordinationKernel] [{}] coordination pausing (persistent)", host.member_name or "?")
        if self._scheduler is not None:
            self._scheduler.deactivate()
        # 周期轮询是最便宜的资源、也是 teardown 中段失败时的泄漏载体（删除会话
        # 删表后僵尸轮询无限报 no such table）——先停轮询；run_loop 保留到
        # finally 的 stop，不改变中段的事件处理语义。pause_polls/stop 均幂等。
        if self._event_bus is not None:
            await self._event_bus.pause_polls()
        members_torn_down = False
        propagating_error: BaseException | None = None
        try:
            # Pause, do not tear down: the round stops at a clean inner-iteration
            # boundary and stays resumable in place. This used to hard-cancel via
            # ``drain_agent_task`` → ``abort(immediate=True)``, which threw away
            # everything the member had done in the round it interrupted mid-way.
            await self.pause_agent_round()
            host.persist_allocator_state()
            # Extract team memories while the session is still bound and the DB
            # is accessible. Moved from finalize_round so extraction runs once
            # per run cycle instead of on every streaming round.
            memory_manager = host.resources.memory_manager
            if memory_manager:
                await memory_manager.extract_after_round()
            if host.role == TeamRole.LEADER:
                await self._mark_live_teammates(MemberStatus.PAUSED)
                await host.spawn_manager.cancel_recovery_tasks()
                await host.spawn_manager.shutdown_all_handles()
                members_torn_down = True
                self._persist_team_lifecycle("paused")
                # Make a later cold start (pause -> stop -> start) continue this
                # round rather than idle waiting for a new message.
                self._persist_pending_resume()
            messager = host.infra.messager
            if messager and host.role == TeamRole.LEADER:
                from openjiuwen.agent_teams.context import get_session_id
                from openjiuwen.agent_teams.schema.events import (
                    EventMessage,
                    TeamStandbyEvent,
                    TeamTopic,
                )

                team_name = host.team_name
                if team_name:
                    try:
                        await messager.publish(
                            topic_id=TeamTopic.TEAM.build(get_session_id(), team_name),
                            message=EventMessage.from_event(TeamStandbyEvent(team_name=team_name)),
                        )
                    except Exception as e:
                        team_logger.error("[CoordinationKernel] Failed to publish TEAM_STANDBY: {}", e)
            await self.unsubscribe_transport()
            await self._stop_event_bus_safely(context="pause")
            self.close_stream()
            host.session_manager.release_session()
            # team_member status update is owned by ``TeamRuntimeManager.finalize_member``
            # so persistence-layer status (lives across restarts) stays decoupled
            # from kernel runtime teardown (volatile). External stop_coordination
            # from leader path must not silently mark teammates SHUTDOWN — that
            # would trip the kernel.start ``all-SHUTDOWN -> clean_team`` guard and
            # delete a team that should be recoverable.
            self._lifecycle_state = "paused"
        except BaseException as exc:
            # 同 stop：主链异常在途时，tail 里的取消不得覆盖它。
            propagating_error = exc
            raise
        finally:
            # EventBus 必停 + 成员 teardown 兜底：前半段（回合暂停/记忆抽取/
            # 状态标记）任一步失败都不能留下周期轮询或成员任务——它们是独立
            # asyncio 任务，leader 侧是唯一能取消它们的位置。两步各自独立
            # 兜底，EventBus 失败不再连带跳过成员拆除。兜底拆成员前先补
            # PAUSED 标记（best-effort）：中段失败可能停在标记之前，而
            # handle 一旦 pop 就再无人能区分「外部暂停」与「成员仍在跑」，
            # 持久态会一直停在 READY/BUSY。
            members_still_live = (
                    host.role == TeamRole.LEADER and not members_torn_down
            )
            main_chain_aborted = self._lifecycle_state == "running"
            if main_chain_aborted and host.role == TeamRole.LEADER:
                # 主链中断（无论成员是否已拆）：lifecycle/pending-resume 可能
                # 未落，best-effort 补写。若释放 session 尚未执行，这里仍
                # 能写进 session；已释放时内部直接 no-op。
                self._persist_pause_fallback_state()
            # 内存态无条件归一：主链的 ``_lifecycle_state = "paused"`` 在
            # 末尾，中段任一 await 失败都会停在 running——包括成员已经拆除
            # （``members_still_live`` 为 False）之后、落 paused 之前的收尾段
            # （TEAM_STANDBY 发布 / 退订 / 停 bus / 关流 / 释放 session）。
            # 资源已按暂停语义拆解，状态机不落 paused 会让后续 pause/stop
            # 把已释放资源当运行中再跑一遍。
            if main_chain_aborted:
                self._lifecycle_state = "paused"
            # latch 之后本内核不再重试 pause：主链没走到的暂停域资源
            # （退订 / 关流 / 释放 session）必须在这里兜底回收，否则
            # 流不关、session 不放，且再 pause 已无法补齐。
            pause_fallback_steps: tuple[TeardownStep, ...] = ()
            if main_chain_aborted:
                pause_fallback_steps = (
                    TeardownStep(
                        "fallback unsubscribe transport",
                        self._step_unsubscribe_transport,
                    ),
                    TeardownStep("fallback close stream", self._step_close_stream),
                    TeardownStep(
                        "fallback release session", self._step_release_session,
                    ),
                )
            await self._teardown_tail(
                context="pause",
                tear_down_members=members_still_live,
                member_status=MemberStatus.PAUSED,
                propagating_error=propagating_error,
                extra_steps=pause_fallback_steps,
            )

    async def _mark_live_teammates(self, target_status: MemberStatus) -> None:
        """Persist ``target_status`` for every spawned teammate before tearing down handles.

        Members that were never started (UNSTARTED), are still spawning
        (STARTING), or are already gone (SHUTDOWN) keep their existing
        status — the mark only applies to runtime that was actually live
        during this round. STARTING in particular has no PAUSED/STOPPED
        edge in ``MEMBER_TRANSITIONS`` (a spawning member has no live
        round to pause), so marking it would only bounce off the DAO
        guard with a misleading error log. Used by both pause
        (writes PAUSED — natural round-end idle) and stop (writes STOPPED —
        external teardown without disbanding the team) so the persistence
        layer captures *why* the teammate runtime went away.
        """
        host = self._host
        team_backend = host.infra.team_backend
        team_name = host.team_name
        if not team_backend or not team_name:
            return
        spawned = set(host.spawn_manager.spawned_handles.keys())
        if not spawned:
            return
        leader = host.member_name
        members = await team_backend.list_member_roster()
        for member in members:
            if member.member_name == leader:
                continue
            if member.member_name not in spawned:
                continue
            try:
                current = MemberStatus(member.status)
            except ValueError:
                continue
            if current in {MemberStatus.UNSTARTED, MemberStatus.STARTING, MemberStatus.SHUTDOWN}:
                continue
            try:
                # set_member_status：DAO 直写补发 MemberStatusChangedEvent，
                # 前端实时看到 PAUSED/STOPPED 而非等快照轮询
                await team_backend.set_member_status(
                    member.member_name,
                    target_status,
                )
            except Exception as e:
                team_logger.error(
                    "[CoordinationKernel] [{}] failed to mark teammate {} {}: {}",
                    leader or "?",
                    member.member_name,
                    target_status.value,
                    e,
                )

    def _persist_pause_fallback_state(self) -> None:
        """Persist pause intent when the pause main chain aborted early.

        The main chain writes the lifecycle hint *after* member teardown and
        the pending-resume marker *after* that; a mid-chain failure skips
        both while the fallback still kills the member runtimes. Mirrors both
        writes best-effort: persistence must never mask the original pause
        error (or the cancellation being propagated).
        """
        try:
            self._persist_team_lifecycle("paused")
            self._persist_pending_resume()
        except Exception as exc:
            team_logger.warning(
                "[CoordinationKernel] [{}] pause fallback persistence "
                "failed: {}",
                self._host.member_name or "?",
                exc,
            )

    def _persist_team_lifecycle(self, lifecycle: str) -> None:
        """Write the team lifecycle hint into the session's per-team bucket."""
        host = self._host
        session = host.session_manager.team_session
        team_name = host.team_name
        if session is None or team_name is None:
            return
        from openjiuwen.agent_teams.runtime.metadata import merge_team_namespace

        try:
            merge_team_namespace(session, team_name, {"lifecycle": lifecycle})
        except Exception as e:
            team_logger.warning(
                "[CoordinationKernel] [{}] failed to persist team lifecycle: {}",
                host.member_name or "?",
                e,
            )

    def _persist_pending_resume(self) -> None:
        """Record what a later cold start needs to continue the paused round.

        ``pause`` suspends the round at a clean inner-iteration boundary and the
        run cycle's teardown commits its context. Persisting this marker makes
        ``pause -> stop -> start`` equivalent to ``pause -> resume``: the
        cold-started harness continues that round instead of idling until a new
        message arrives.

        The recorded query is not replayed into the continuation's context (that
        is restored from the checkpoint) — it drives the rounds that *follow* it:
        a task-plan continuation, or a failure retry.

        Leader-only, mirroring ``_persist_team_lifecycle`` — teammates are shut
        down by ``pause`` and re-spawned from the roster by ``recover_team``, so
        they have no in-memory round to carry across a restart.
        """
        host = self._host
        session = host.session_manager.team_session
        team_name = host.team_name
        harness = host.resources.harness
        if session is None or team_name is None or harness is None:
            return
        # Nothing was suspended (no in-flight round), so nothing to continue.
        if harness.state is not HarnessState.PAUSED:
            return
        from openjiuwen.agent_teams.runtime.metadata import merge_pending_resume

        try:
            merge_pending_resume(
                session,
                team_name,
                {
                    "query": harness.paused_query or "",
                    # 区分「pause 落定在 IDLE（无回合可续）」与
                    # 「II 起轮的暂停回合」（同样空 query 但应照常续跑）——
                    # 冷恢复只在前者跳过 resume。
                    "paused_at_idle": bool(getattr(harness, "paused_at_idle", False)),
                },
            )
        except Exception as e:
            team_logger.warning(
                "[CoordinationKernel] [{}] failed to persist pending resume: {}",
                host.member_name or "?",
                e,
            )

    def _read_pending_resume(self) -> dict | None:
        """Return the persisted cold-resume marker, if any."""
        host = self._host
        session = host.session_manager.team_session
        team_name = host.team_name
        if session is None or team_name is None:
            return None
        from openjiuwen.agent_teams.runtime.metadata import read_pending_resume

        try:
            return read_pending_resume(session, team_name)
        except Exception as e:
            team_logger.warning(
                "[CoordinationKernel] [{}] failed to read pending resume: {}",
                host.member_name or "?",
                e,
            )
            return None

    def _clear_pending_resume(self) -> None:
        """Drop the cold-resume marker once the round has been continued."""
        host = self._host
        session = host.session_manager.team_session
        team_name = host.team_name
        if session is None or team_name is None:
            return
        from openjiuwen.agent_teams.runtime.metadata import clear_pending_resume

        try:
            clear_pending_resume(session, team_name)
        except Exception as e:
            team_logger.warning(
                "[CoordinationKernel] [{}] failed to clear pending resume: {}",
                host.member_name or "?",
                e,
            )

    async def stop(
            self,
            *,
            on_quiesced: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Quiesce coordination and then tear down its runtime resources.

        ``on_quiesced`` runs after new scheduler admission is disabled, the
        active agent task is drained, and allocator state is persisted, but
        before memory, transport, harness, and session resources are released.
        The hook is best-effort: its failure is logged and never blocks the
        original Team stop path.
        """
        # Stop cancels any pending takeover nudge — the round this timer was
        # armed for is no longer running.
        self._cancel_takeover_nudge()
        # Idempotent: terminal state. Pause -> stop is allowed (resources
        # still need close), running -> stop is the normal path, idle/stopped
        # are no-ops.
        if self._lifecycle_state in ("idle", "stopped"):
            # 防御：同 pause——半途中断的历史 teardown 残留态顺手收轮询（幂等）
            await self._stop_event_bus_safely(context="stop early-return")
            return
        host = self._host
        team_logger.info("[CoordinationKernel] [{}] coordination stopping", host.member_name or "?")
        if self._scheduler is not None:
            self._scheduler.deactivate()
        # 同 pause：先停周期轮询（teardown 中段失败时的泄漏载体），run_loop
        # 保留到 finally 的 stop。pause_polls/stop 均幂等。
        if self._event_bus is not None:
            await self._event_bus.pause_polls()
        handles_torn_down = False
        propagating_error: BaseException | None = None
        try:
            await self.drain_agent_task()
            host.persist_allocator_state()
            if on_quiesced is not None:
                try:
                    await on_quiesced()
                except Exception as exc:
                    team_logger.warning(
                        "[CoordinationKernel] [{}] quiesced stop hook failed: {}", host.member_name or "?",
                        exc
                    )
            # Final memory extraction before permanent teardown. Only extract
            # when transitioning directly from running (session still bound).
            # When coming from paused, extraction already happened in pause()
            # and the session is already released — the DB query would fail.
            memory_manager = host.resources.memory_manager
            if memory_manager and self._lifecycle_state == "running":
                await memory_manager.extract_after_round()
            if host.role == TeamRole.LEADER:
                # Mirror of the pause path: mark every spawned teammate so the
                # persistence layer captures why the runtime went away. STOPPED
                # is a non-disbanding teardown — ``recover_team`` re-spawns from
                # here. Done before ``shutdown_all_handles`` so the in-process
                # task cancellations cannot race with the status write.
                await self._mark_live_teammates(MemberStatus.STOPPED)
            await self.unsubscribe_transport()
            await host.spawn_manager.cancel_recovery_tasks()
            await host.spawn_manager.shutdown_all_handles()
            handles_torn_down = True
            if memory_manager:
                await memory_manager.close()
            await self._stop_event_bus_safely(context="stop")
            self.close_stream()
            # Permanent teardown (not round-end): stop the native and drop its
            # process-global sys_operation so a stopped/discarded member does not
            # leak it. The round-end ``finalize_round`` path only calls
            # ``harness.stop`` (kept for reuse on the same session); this stop is
            # where the runtime goes away. Done before ``release_session`` because
            # ``dispose`` tears the native down over its bound session, and it does
            # not always follow a ``finalize_round`` (e.g. external stop_team).
            if host.resources.harness is not None:
                await host.resources.harness.dispose()
            messager = host.infra.messager
            if messager is not None:
                await messager.stop()
            host.session_manager.release_session()
            # See pause(): team_member status update for the agent's own
            # ``team_member`` handle is owned by
            # ``TeamRuntimeManager.finalize_member`` so stop_coordination on a
            # teammate kernel only tears down runtime and leaves persisted
            # status alone — that is what makes stop -> recover possible.
            self._lifecycle_state = "stopped"
        except BaseException as exc:
            # 记下主链在途的异常传给 tail：tail 里到达的取消不得把它顶掉。
            propagating_error = exc
            raise
        finally:
            # EventBus 必停 + 句柄 teardown 兜底（同 pause）：中段任何一步
            # 失败都不能留下周期轮询（僵尸内核会持续查询已删除的团队表）
            # 或成员线程/成员 EventBus。两步各自独立兜底，互不连带。
            # 兜底拆成员前先补 STOPPED 标记，保持「先写状态再 shutdown」
            # 的顺序契约。
            extra_steps: tuple[TeardownStep, ...] = ()
            if self._lifecycle_state != "stopped":
                # 主链没走到末尾的 ``stopped``：兜底已拆成员句柄，内存态
                # 必须同步落终态（与 pause 兜底对称），否则后续 pause/stop
                # 会把已拆资源当运行中再跑一遍。
                self._lifecycle_state = "stopped"
                # latch 之后本内核不再重试：主链没走到的永久资源
                # （流/连接/内存/harness/messager/session）必须在这里兜底
                # 回收，否则会一直泄漏到进程退出。
                extra_steps = self._stop_fallback_release().as_steps()
            await self._teardown_tail(
                context="stop",
                tear_down_members=not handles_torn_down,
                member_status=MemberStatus.STOPPED,
                propagating_error=propagating_error,
                extra_steps=extra_steps,
            )

    async def dispose(self) -> None:
        """Release volatile coordination resources after the owning task ends.

        Spawned members run as one asyncio task, but the event bus runs as
        its own task set (the event loop plus the two 30s poll timers):
        ending the member task — normally or by cancellation — does not
        cascade to them. Without this teardown every pause/stop/restart of
        an in-process member leaves a zombie EventBus polling the mailbox
        and task board until the process exits.

        Deliberately skips the pause/stop life-cycle semantics — memory
        extraction, member status writes, resume markers, messager shutdown:
        those belong to the team transition the leader drives. This only
        tears down what must not outlive the task.

        Every step runs independently (``_run_teardown_steps``), so a
        failure or cancellation in one never skips the EventBus stop. The
        ``_disposed`` latch is set only when no step was interrupted by a
        caller cancellation: an interrupted step broke mid-await and did not
        release its resource, so claiming the kernel disposed would make the
        only remaining retry point a no-op. A partial dispose therefore stays
        retryable (all steps are idempotent); the caller cancellation is
        re-raised either way.

        Idempotent; safe to call after ``pause()``/``stop()``.
        """
        if self._disposed or self._disposing:
            return
        self._disposing = True

        cancelling_before = _task_cancelling_count()
        try:
            if self._scheduler is not None:
                self._scheduler.deactivate()
            # Leader-only one-shot timers (takeover nudge / budget wrap-up
            # wake): cancel them like ``stop`` does, so no timer outlives the
            # kernel it would wake.
            self._cancel_takeover_nudge()
            self._cancel_budget_wake()
            outcome = await self._run_teardown_steps(
                context="dispose",
                steps=(
                    TeardownStep("pause polls", self._step_pause_polls),
                    TeardownStep("unsubscribe transport", self._step_unsubscribe_transport),
                    TeardownStep("stop event bus", self._step_stop_event_bus),
                    TeardownStep("close stream", self._step_close_stream),
                    TeardownStep("close memory", self._step_close_memory),
                    TeardownStep("dispose harness", self._step_dispose_harness),
                    TeardownStep("release session", self._step_release_session),
                ),
            )
            # Latch only when every step actually finished. A caller
            # cancellation can break a step mid-await: the runner still runs
            # the remaining steps, but the interrupted one did not release
            # its resource — claiming `_disposed` would make a later dispose
            # (the only remaining retry point) a no-op and leak that resource.
            if outcome.interrupted_step is None:
                self._lifecycle_state = "stopped"
                self._disposed = True
            else:
                team_logger.warning(
                    "[CoordinationKernel] [{}] dispose step {} interrupted "
                    "by a caller cancellation; not latching so a later "
                    "dispose can retry",
                    self._host.member_name or "?",
                    outcome.interrupted_step,
                )
            if outcome.caller_cancelled:
                raise asyncio.CancelledError()
        finally:
            self._disposing = False
            # 兜底。内层取消（bus 内部任务）只在调用方本次确实被取消时
            # 才向上抛，否则吞掉记日志——不能把已跑完的 dispose 误标成被取消。
            try:
                await self._stop_event_bus_safely(context="dispose fallback")
            except asyncio.CancelledError:
                if _task_cancelling_count() > cancelling_before:
                    raise
                team_logger.warning(
                    "[CoordinationKernel] [{}] dispose fallback bus stop "
                    "observed an inner cancellation; ignoring",
                    self._host.member_name or "?",
                )

    # Teardown steps shared by ``dispose()`` and the ``stop()`` fallback:
    # each is a single idempotent unit fed to ``_run_teardown_steps``, so a
    # failure or cancellation is isolated per step. Keep them idempotent —
    # the stop fallback may run a step the main chain already completed.

    async def _step_pause_polls(self) -> None:
        """Pause periodic polls first — the cheapest leak carrier."""
        if self._event_bus is not None:
            await self._event_bus.pause_polls()

    async def _step_stop_event_bus(self) -> None:
        await self._stop_event_bus_safely(context="dispose")

    async def _step_unsubscribe_transport(self) -> None:
        await self.unsubscribe_transport()

    async def _step_close_stream(self) -> None:
        self.close_stream()

    async def _step_close_memory(self) -> None:
        memory_manager = self._host.resources.memory_manager
        if memory_manager is not None:
            await memory_manager.close()

    async def _step_dispose_harness(self) -> None:
        harness = self._host.resources.harness
        if harness is not None:
            await harness.dispose()

    async def _step_stop_messager(self) -> None:
        messager = self._host.infra.messager
        if messager is not None:
            await messager.stop()

    async def _step_release_session(self) -> None:
        self._host.session_manager.release_session()

    async def _run_teardown_steps(
            self,
            *,
            context: str,
            steps: tuple[TeardownStep, ...],
    ) -> TeardownOutcome:
        """Run teardown steps one by one, isolating failures from each other.

        Each step is awaited independently: a failing step is logged and
        execution continues with the remaining steps. The EventBus stop is
        one such step, so nothing in the teardown chain can skip it any more.

        Args:
            context: Teardown phase named in the error logs.
            steps: The ordered teardown steps.

        Returns:
            A :class:`TeardownOutcome`. ``caller_cancelled`` reports a
            *new* cancellation request targeting the caller task during the
            run (a counter increase — an inner task's cancellation does not
            touch it); the caller restores cancellation semantics after the
            remaining steps have run. ``interrupted_step`` names the step
            whose await was broken by that caller cancellation: it did **not**
            finish, so callers that latch terminal state must not treat its
            resources as released. Inner cancellations (e.g. ``EventBus.stop``
            waiting on the loop task) are logged and swallowed: they say
            nothing about the caller.
        """
        member_name = self._host.member_name or "?"
        cancelling_before = _task_cancelling_count()
        caller_cancelled = False
        interrupted_step: Optional[str] = None
        for step in steps:
            try:
                await step.run()
            except asyncio.CancelledError:
                if _task_cancelling_count() > cancelling_before:
                    caller_cancelled = True
                    interrupted_step = step.name
                    continue
                team_logger.warning(
                    "[CoordinationKernel] [{}] {} {} observed an inner "
                    "cancellation; continuing teardown",
                    member_name,
                    context,
                    step.name,
                )
            except Exception as exc:
                team_logger.error(
                    "[CoordinationKernel] [{}] {} {} failed: {}",
                    member_name,
                    context,
                    step.name,
                    exc,
                )
        return TeardownOutcome(
            caller_cancelled=caller_cancelled,
            interrupted_step=interrupted_step,
        )

    async def _stop_event_bus_safely(self, *, context: str) -> None:
        """Stop the EventBus, logging a failure instead of raising it.

        Teardown calls this from ``finally`` blocks and from the middle of
        chains that still have resources to release afterwards: a bus
        failure must neither abort the remaining steps nor skip the other
        tail fallback. ``CancelledError`` is propagated as-is; callers
        (``_teardown_tail`` / the dispose fallback) compare
        ``_task_cancelling_count()`` deltas to decide whether it was the
        caller task or an inner bus task, and restore cancellation semantics
        only for the former.

        Args:
            context: Teardown phase named in the error log (``pause``,
                ``stop``, ``dispose``, or an early-return probe).
        """
        if self._event_bus is None:
            return
        try:
            await self._event_bus.stop()
        except Exception as exc:
            team_logger.error(
                "[CoordinationKernel] [{}] {} event bus stop failed: {}",
                self._host.member_name or "?",
                context,
                exc,
            )

    async def _teardown_member_handles_safely(
            self,
            *,
            context: str,
            member_status: MemberStatus | None = None,
    ) -> None:
        """Mark live teammates, cancel recovery tasks, shut down member handles.

        Fallback for pause/stop when the main chain aborted before reaching
        them: teammate tasks and their EventBuses are independent asyncio
        tasks, so the leader-side teardown is the only place they can be
        cancelled. When the main chain failed *before* marking the roster,
        the same mark runs first — otherwise the persisted status keeps
        saying READY/BUSY while the runtime is being torn down, violating
        the "write status, then kill" order of S_03 invariant 16. All steps
        always run (``_run_teardown_steps``); marking is best-effort, so a
        broken DB cannot block the handle teardown.

        Args:
            context: Teardown phase named in the error logs.
            member_status: Status to persist for live teammates before their
                handles are dropped; ``None`` when marking is not needed
                (non-leader fallback, or the main chain already marked).
        """
        steps: list[TeardownStep] = []
        if member_status is not None:
            steps.append(
                TeardownStep(
                    "fallback mark live teammates",
                    lambda: self._mark_live_teammates(member_status),
                )
            )
        steps.extend(
            (
                TeardownStep(
                    "fallback cancel recovery tasks",
                    self._host.spawn_manager.cancel_recovery_tasks,
                ),
                TeardownStep(
                    "fallback shutdown member handles",
                    self._host.spawn_manager.shutdown_all_handles,
                ),
            )
        )
        outcome = await self._run_teardown_steps(
            context=context,
            steps=tuple(steps),
        )
        if outcome.caller_cancelled:
            raise asyncio.CancelledError()

    async def _teardown_tail(
            self,
            *,
            context: str,
            tear_down_members: bool,
            member_status: MemberStatus | None = None,
            propagating_error: BaseException | None = None,
            extra_steps: tuple[TeardownStep, ...] = (),
    ) -> None:
        """Best-effort teardown tail shared by ``pause`` and ``stop``.

        Runs from the teardown chain's ``finally``: the EventBus is always
        stopped, and — when the main chain did not get there —
        ``tear_down_members`` tears down the member handles. The steps are
        isolated, so an EventBus failure no longer skips the member
        teardown.

        A cancellation that lands during the tail is deferred until all
        steps have run and then re-raised, so the caller's task stays
        cancelled while the resources are still reclaimed — unless the main
        chain is already unwinding a real error (``propagating_error``):
        raising a fresh ``CancelledError`` from ``finally`` would replace
        that error and hide the actual failure, so the cancellation is only
        logged there and the original exception keeps propagating.

        Args:
            context: Teardown phase named in error logs (``pause``/``stop``).
            tear_down_members: Whether the member-handle fallback is needed
                (the main chain aborted before reaching it).
            member_status: Status to persist for live teammates before they
                are killed (``PAUSED``/``STOPPED``) when the main chain
                aborted before marking them; ``None`` otherwise.
            propagating_error: Exception the main chain is already raising
                when it entered ``finally``; suppresses the deferred
                cancellation so the original failure is not masked.
            extra_steps: Additional best-effort steps run after the member
                teardown (used by ``stop`` to release the permanent
                resources its main chain owns). Every step there must be
                idempotent: the main chain may already have completed some
                of them before it aborted.
        """
        cancelling_before = _task_cancelling_count()
        try:
            await self._stop_event_bus_safely(context=context)
        except asyncio.CancelledError:
            if _task_cancelling_count() <= cancelling_before:
                team_logger.warning(
                    "[CoordinationKernel] [{}] {} event bus stop observed an "
                    "inner cancellation; continuing tail",
                    self._host.member_name or "?",
                    context,
                )
        if tear_down_members:
            try:
                await self._teardown_member_handles_safely(
                    context=context,
                    member_status=member_status,
                )
            except asyncio.CancelledError:
                if _task_cancelling_count() <= cancelling_before:
                    team_logger.warning(
                        "[CoordinationKernel] [{}] {} member teardown observed "
                        "an inner cancellation; continuing tail",
                        self._host.member_name or "?",
                        context,
                    )
        if extra_steps:
            await self._run_teardown_steps(context=context, steps=extra_steps)
        if _task_cancelling_count() > cancelling_before:
            if propagating_error is None:
                raise asyncio.CancelledError()
            # 主链已有异常在途：finally 里再抛 CancelledError 会把它顶掉，
            # 调用方就再也看不到真正的失败原因（且原异常的回溯一起丢失）。
            # 取消只记日志，让原异常继续上抛。
            team_logger.warning(
                "[CoordinationKernel] [{}] {} deferred cancellation suppressed; "
                "the in-flight {} keeps propagating",
                self._host.member_name or "?",
                context,
                type(propagating_error).__name__,
            )

    def _stop_fallback_release(self) -> StopFallbackRelease:
        """Permanent-resource release steps for the ``stop`` fallback.

        Used only when ``stop()``'s main chain aborted: the kernel is then
        latched ``stopped`` and never retried, so whatever the main chain
        did not reach would leak for the process lifetime. Stream and
        transport teardown are included for the same reason (an unclosed
        leader stream leaves the client waiting).

        Every step is idempotent — ``close_stream``, ``memory.close``,
        ``harness.dispose``, ``messager.stop`` and ``release_session`` are
        all safe to call after the main chain already ran them — so the
        steps run unconditionally instead of tracking per-step progress.
        """
        return StopFallbackRelease(
            unsubscribe_transport=TeardownStep(
                "fallback unsubscribe transport", self._step_unsubscribe_transport,
            ),
            close_stream=TeardownStep(
                "fallback close stream", self._step_close_stream,
            ),
            close_memory=TeardownStep(
                "fallback close memory", self._step_close_memory,
            ),
            dispose_harness=TeardownStep(
                "fallback dispose harness", self._step_dispose_harness,
            ),
            stop_messager=TeardownStep(
                "fallback stop messager", self._step_stop_messager,
            ),
            release_session=TeardownStep(
                "fallback release session", self._step_release_session,
            ),
        )

    async def subscribe_transport(self, team_name: str) -> None:
        host = self._host
        messager = host.infra.messager
        if not messager or not self._event_bus:
            return
        from openjiuwen.agent_teams.context import get_session_id
        from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic

        local_member_name = host.member_name or ""

        async def _filter_self(event: EventMessage) -> None:
            for listener in host.state.event_listeners:
                try:
                    await listener(event)
                except Exception as e:
                    team_logger.error("[CoordinationKernel] Event listener error: {}", e)
            if local_member_name and event.sender_id == local_member_name:
                team_logger.debug("[CoordinationKernel] ignoring self-published event: {}", event.event_type)
                # F_62: the scheduler must observe board changes the leader
                # process performed itself (create_task, settle). Coordination
                # must not re-process the echo, so the dropped event degrades
                # to a bare "board changed" scan hint on the same bus loop.
                if (
                        self._scheduler is not None
                        and self._scheduler.is_active
                        and str(event.event_type).startswith("task_")
                ):
                    await self._event_bus.enqueue(
                        InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN)
                    )
                return
            await self._event_bus.enqueue(event)

        session_id = get_session_id()
        await messager.register_direct_message_handler(self._event_bus.enqueue)
        for topic in TeamTopic:
            topic_str = topic.build(session_id, team_name)
            await messager.subscribe(topic_str, _filter_self)
            self._subscribed_topics.append(topic_str)

    async def unsubscribe_transport(self) -> None:
        host = self._host
        messager = host.infra.messager
        if not messager:
            return
        try:
            await messager.unregister_direct_message_handler()
        except Exception:
            team_logger.debug("[CoordinationKernel] failed to unregister direct message handler during cleanup")
        for topic in self._subscribed_topics:
            try:
                await messager.unsubscribe(topic)
            except Exception:
                team_logger.debug("[CoordinationKernel] failed to unsubscribe topic {} during cleanup", topic)
        self._subscribed_topics.clear()

    async def enqueue_user_input(self, inputs: Any) -> None:
        """Push a user-input inner event onto the bus.

        Accepts either a string (used directly as content) or a dict with
        a 'query' key (used to extract the content). The unified entry
        point used by both invoke()/stream() and interact().
        """
        if self._event_bus is None:
            return
        query = inputs.get("query", "") if isinstance(inputs, dict) else inputs
        await self._event_bus.enqueue(
            InnerEventMessage(
                event_type=InnerEventType.USER_INPUT,
                payload={"content": query},
            )
        )

    async def enqueue_initial_mailbox_poll(self) -> None:
        host = self._host
        if host.role == TeamRole.LEADER:
            return
        if self._event_bus is None:
            return
        # The member runtime is already started by ``start`` before coordination
        # runs, so the first mailbox poll is enqueued directly — the old
        # FirstIterationGate wait is gone with the single-supervisor model.
        await self._event_bus.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    async def enqueue_initial_task_poll(self) -> None:
        """Queue the member's one startup board survey (F_69).

        The counterpart of ``enqueue_initial_mailbox_poll``: the mailbox
        sweep picks up messages sent while the member was down, this one
        picks up *work* assigned while it was down. A task assigned at
        creation time is announced only by a transient ``TASK_CLAIMED``
        event, which a member that has not started yet — ``spawn_member``
        leaves it ``UNSTARTED`` — can never receive.

        Leader-excluded for the same reason as the mailbox poll: its board
        survey renders the whole board (or an all-done prompt on an empty
        one), which is not what a leader coming up should be handed. Queued
        after the mailbox poll so the member reads its messages first and
        the board second.
        """
        host = self._host
        if host.role == TeamRole.LEADER:
            return
        if self._event_bus is None:
            return
        await self._event_bus.enqueue(
            InnerEventMessage(event_type=InnerEventType.INITIAL_POLL_TASK),
        )

    async def drain_agent_task(self) -> None:
        """Hard-cancel the in-flight round. Stop / teardown paths only."""
        await self._host.stream_controller.drain_agent_task()

    async def pause_agent_round(self) -> None:
        """Pause the in-flight round at its nearest inner iteration boundary."""
        await self._host.stream_controller.pause_agent()

    async def resume_paused_round(self) -> None:
        """Continue a round that a lifecycle pause suspended, if any.

        Two paths, both reached from the tail of ``start`` (session, stream and
        event bus are back by then):

        - **warm**: the harness is still PAUSED in this process — ``pause`` never
          stops it — so the suspended round is in memory.
        - **cold**: the harness was stopped and rebuilt, its context restored from
          the session checkpoint. The marker ``pause`` persisted names the round's
          originating query, making ``pause -> stop -> start`` behave exactly like
          ``pause -> resume``.

        Without this the member would idle until a new message arrived, silently
        dropping the work it was suspended mid-way through.
        """
        harness = self._host.resources.harness
        if harness is None:
            return

        if harness.state is HarnessState.PAUSED:
            team_logger.info(
                "[CoordinationKernel] [{}] resuming the paused round in place",
                self._host.member_name or "?",
            )
            await self._host.stream_controller.resume_agent()
            self._clear_pending_resume()
            return

        # Cold resume is leader-only: the marker lives in the team namespace
        # and is written only by the leader's pause path
        # (_persist_pending_resume). A teammate cold-started by recover_team
        # must not read it — it names the LEADER's paused query, and every
        # teammate that reads it fabricates a round on a message never
        # addressed to it.
        if self._host.role != TeamRole.LEADER:
            return
        pending = self._read_pending_resume()
        if pending is None:
            return
        query = str(pending.get("query") or "")
        if not query.strip() and pending.get("paused_at_idle"):
            # pause 落定在 IDLE（无在跑回合）写下的标记——无回合可续，
            # 空续轮会把空串当挂起中断的答复。清标记、留在 IDLE 等用户输入
            # （挂起中断态由 checkpoint 保留，文本经 _on_send 放行开轮回填）。
            # 空 query 但非 paused_at_idle（II 起轮的暂停回合）不在此列，
            # 照常走下方 resume_agent(query="") 续跑。
            team_logger.info(
                "[CoordinationKernel] [{}] pending resume marker paused at IDLE; awaiting user input",
                self._host.member_name or "?",
            )
            self._clear_pending_resume()
            return
        team_logger.info(
            "[CoordinationKernel] [{}] resuming the paused round from the session checkpoint",
            self._host.member_name or "?",
        )
        await self._host.stream_controller.resume_agent(query=query)
        self._clear_pending_resume()

    def close_stream(self) -> None:
        self._host.stream_controller.close_stream()

    async def wake_mailbox_if_interrupt_cleared(self) -> None:
        host = self._host
        if host.role != TeamRole.TEAMMATE:
            return
        if host.has_pending_interrupt():
            return
        if self._event_bus is None:
            return
        await self._event_bus.enqueue(
            InnerEventMessage(event_type=InnerEventType.POLL_MAILBOX),
        )

    async def finalize_round(self) -> None:
        """Run round-end cleanup; lifecycle decisions live at the Runner layer.

        Purely tears down this cycle's runtime resources (stream controller
        and native harness). Memory extraction has moved to
        :meth:`pause` / :meth:`stop` so it runs once per run cycle
        rather than on every round.
        """
        host = self._host
        # 回合收尾：撤掉接管提示定时器（迟到的提示不得落进下一回合）。
        self._cancel_takeover_nudge()
        if self._round_budget_ledger is not None:
            self._round_budget_ledger.close(reason="round_finalize")
        # Tear down this cycle's runtime: stop the controller's forwarder/status
        # mappers, then the member runtime (native supervisor). start() rebuilds
        # a fresh native next cycle.
        await host.stream_controller.stop()
        if host.resources.harness is not None:
            await host.resources.harness.stop()
        host.stream_controller.stream_queue = None
