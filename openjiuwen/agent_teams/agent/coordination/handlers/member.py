# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Member-lifecycle coordination events.

Handles all six ``MEMBER_*`` events. Leader observes every member's
transitions; teammate only reacts to events targeting itself
(``MEMBER_CANCELED`` cancels the local round). The on-shutdown
mailbox drain is **not** this handler's concern — ``MessageHandler``
registers its own ``MEMBER_SHUTDOWN`` callback and the framework
fans out both, so this handler stays scoped to lifecycle state only.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.coordination.event_bus import CoordinationEvent
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.status import MemberStatus, TaskStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.timefmt import format_time_context
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import DispatcherHost, PollController


class MemberHandler(BaseCoordinationHandler):
    """Handle MEMBER_* lifecycle events.

    Leader: observe all members' transitions for logging + idle-nudge
    on stale claims.

    Teammate: only react to events targeting self. ``MEMBER_CANCELED``
    cancels the local agent task. ``MEMBER_SHUTDOWN`` does **not**
    drain the mailbox here — that's ``MessageHandler``'s fan-out
    callback, registered on the same event_key.
    """

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        TeamEvent.MEMBER_SPAWNED: "on_member_event",
        TeamEvent.MEMBER_RESTARTED: "on_member_event",
        TeamEvent.MEMBER_STATUS_CHANGED: "on_member_event",
        TeamEvent.MEMBER_EXECUTION_CHANGED: "on_member_event",
        TeamEvent.MEMBER_SHUTDOWN: "on_member_event",
        TeamEvent.MEMBER_CANCELED: "on_member_event",
    }

    _IDLE_NUDGE_STATUSES: ClassVar[frozenset[str]] = frozenset({MemberStatus.READY.value, MemberStatus.ERROR.value})
    _STALE_CLAIM_SECONDS: ClassVar[float] = 10 * 60.0

    def __init__(
        self,
        host: "DispatcherHost",
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: "PollController",
        stale_claim_throttle: dict[str, float],
    ) -> None:
        super().__init__(host, blueprint, infra, poll_ctrl)
        # task_id -> wall-clock seconds when we last fired a stale-claim
        # nudge. Shared by reference with StaleTaskHandler so a member
        # status flip and a poll tick within the same window cannot
        # double-nudge the same task.
        self._last_stale_nudge = stale_claim_throttle

    async def on_member_event(self, event: EventMessage) -> None:
        """Handle MEMBER_* lifecycle events.

        Teammate: only react to events targeting self (cancel only —
        on-shutdown drain is MessageHandler's concern).
        Leader: observe all members' lifecycle transitions.
        """
        if self._blueprint.role == TeamRole.LEADER:
            await self._handle_leader_member_event(event)
        else:
            await self._handle_teammate_member_event(event)

    async def _handle_teammate_member_event(self, event: CoordinationEvent) -> None:
        """Handle member events as a non-leader — only react to events targeting self.

        Teammate ``MEMBER_SHUTDOWN`` teardown is intentionally not handled
        here: a teammate consumes its shutdown message through the mailbox
        drain (``MessageHandler.on_member_shutdown_drain``, registered on
        the same event_key) and a final round, then closes its stream at
        round-end. A human agent has no such autonomous round, so its own
        ``MEMBER_SHUTDOWN`` routes to :meth:`_shutdown_human_agent`.
        """
        member_name = self._blueprint.member_name
        target_id = event.get_payload().member_name
        if target_id is None or target_id != member_name:
            return
        if event.event_type == TeamEvent.MEMBER_CANCELED:
            await self._round.cancel_agent()
        elif event.event_type == TeamEvent.MEMBER_SHUTDOWN and self._blueprint.role == TeamRole.HUMAN_AGENT:
            await self._shutdown_human_agent(event)

    async def _shutdown_human_agent(self, event: CoordinationEvent) -> None:
        """Tear a human-agent avatar down on its own shutdown event.

        A human agent has no autonomous round, so it cannot ride the
        teammate teardown path (mailbox drain -> final round -> round-end
        ``close_stream``). Two cases:

        * A controller-driven round is in flight and the shutdown is not
          forced: leave it alone. ``shutdown_member`` writes
          ``SHUTDOWN_REQUESTED`` before publishing this event, so
          ``_run_one_round``'s round-end check closes the stream once that
          round finishes naturally -- the same path teammates ride. This
          honors "do not interrupt the controller's current turn".
        * Idle (no round to ride) or ``force=True``: collapse the avatar
          directly via ``shutdown_self`` -- idle leaves nothing to
          interrupt (``cooperative_cancel`` is a no-op), and force is a
          deliberate immediate teardown.
        """
        force = getattr(event.get_payload(), "force", False)
        if force or not self._round.has_in_flight_round():
            await self._lifecycle.shutdown_self()

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

    async def _nudge_idle_member_with_stale_claims(
        self,
        target_id: str,
        old_status: str | None,
        new_status: str | None,
    ) -> None:
        """Remind a member about long-claimed work on transition to READY/ERROR.

        Only tasks whose claim has aged past ``_STALE_CLAIM_SECONDS``
        are included, and each task is throttled via
        ``_last_stale_nudge`` — shared with the POLL_TASK path — so
        successive status flips or a concurrent poll tick cannot
        re-nudge within one stale window.
        """
        if not target_id:
            return
        if new_status not in self._IDLE_NUDGE_STATUSES:
            return
        if new_status == old_status:
            return
        task_manager = self._infra.task_manager
        message_manager = self._infra.message_manager
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

        now_ms = get_current_time()
        lines = [t("dispatcher.stale_claim_header", count=len(stale))]
        for task in stale:
            time_info = format_time_context(task.updated_at, now_ms)
            lines.append(f"- [{task.task_id}] {task.title}: {task.content} ({time_info})")
        await message_manager.send_message("\n".join(lines), target_id)
        team_logger.info(
            "[leader] nudged {} about {} stale claimed task(s) after status → {}",
            target_id,
            len(stale),
            new_status,
        )
