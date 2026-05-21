# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Task-board coordination events.

Owns ``TASK_CLAIMED`` (targeted assignment to one member) and the
five board-state events (``TASK_CREATED`` / ``TASK_UPDATED`` /
``TASK_COMPLETED`` / ``TASK_CANCELLED`` / ``TASK_UNBLOCKED``) that
nudge an idle agent to re-evaluate the board. Stale-task sweeping
on poll ticks lives in :class:`StaleTaskHandler`.
"""

from __future__ import annotations

from typing import ClassVar

from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger


class TaskBoardHandler(BaseCoordinationHandler):
    """Handle TASK_CLAIMED + 5 task-board state-transition events."""

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        # Targeted assignment (message reaches the assignee directly)
        TeamEvent.TASK_CLAIMED: "on_task_claimed",
        # Task board (everything except TASK_CLAIMED nudges idle agent)
        TeamEvent.TASK_CREATED: "on_task_board_event",
        TeamEvent.TASK_UPDATED: "on_task_board_event",
        TeamEvent.TASK_COMPLETED: "on_task_board_event",
        TeamEvent.TASK_CANCELLED: "on_task_board_event",
        TeamEvent.TASK_UNBLOCKED: "on_task_board_event",
    }

    async def on_task_claimed(self, event: EventMessage) -> None:
        """Directed assignment from another node.

        Self-claims are filtered upstream via ``sender_id``. When the
        claim targets self, route through ``deliver_input`` (steer /
        queue / start, picked by round state) and skip the board nudge
        — the targeted message already names the task. When the claim
        targets someone else, fall through to ``on_task_board_event``
        so the local idle agent (typically the leader observing
        teammate claims) still gets nudged with the updated board.
        Without this fallback an idle leader would miss the board
        change until the next stale-pending poll, which can be up to
        ``_STALE_PENDING_SECONDS`` away.

        Self-assignment rendering is role-aware: a teammate / leader
        sees the teammate-oriented ``dispatcher.task_assigned_to_self``
        prompt ("call view_task and start working"). A human_agent
        avatar sees the HITT-specific ``hitt.task_assigned_to_self_human``
        prompt, which frames the event as a notification for the
        controlling human and tells the avatar LLM not to autonomously
        call tools — the avatar's actions are driven only by Inbox
        instructions from its controller.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        payload = event.get_payload()
        if payload.member_name != member_name:
            await self.on_task_board_event(event)
            return
        await self._poll.resume_polls()

        backend = self._infra.team_backend
        is_self_human = backend is not None and backend.is_human_agent(member_name)
        if is_self_human:
            # Title lookup is best-effort: a glitch must not break the
            # dispatch loop. Same exception discipline as
            # ``MessageHandler._notify_human_agent_inbound``.
            title = ""
            try:
                task = await self._infra.task_manager.get(payload.task_id)
                if task is not None:
                    title = task.title or ""
            except Exception as exc:
                team_logger.warning(
                    "task_assigned_to_human_agent: title lookup failed for {}: {}",
                    payload.task_id,
                    exc,
                )
            content = t(
                "hitt.task_assigned_to_self_human",
                task_id=payload.task_id,
                title=title,
            )
        else:
            content = t("dispatcher.task_assigned_to_self", task_id=payload.task_id)

        team_logger.info(
            "[{}] received TASK_CLAIMED for self, task_id={}, human_agent={}",
            member_name,
            payload.task_id,
            is_self_human,
        )
        await self._round.deliver_input(content)

    async def on_task_board_event(self, event: EventMessage) -> None:
        """Nudge idle agent on TASK_CREATED/UPDATED/COMPLETED/CANCELLED/UNBLOCKED.

        Gates on the task-level check, not ``is_agent_running``: nudging
        during the pre-stream or finalize window would call
        ``_start_agent`` and overwrite the still-live agent task.
        TASK_CLAIMED is routed separately to ``on_task_claimed``.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.task_manager is None:
            return
        await self._poll.resume_polls()
        team_logger.debug("task trigger detected, nudging idle agent: member_name={}", member_name)
        await self._nudge_idle_agent(member_name)

    async def _nudge_idle_agent(self, member_name: str, from_poll: bool = False) -> None:
        """Feed task context to an idle agent.

        Leader: reviews full task board to decide whether to re-plan or conclude.
        Teammate: reviews claimable tasks to pick one, plus all tasks for coordination context.

        Args:
            member_name: The calling member's own name.
            from_poll: True when the nudge originates from a routine
                POLL_TASK tick. In that case an idle leader with no
                incomplete tasks (covers all-done / no-tasks /
                empty-team) returns silently — real task-completion
                prompts arrive via the TASK_EVENTS path so polling
                should not re-trigger them.
        """
        all_tasks = await self._infra.task_manager.list_tasks()
        terminal = {"completed", "cancelled"}
        incomplete = [tk for tk in all_tasks if tk.status not in terminal]

        if from_poll and self._blueprint.role == TeamRole.LEADER and not incomplete:
            return

        team_logger.debug("[{}] nudge_idle_agent: {} incomplete tasks", member_name, len(incomplete))
        if self._blueprint.role == TeamRole.LEADER:
            if not incomplete:
                lifecycle = self._blueprint.lifecycle
                if lifecycle == "persistent":
                    prompt = t("dispatcher.all_done_persistent")
                else:
                    prompt = t("dispatcher.all_done_temporary")
                await self._round.deliver_input(prompt)
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

        await self._round.deliver_input("\n".join(lines))
