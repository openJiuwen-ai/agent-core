# coding: utf-8
"""Event dispatcher for TeamAgent coordination events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from openjiuwen.agent_teams.agent.coordination import (
    CoordinationEvent,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.team_events import TeamEvent
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
    from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager


@runtime_checkable
class DispatcherHost(Protocol):
    """Contract between EventDispatcher and its owning agent.

    Defines the minimal surface the dispatcher needs to drive
    coordination — agent internals stay behind this boundary.
    """

    @property
    def role(self) -> TeamRole: ...

    @property
    def lifecycle(self) -> str: ...

    @property
    def member_id(self) -> str | None: ...

    @property
    def message_manager(self) -> TeamMessageManager | None: ...

    @property
    def task_manager(self) -> TeamTaskManager | None: ...

    def is_agent_ready(self) -> bool: ...

    def is_agent_running(self) -> bool: ...

    async def start_agent(self, content: str) -> None: ...

    async def follow_up(self, content: str) -> None: ...

    async def cancel_agent(self) -> None: ...

    async def steer(self, content: str) -> None: ...


class EventDispatcher:
    """Dispatches coordination events to the appropriate handler.

    Works through the DispatcherHost protocol — never reaches
    into the concrete agent's private members.
    """

    _TASK_EVENTS = frozenset(
        {
            TeamEvent.TASK_CREATED,
            TeamEvent.TASK_UPDATED,
            TeamEvent.TASK_CLAIMED,
            TeamEvent.TASK_COMPLETED,
            TeamEvent.TASK_CANCELLED,
            TeamEvent.TASK_UNBLOCKED,
        }
    )

    _MEMBER_EVENTS = frozenset(
        {
            TeamEvent.MEMBER_SPAWNED,
            TeamEvent.MEMBER_RESTARTED,
            TeamEvent.MEMBER_STATUS_CHANGED,
            TeamEvent.MEMBER_EXECUTION_CHANGED,
            TeamEvent.MEMBER_SHUTDOWN,
            TeamEvent.MEMBER_CANCELED,
        }
    )

    def __init__(self, host: DispatcherHost) -> None:
        self._host = host

    async def dispatch(self, event: CoordinationEvent) -> None:
        """Entry point called by CoordinationLoop on every wake-up.

        Dispatches to inner-event or transport-event handling.
        """
        host = self._host
        if not host.is_agent_ready():
            team_logger.debug("agent not ready, skipping coordination wake")
            return

        if isinstance(event, InnerEventMessage):
            await self._handle_inner_event(event)
            return

        # --- Transport events (cross-process EventMessage) ---
        member_id = host.member_id
        if not member_id:
            team_logger.debug("no member_id, skipping transport event")
            return

        event_type = event.event_type
        team_logger.debug("transport event: type={}, member_id={}", event_type, member_id)

        if event_type in self._MEMBER_EVENTS:
            await self._handle_member_event(event)
            return

        if event_type in (TeamEvent.MESSAGE, TeamEvent.BROADCAST) and host.message_manager:
            await self._process_unread_messages(member_id)
            return

        if event_type in self._TASK_EVENTS and not host.is_agent_running() and host.task_manager:
            team_logger.debug("task trigger detected, nudging idle agent: member_id={}", member_id)
            await self._nudge_idle_agent(member_id)

    async def _handle_inner_event(self, event: InnerEventMessage) -> None:
        """Handle local inner events (user input, polling)."""
        host = self._host
        team_logger.debug("inner event received: type={}, payload={}", event.event_type, event.payload)

        if event.event_type == InnerEventType.USER_INPUT:
            content = event.payload.get("content", "")
            if host.is_agent_running():
                team_logger.info("user_input → follow_up (agent running)")
                await host.follow_up(content)
            else:
                team_logger.info("user_input → start_agent (agent idle)")
                await host.start_agent(content)
            return

        if event.event_type == InnerEventType.POLL_TASK:
            member_id = host.member_id
            team_logger.debug("poll task: member_id={}, agent_running={}", member_id, host.is_agent_running())
            if member_id and not host.is_agent_running() and host.task_manager:
                await self._nudge_idle_agent(member_id)
            return

        if event.event_type == InnerEventType.POLL_MAILBOX:
            member_id = host.member_id
            team_logger.debug("poll mailbox: member_id={}", member_id)
            if member_id and host.message_manager:
                await self._process_unread_messages(member_id)

    # ------------------------------------------------------------------
    # Member events
    # ------------------------------------------------------------------

    async def _handle_member_event(self, event: CoordinationEvent) -> None:
        """Handle member lifecycle events.

        Teammate: handle cancel events targeting self.
        Leader: observe all other members' lifecycle events.
        """
        if self._host.role == TeamRole.LEADER:
            await self._handle_leader_member_event(event)
        else:
            await self._handle_teammate_member_event(event)

    async def _handle_teammate_member_event(self, event: CoordinationEvent) -> None:
        """Handle member events as a teammate — only react to events targeting self."""
        member_id = self._host.member_id
        target_id = event.get_payload().member_id
        if target_id is None or target_id != member_id:
            return
        if event.event_type == TeamEvent.MEMBER_CANCELED:
            await self._host.cancel_agent()
        elif event.event_type == TeamEvent.MEMBER_SHUTDOWN:
            await self._process_unread_messages(member_id, use_steer=True)

    async def _handle_leader_member_event(self, event: CoordinationEvent) -> None:
        """Handle member events as the leader — observe other members' lifecycle."""
        payload = event.payload
        target_id = payload.get("member_id", "")
        event_type = event.event_type
        if event_type == TeamEvent.MEMBER_SPAWNED:
            text = f"[成员事件] 成员 {target_id} 已上线"
        elif event_type == TeamEvent.MEMBER_RESTARTED:
            restart_count = payload.get("restart_count", 1)
            text = f"[成员事件] 成员 {target_id} 已重启 (第{restart_count}次)"
        elif event_type == TeamEvent.MEMBER_STATUS_CHANGED:
            text = (
                f"[成员事件] 成员 {target_id} 状态变更: "
                f"{payload.get('old_status')} → {payload.get('new_status')}"
            )
        elif event_type == TeamEvent.MEMBER_EXECUTION_CHANGED:
            text = (
                f"[成员事件] 成员 {target_id} 执行状态变更: "
                f"{payload.get('old_status')} → {payload.get('new_status')}"
            )
        elif event_type == TeamEvent.MEMBER_SHUTDOWN:
            text = f"[成员事件] 成员 {target_id} 已关闭"
        elif event_type == TeamEvent.MEMBER_CANCELED:
            text = f"[成员事件] 成员 {target_id} 已取消"
        else:
            return

        team_logger.info(text)

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _process_unread_messages(self, member_id: str, *, use_steer: bool = False) -> None:
        """Read unread messages, feed to agent one by one, loop until no new messages.

        Args:
            member_id: Current member ID.
            use_steer: When True, use steer instead of follow_up for running agent.
        """
        host = self._host
        seen_ids: set[str] = set()

        while True:
            all_unread = await self._read_all_unread(member_id)
            new_messages = [m for m in all_unread if m.message_id not in seen_ids]

            if not new_messages:
                break

            team_logger.info("[{}] processing {} unread messages (steer={})", member_id, len(new_messages), use_steer)
            for msg in new_messages:
                seen_ids.add(msg.message_id)
                text = self._format_message(msg)
                team_logger.info("[{}] message from={}, id={}", member_id, msg.from_member, msg.message_id)

                if not host.is_agent_running():
                    await host.start_agent(text)
                elif use_steer:
                    await host.steer(text)
                else:
                    await host.follow_up(text)
                await host.message_manager.mark_message_read(msg.message_id, member_id)

    async def _read_all_unread(self, member_id: str) -> list:
        """Read all unread messages (direct + broadcast).

        Returns merged list sorted by timestamp descending (newest first).
        """
        mm = self._host.message_manager
        direct = await mm.get_messages(to_member=member_id, unread_only=True)
        broadcasts = await mm.get_broadcast_messages(member_id=member_id, unread_only=True)
        merged = list(direct) + list(broadcasts)
        merged.sort(key=lambda m: m.timestamp, reverse=True)
        return merged

    @staticmethod
    def _format_message(msg) -> str:
        """Format one TeamMessage for agent input.

        Includes message_id so the agent can call mark_message_read,
        and distinguishes direct vs broadcast messages.
        """
        msg_type = "广播消息" if msg.broadcast else "单播消息"
        return (
            f"[收到{msg_type}] message_id={msg.message_id}, "
            f"来自: {msg.from_member}\n"
            f"内容: {msg.content}\n"
            f"提示: 如果对方在提问或等待回复，请务必通过 send_message 回复 {msg.from_member}"
        )

    # ------------------------------------------------------------------
    # Task nudging
    # ------------------------------------------------------------------

    async def _nudge_idle_agent(self, member_id: str) -> None:
        """Feed task context to an idle agent.

        Leader: reviews full task board to decide whether to re-plan or conclude.
        Teammate: reviews claimable tasks to pick one, plus all tasks for coordination context.
        """
        host = self._host
        all_tasks = await host.task_manager.list_tasks()
        incomplete = [
            t
            for t in all_tasks
            if t.status
               not in (
                   "completed",
                   "cancelled",
               )
        ]

        team_logger.info("[{}] nudge_idle_agent: {} incomplete tasks", member_id, len(incomplete))
        if host.role == TeamRole.LEADER:
            if not incomplete:
                lifecycle = host.lifecycle
                if lifecycle == "persistent":
                    prompt = (
                        "所有任务已完成。请汇总本轮工作成果。"
                        "团队继续保持运行，等待新的任务指令。"
                    )
                else:
                    prompt = (
                        "所有任务已完成。请汇总团队工作成果，"
                        "然后依次调用 shutdown_member 关闭所有成员，"
                        "等待所有成员状态转为 shutdown 后，"
                        "调用 clean_team 解散团队。"
                    )
                await host.start_agent(prompt)
                return
            lines = [
                "当前任务看板如下，请审查：\n"
                "- 是否需要调整任务（增删、修改、调整依赖）\n"
                "- 就绪任务是否需要指派给 teammate\n"
                "- 整体进度是否符合预期",
            ]
        else:
            claimable = [t for t in incomplete if t.status == "pending" and not t.assignee]
            if not claimable and not incomplete:
                return
            lines = [
                "当前任务列表如下：\n- 请认领适合你领域的待领取任务\n- 了解相关任务的执行者，必要时与他们协调配合",
            ]

        for task in incomplete:
            assignee = f" → {task.assignee}" if task.assignee else " (待领取)"
            lines.append(f"- [{task.task_id}] [{task.status}] {task.title}: {task.content}{assignee}")

        await host.start_agent("\n".join(lines))
