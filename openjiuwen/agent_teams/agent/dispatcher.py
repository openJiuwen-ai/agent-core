# coding: utf-8
"""Event dispatcher for TeamAgent coordination events."""

from __future__ import annotations

import time
from typing import (
    TYPE_CHECKING,
    Protocol,
    runtime_checkable,
)

from openjiuwen.agent_teams.agent.coordinator import (
    CoordinationEvent,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.interaction import UserInbox, parse_mention
from openjiuwen.agent_teams.schema.events import MessageEvent, TeamEvent
from openjiuwen.agent_teams.schema.status import MemberStatus, TaskStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.team import TeamSpec
    from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
    from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager


@runtime_checkable
class DispatcherHost(Protocol):
    """Contract between EventDispatcher and its owning agent.

    Defines the minimal surface the dispatcher needs to drive
    coordination — agent internals stay behind this boundary.
    """

    @property
    def role(self) -> TeamRole:
        """Return the current team role."""
        ...

    @property
    def lifecycle(self) -> str:
        """Return the team lifecycle mode."""
        ...

    @property
    def member_name(self) -> str | None:
        """Return the current member_name, or None if unassigned."""
        ...

    @property
    def message_manager(self) -> TeamMessageManager | None:
        """Return the bound message manager, or None if not configured."""
        ...

    @property
    def task_manager(self) -> TeamTaskManager | None:
        """Return the bound task manager, or None if not configured."""
        ...

    @property
    def team_spec(self) -> TeamSpec | None:
        """Return the bound team spec, or None if not configured."""
        ...

    async def has_team_member(self, member_name: str) -> bool:
        """Check whether a team member exists in the backend."""
        ...

    def is_agent_ready(self) -> bool:
        """Return whether the agent has been fully initialized."""
        ...

    def is_agent_running(self) -> bool:
        """Return whether the agent is in an active round."""
        ...

    def has_in_flight_round(self) -> bool:
        """Return whether an agent round is scheduled and not yet finalized."""
        ...

    def has_pending_interrupt(self) -> bool:
        """Return whether an unresolved tool interrupt is pending."""
        ...

    async def start_agent(self, content: str) -> None:
        """Start a new agent round with the given content."""
        ...

    async def follow_up(self, content: str) -> None:
        """Feed content to the currently running agent."""
        ...

    async def cancel_agent(self) -> None:
        """Cancel the running agent task."""
        ...

    async def shutdown_self(self) -> None:
        """Force-shutdown this agent in response to team dissolution."""
        ...

    async def pause_polls(self) -> None:
        """Pause periodic polling in the coordination loop."""
        ...

    async def resume_polls(self) -> None:
        """Resume periodic polling in the coordination loop."""
        ...

    async def steer(self, content: str) -> None:
        """Steer instruction into the running agent."""
        ...

    async def deliver_input(self, content: str, *, use_steer: bool = True) -> None:
        """Guarantee that content reaches the DeepAgent regardless of state."""
        ...

    async def resume_interrupt(self, user_input) -> None:
        """Resume a pending HITL interrupt with structured input."""
        ...


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

    _STALE_CLAIM_SECONDS = 10 * 60.0
    _STALE_PENDING_SECONDS = 10 * 60.0

    def __init__(self, host: DispatcherHost) -> None:
        self._host = host
        # task_id -> wall-clock seconds when we last fired a stale-claim
        # nudge. Used only to throttle follow-up nudges; the ``is this
        # task stale?`` decision itself reads ``task.updated_at`` from the
        # database so we never lose state across process restarts.
        self._last_stale_nudge: dict[str, float] = {}
        # Same idea, but for stale PENDING tasks the leader observes —
        # throttles the leader's self-prompt about long-unclaimed work.
        self._last_pending_nudge: dict[str, float] = {}

    async def dispatch(self, event: CoordinationEvent) -> None:
        """Entry point called by CoordinatorLoop on every wake-up.

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
        member_name = host.member_name
        if not member_name:
            team_logger.debug("no member_name, skipping transport event")
            return

        event_type = event.event_type
        # team_logger.debug("transport event: type={}, member_name={}", event_type, member_name)

        if event_type == TeamEvent.STANDBY:
            team_logger.info("[{}] received TEAM_STANDBY, pausing polls", member_name)
            await host.pause_polls()
            return

        if event_type == TeamEvent.CLEANED:
            # Teammates must abandon their loop when the team row (and their
            # own member row) has been wiped — otherwise they spin forever
            # waiting for events on a dead team. The leader must NEVER
            # shutdown_self from its own CLEANED event: persistent leaders
            # have to survive clean_team to accept the next interaction, and
            # the teardown for temporary leaders is handled by the natural
            # _finalize_round path instead. Skip the leader branch as
            # defense in depth on top of the sender_id self-filter.
            if host.role == TeamRole.LEADER:
                team_logger.debug(
                    "[{}] ignoring TEAM_CLEANED on leader path",
                    member_name,
                )
                return
            team_logger.info(
                "[{}] received TEAM_CLEANED, shutting down coordination",
                member_name,
            )
            await host.shutdown_self()
            return

        if event_type == TeamEvent.TOOL_APPROVAL_RESULT:
            await self._handle_tool_approval_result(event)
            return

        if event_type in self._MEMBER_EVENTS:
            await self._handle_member_event(event)
            return

        if event_type in (TeamEvent.MESSAGE, TeamEvent.BROADCAST) and host.message_manager:
            # Leader auto-acks teammate→user replies. The "user" pseudo-member
            # has no agent process polling its mailbox, so without this the
            # message would stay unread forever and re-fire dispatcher wakes.
            if host.role == TeamRole.LEADER and event_type == TeamEvent.MESSAGE:
                await self._ack_user_bound_message(event)
            await host.resume_polls()
            await self._process_unread_messages(member_name)
            return

        if event_type in self._TASK_EVENTS and not host.has_in_flight_round() and host.task_manager:
            # Gate on the task-level check, not ``is_agent_running``: nudging
            # during the pre-stream or finalize window would call
            # ``_start_agent`` and overwrite the still-live ``_agent_task``.
            await host.resume_polls()
            team_logger.debug("task trigger detected, nudging idle agent: member_name={}", member_name)
            await self._nudge_idle_agent(member_name)

    async def _handle_inner_event(self, event: InnerEventMessage) -> None:
        """Handle local inner events (user input, polling)."""
        host = self._host
        team_logger.debug("inner event received: type={}, payload={}", event.event_type, event.payload)

        if event.event_type == InnerEventType.USER_INPUT:
            content = event.payload.get("content", "")

            mention = await self._resolve_mention(content)
            if mention is not None:
                target, body = mention
                await self._send_user_direct_message(target, body)
                return

            team_logger.info("user_input → deliver_input")
            await host.deliver_input(content)
            return

        if event.event_type == InnerEventType.POLL_TASK:
            member_name = host.member_name
            team_logger.debug("poll task: member_name={}, agent_running={}", member_name, host.is_agent_running())
            if member_name and host.task_manager:
                await self._check_stale_claimed_tasks()
                await self._check_stale_pending_tasks()
                # if not host.is_agent_running():
                #     await self._nudge_idle_agent(member_name, from_poll=True)
            return

        if event.event_type == InnerEventType.POLL_MAILBOX:
            member_name = host.member_name
            team_logger.debug("poll mailbox: member_name={}", member_name)
            if member_name and host.message_manager:
                await self._process_unread_messages(member_name)

    # ------------------------------------------------------------------
    # User @mention helpers
    # ------------------------------------------------------------------

    async def _resolve_mention(self, content: str) -> tuple[str, str] | None:
        """Parse ``@member_name message`` and validate the target exists.

        Returns ``(target, body)`` only when the target is a real roster
        member, otherwise ``None`` so the caller falls through to the
        default (leader) delivery path.
        """
        parsed = parse_mention(content)
        if parsed is None:
            return None
        target, body = parsed
        if not await self._host.has_team_member(target):
            team_logger.warning("@mention target '{}' not found in database, falling through", target)
            return None
        return target, body

    async def _send_user_direct_message(self, to_member_name: str, content: str) -> None:
        """Write a user→member direct message via the user inbox."""
        mm = self._host.message_manager
        if mm is None:
            team_logger.warning("message_manager unavailable, cannot send user direct message")
            return
        msg_id = await UserInbox(mm).direct(to_member_name, content)
        team_logger.info("user direct message sent to {}: {}", to_member_name, msg_id)

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
        member_name = self._host.member_name
        target_id = event.get_payload().member_name
        if target_id is None or target_id != member_name:
            return
        if event.event_type == TeamEvent.MEMBER_CANCELED:
            await self._host.cancel_agent()
        elif event.event_type == TeamEvent.MEMBER_SHUTDOWN:
            await self._process_unread_messages(member_name, use_steer=True)

    async def _handle_leader_member_event(self, event: CoordinationEvent) -> None:
        """Handle member events as the leader — observe other members' lifecycle."""
        payload = event.payload
        target_id = payload.get("member_name", "")
        event_type = event.event_type
        if event_type == TeamEvent.MEMBER_SPAWNED:
            text = t("dispatcher.member_online", target_id=target_id)
        elif event_type == TeamEvent.MEMBER_RESTARTED:
            restart_count = payload.get("restart_count", 1)
            text = t("dispatcher.member_restarted", target_id=target_id, restart_count=restart_count)
        elif event_type == TeamEvent.MEMBER_STATUS_CHANGED:
            old_status = payload.get("old_status")
            new_status = payload.get("new_status")
            text = t(
                "dispatcher.member_status_changed",
                target_id=target_id,
                old_status=old_status,
                new_status=new_status,
            )
            await self._nudge_idle_member_with_stale_claims(
                target_id,
                old_status,
                new_status,
            )
        elif event_type == TeamEvent.MEMBER_EXECUTION_CHANGED:
            text = t(
                "dispatcher.member_execution_changed",
                target_id=target_id,
                old_status=payload.get("old_status"),
                new_status=payload.get("new_status"),
            )
        elif event_type == TeamEvent.MEMBER_SHUTDOWN:
            text = t("dispatcher.member_shutdown", target_id=target_id)
        elif event_type == TeamEvent.MEMBER_CANCELED:
            text = t("dispatcher.member_canceled", target_id=target_id)
        else:
            return

        team_logger.debug(text)

    _IDLE_NUDGE_STATUSES = frozenset({MemberStatus.READY.value, MemberStatus.ERROR.value})

    async def _nudge_idle_member_with_stale_claims(
        self,
        target_id: str,
        old_status: str | None,
        new_status: str | None,
    ) -> None:
        """Remind a member about long-claimed work on transition to READY/ERROR.

        Only tasks whose claim has aged past ``_STALE_CLAIM_SECONDS`` are
        included, and each task is throttled via ``_last_stale_nudge`` —
        shared with the POLL_TASK path — so successive status flips or a
        concurrent poll tick cannot re-nudge within one stale window.
        """
        if not target_id:
            return
        if new_status not in self._IDLE_NUDGE_STATUSES:
            return
        if new_status == old_status:
            return
        task_manager = self._host.task_manager
        message_manager = self._host.message_manager
        if task_manager is None or message_manager is None:
            return

        claimed = await task_manager.get_tasks_by_assignee(
            target_id,
            status=TaskStatus.CLAIMED.value,
        )
        if not claimed:
            return

        now = time.time()
        threshold_ms = self._STALE_CLAIM_SECONDS * 1000
        stale = []
        for task in claimed:
            if task.updated_at is None:
                continue
            if now * 1000 - task.updated_at < threshold_ms:
                continue
            last_nudge = self._last_stale_nudge.get(task.task_id, 0.0)
            if now - last_nudge < self._STALE_CLAIM_SECONDS:
                continue
            stale.append(task)

        if not stale:
            return

        for task in stale:
            self._last_stale_nudge[task.task_id] = now

        lines = [t("dispatcher.stale_claim_header", count=len(stale))]
        for task in stale:
            lines.append(f"- [{task.task_id}] {task.title}: {task.content}")
        await message_manager.send_message("\n".join(lines), target_id)
        team_logger.info(
            "[leader] nudged {} about {} stale claimed task(s) after status → {}",
            target_id,
            len(stale),
            new_status,
        )

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _handle_tool_approval_result(self, event: CoordinationEvent) -> None:
        """Resume a teammate HITL interrupt from a structured approval event."""
        host = self._host
        member_name = host.member_name
        payload = event.get_payload()
        target_id = payload.member_name

        if target_id is None or target_id != member_name:
            return

        from openjiuwen.core.session import InteractiveInput

        interactive_input = InteractiveInput()
        interactive_input.update(
            payload.tool_call_id,
            {
                "approved": payload.approved,
                "feedback": payload.feedback,
                "auto_confirm": payload.auto_confirm,
            },
        )
        team_logger.debug(
            "[{}] received tool approval result for tool_call_id={}, approved={}",
            member_name,
            payload.tool_call_id,
            payload.approved,
        )
        await host.resume_interrupt(interactive_input)

    async def _process_unread_messages(self, member_name: str, *, use_steer: bool = True) -> None:
        """Read unread messages, feed to agent one by one, loop until no new messages.

        Args:
            member_name: Current member ID.
            use_steer: When True, use steer instead of follow_up for running agent.
        """
        host = self._host
        seen_ids: set[str] = set()

        while True:
            all_unread = await self._read_all_unread(member_name)
            new_messages = [m for m in all_unread if m.message_id not in seen_ids]

            if not new_messages:
                break

            team_logger.info("[{}] processing {} unread messages (steer={})", member_name, len(new_messages), use_steer)
            for msg in new_messages:
                seen_ids.add(msg.message_id)
                if host.has_pending_interrupt():
                    team_logger.info(
                        "[{}] deferring mailbox message {} until pending interrupt is resolved",
                        member_name,
                        msg.message_id,
                    )
                    return
                text = self._format_message(msg)
                team_logger.debug("[{}] message from={}, id={}", member_name, msg.from_member_name, msg.message_id)

                await host.deliver_input(text, use_steer=use_steer)
                await host.message_manager.mark_message_read(msg.message_id, member_name)

    async def _ack_user_bound_message(self, event: CoordinationEvent) -> None:
        """Mark a teammate→user direct message as read on the user's behalf.

        The leader observes every direct-message event on the team topic;
        when the recipient is the ``user`` pseudo-member (no real polling
        process), the leader flips ``is_read`` so the message does not
        accumulate as unread and keep waking the dispatcher.
        """
        payload: MessageEvent = event.get_payload()
        if payload.to_member_name != "user":
            return
        mm = self._host.message_manager
        if mm is None:
            return
        await mm.mark_message_read(payload.message_id, "user")
        team_logger.debug(
            "leader auto-acked user-bound message {} from {}",
            payload.message_id,
            payload.from_member_name,
        )

    async def _read_all_unread(self, member_name: str) -> list:
        """Read all unread messages (direct + broadcast).

        Returns merged list sorted by timestamp descending (newest first).
        """
        mm = self._host.message_manager
        direct = await mm.get_messages(to_member_name=member_name, unread_only=True)
        broadcasts = await mm.get_broadcast_messages(member_name=member_name, unread_only=True)
        merged = list(direct) + list(broadcasts)
        merged.sort(key=lambda m: m.timestamp, reverse=True)
        return merged

    @staticmethod
    def _format_message(msg) -> str:
        """Format one TeamMessage for agent input.

        Includes message_id so the agent can call mark_message_read,
        and distinguishes direct vs broadcast messages.
        """
        msg_type = t("dispatcher.msg_type_broadcast") if msg.broadcast else t("dispatcher.msg_type_direct")
        return t(
            "dispatcher.msg_received",
            msg_type=msg_type,
            message_id=msg.message_id,
            sender=msg.from_member_name,
            content=msg.content,
        )

    # ------------------------------------------------------------------
    # Task nudging
    # ------------------------------------------------------------------

    async def _nudge_idle_agent(self, member_name: str, from_poll: bool = False) -> None:
        """Feed task context to an idle agent.

        Leader: reviews full task board to decide whether to re-plan or conclude.
        Teammate: reviews claimable tasks to pick one, plus all tasks for coordination context.

        Args:
            member_name: The calling member's own name.
            from_poll: True when the nudge originates from a routine
                POLL_TASK tick. In that case an idle leader with no
                incomplete tasks (covers all-done / no-tasks / empty-team)
                returns silently — real task-completion prompts arrive
                via the TASK_EVENTS path so polling should not
                re-trigger them.
        """
        host = self._host
        all_tasks = await host.task_manager.list_tasks()
        _terminal = {"completed", "cancelled"}
        incomplete = [t for t in all_tasks if t.status not in _terminal]

        if from_poll and host.role == TeamRole.LEADER and not incomplete:
            return

        team_logger.debug("[{}] nudge_idle_agent: {} incomplete tasks", member_name, len(incomplete))
        if host.role == TeamRole.LEADER:
            if not incomplete:
                lifecycle = host.lifecycle
                if lifecycle == "persistent":
                    prompt = t("dispatcher.all_done_persistent")
                else:
                    prompt = t("dispatcher.all_done_temporary")
                await host.deliver_input(prompt)
                return
            lines = [t("dispatcher.leader_task_board")]
        else:
            claimable = [task for task in incomplete if task.status == "pending" and not task.assignee]
            if not claimable and not incomplete:
                return
            lines = [t("dispatcher.teammate_task_list")]

        for task in incomplete:
            assignee = f" → {task.assignee}" if task.assignee else t("dispatcher.task_unassigned_marker")
            lines.append(f"- [{task.task_id}] [{task.status}] {task.title}: {task.content}{assignee}")

        await host.deliver_input("\n".join(lines))

    async def _check_stale_claimed_tasks(self) -> None:
        """Find claimed tasks that have been running past the stale threshold.

        Measures how long a task has been in CLAIMED state by reading the
        database ``updated_at`` column (bumped on every status transition,
        so for a claimed task it is the claim timestamp). When the
        elapsed time exceeds ``_STALE_CLAIM_SECONDS`` the assignee is
        nudged via the local agent loop (self) or a direct message
        (leader → other member). A per-task throttle prevents follow-up
        polls from re-nudging inside the same stale window.
        """
        host = self._host
        task_manager = host.task_manager
        if task_manager is None:
            return

        claimed = await task_manager.list_tasks(status=TaskStatus.CLAIMED.value)
        own_name = host.member_name
        is_leader = host.role == TeamRole.LEADER
        relevant = [t for t in claimed if t.assignee and (t.assignee == own_name or is_leader)]

        current_ids = {t.task_id for t in relevant}
        for tid in [k for k in self._last_stale_nudge if k not in current_ids]:
            self._last_stale_nudge.pop(tid, None)

        if not relevant:
            return

        now = time.time()
        threshold_ms = self._STALE_CLAIM_SECONDS * 1000
        for task in relevant:
            if task.updated_at is None:
                continue
            elapsed_ms = now * 1000 - task.updated_at
            if elapsed_ms < threshold_ms:
                continue
            last_nudge = self._last_stale_nudge.get(task.task_id, 0.0)
            if now - last_nudge < self._STALE_CLAIM_SECONDS:
                continue
            self._last_stale_nudge[task.task_id] = now
            await self._nudge_stale_claim(task)

    async def _nudge_stale_claim(self, task) -> None:
        """Dispatch a stale-claim nudge to self or to the assigned member."""
        host = self._host
        assignee = task.assignee
        if assignee and assignee == host.member_name:
            await self._self_nudge_stale_claim(task)
        elif host.role == TeamRole.LEADER and assignee:
            await self._leader_nudge_stale_claim(task)

    @staticmethod
    def _format_stale_claim_nudge(task) -> str:
        return t(
            "dispatcher.stale_claim_self",
            task_id=task.task_id,
            title=task.title,
            content=task.content,
        )

    async def _self_nudge_stale_claim(self, task) -> None:
        """Feed a nudge input into the local agent loop."""
        host = self._host
        content = self._format_stale_claim_nudge(task)
        await host.deliver_input(content)
        team_logger.info(
            "[{}] self-nudged stale claimed task {}",
            host.member_name,
            task.task_id,
        )

    async def _leader_nudge_stale_claim(self, task) -> None:
        """Send a direct reminder to the member holding a stale claim."""
        host = self._host
        if host.message_manager is None:
            return
        content = self._format_stale_claim_nudge(task)
        await host.message_manager.send_message(content, task.assignee)
        team_logger.info(
            "[leader] nudged {} about stale claimed task {}",
            task.assignee,
            task.task_id,
        )

    async def _check_stale_pending_tasks(self) -> None:
        """Leader-only: self-prompt about pending tasks that nobody claimed.

        Scans pending tasks via ``task.updated_at`` (bumped on every status
        transition). When a task has been pending past
        ``_STALE_PENDING_SECONDS``, the leader feeds itself an input
        listing those tasks plus a hint to pick the right teammate and
        ping them via ``send_message``. The model decides who to notify
        based on each task's content and the team roster — the dispatcher
        does not try to do the matching itself. A per-task throttle
        prevents follow-up polls from re-prompting inside the same
        stale window.
        """
        host = self._host
        if host.role != TeamRole.LEADER:
            return
        task_manager = host.task_manager
        if task_manager is None:
            return

        pending = await task_manager.list_tasks(status=TaskStatus.PENDING.value)
        now = time.time()
        threshold_ms = self._STALE_PENDING_SECONDS * 1000
        stale_ids = {
            t.task_id for t in pending if t.updated_at is not None and (now * 1000 - t.updated_at) >= threshold_ms
        }

        # GC throttle entries for tasks no longer pending/stale.
        for tid in [k for k in self._last_pending_nudge if k not in stale_ids]:
            self._last_pending_nudge.pop(tid, None)

        fresh: list = []
        for task in pending:
            if task.task_id not in stale_ids:
                continue
            last = self._last_pending_nudge.get(task.task_id, 0.0)
            if now - last < self._STALE_PENDING_SECONDS:
                continue
            fresh.append(task)

        if not fresh:
            return

        for task in fresh:
            self._last_pending_nudge[task.task_id] = now

        lines = [t("dispatcher.stale_pending_header")]
        for task in fresh:
            lines.append(f"- [{task.task_id}] {task.title}: {task.content}")
        content = "\n".join(lines)

        await host.deliver_input(content)
        team_logger.info(
            "[leader] self-prompted about {} stale pending task(s)",
            len(fresh),
        )
