# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team-completion coordination events.

Drives and consumes the team-completion lifecycle. Two responsibilities:

* On ``POLL_TASK`` ticks (leader only, when the leader is idle): evaluate
  the three team-completion conditions via ``TeamBackend.is_team_completed``
  and emit ``TEAM_COMPLETED`` once per rising edge.
* On ``TASK_LIST_DRAINED``: log it and fire every registered completion
  callback. ``TeamAgent`` wires ``TeamSkillRail.notify_team_completed`` in
  here once after the DeepAgent is built (see
  ``register_completion_callback``), so a drained board triggers team-skill
  evolution without waiting for a ``view_task`` call and without a
  per-event rail lookup.
* On ``TEAM_COMPLETED``: consume the event with a structured log — the hook
  point for a future SDK-facing notification.

Evaluation runs on the poll tick rather than reacting to member-status
events because ``kernel._filter_self`` drops the leader's own
``MemberStatusChangedEvent`` — the leader cannot observe its own settle,
so the periodic idle tick is the reliable leader-idle hook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, ClassVar

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.coordination.event_bus import (
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TeamCompletedEvent,
    TeamEvent,
    TeamTopic,
)
from openjiuwen.agent_teams.schema.team import TeamCompletionSnapshot, TeamRole
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.coordination.dispatcher import DispatcherHost, PollController


class TeamCompletionHandler(BaseCoordinationHandler):
    """Drive + consume the team-completion lifecycle."""

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        InnerEventType.POLL_TASK.value: "on_poll_task",
        TeamEvent.TASK_LIST_DRAINED: "on_task_list_drained",
        TeamEvent.TEAM_COMPLETED: "on_team_completed",
    }

    def __init__(
        self,
        host: "DispatcherHost",
        blueprint: TeamAgentBlueprint,
        infra: TeamInfra,
        poll_ctrl: "PollController",
    ) -> None:
        super().__init__(host, blueprint, infra, poll_ctrl)
        # Rising-edge guard: emit TEAM_COMPLETED once when the team becomes
        # complete, re-arm when it leaves the completed state. In-process,
        # single-leader state — a leader restart re-arms it, and a re-emitted
        # event is harmless since consumers treat the event as at-least-once.
        self._team_completed_emitted = False
        # Callbacks fired on TASK_LIST_DRAINED. Populated once after the
        # DeepAgent is built (TeamAgent._register_team_completion_callbacks)
        # — e.g. TeamSkillRail.notify_team_completed. Empty on members that
        # have no such rail, which is what gates the fan-out to the leader.
        self._completion_callbacks: list[Callable[[], Awaitable[None]]] = []

    def register_completion_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register a coroutine fired whenever the task board drains.

        Called at construction wiring time, not per event. Each callback
        runs on every ``TASK_LIST_DRAINED`` this handler receives.
        """
        self._completion_callbacks.append(callback)

    def rearm(self) -> None:
        """Reset the rising-edge guard so the next completion re-emits.

        Called by the kernel on every ``start`` (cold start / resume /
        recover) so each run cycle evaluates team completion independently:
        a paused team that resumes can conclude again without first having
        to leave and re-enter the completed state.
        """
        self._team_completed_emitted = False

    async def on_poll_task(self, event: InnerEventMessage) -> None:
        """Leader-idle tick: evaluate the three completion conditions.

        Gated to the leader (only the leader owns the team-level
        conclusion) and to a genuinely idle leader — a mid-round leader's
        own status is BUSY, which fails condition 1 anyway, so the early
        return just skips a wasted DB scan.
        """
        if self._blueprint.role != TeamRole.LEADER:
            return
        team_backend = self._infra.team_backend
        if team_backend is None:
            return
        if self._round.has_in_flight_round() or self._round.is_agent_running():
            return

        snapshot = await team_backend.is_team_completed()
        if snapshot is None:
            # Falling edge: re-arm so the next rising edge emits again.
            self._team_completed_emitted = False
            return
        if self._team_completed_emitted:
            return

        await self._publish_team_completed(team_backend.team_name, snapshot)
        self._team_completed_emitted = True

        # Persistent teams auto-pause on completion: close the leader stream
        # so the Runner finally drives finalize -> pause. Temporary teams are
        # torn down by their leader via clean_team, not here.
        if self._blueprint.lifecycle == "persistent":
            await self._lifecycle.conclude_completed_round(
                snapshot.member_count,
                snapshot.task_count,
            )

    async def _publish_team_completed(self, team_name: str, snapshot: TeamCompletionSnapshot) -> None:
        """Publish TEAM_COMPLETED on the team topic; log on failure."""
        messager = self._infra.messager
        if messager is None:
            return
        try:
            await messager.publish(
                topic_id=TeamTopic.TEAM.build(get_session_id(), team_name),
                message=EventMessage.from_event(
                    TeamCompletedEvent(
                        team_name=team_name,
                        member_count=snapshot.member_count,
                        task_count=snapshot.task_count,
                    )
                ),
            )
            team_logger.info(
                "[leader] team {} completed: {} members, {} tasks",
                team_name,
                snapshot.member_count,
                snapshot.task_count,
            )
        except Exception as e:
            team_logger.error("Failed to publish TEAM_COMPLETED for team {}: {}", team_name, e)

    async def on_task_list_drained(self, event: EventMessage) -> None:
        """Consume TASK_LIST_DRAINED — log it and fire registered callbacks.

        Callbacks are wired once after construction; the registry being
        non-empty only on the leader (where ``TeamSkillRail`` is mounted)
        is what scopes the fan-out. Each callback is isolated so one
        failure does not skip the rest.
        """
        payload = event.get_payload()
        team_logger.info(
            "task list drained for team {}: {} terminal task(s)",
            payload.team_name,
            payload.task_count,
        )
        for callback in self._completion_callbacks:
            try:
                await callback()
            except Exception as e:
                team_logger.error("task list drained callback failed: {}", e, exc_info=True)

    async def on_team_completed(self, event: EventMessage) -> None:
        """Consume TEAM_COMPLETED — structured log of team completion.

        ``kernel._filter_self`` drops the emitting leader's own copy, so
        this runs on teammates; the leader already logged at emit time.
        """
        payload = event.get_payload()
        team_logger.info(
            "team {} reported completed: {} members, {} tasks",
            payload.team_name,
            payload.member_count,
            payload.task_count,
        )
