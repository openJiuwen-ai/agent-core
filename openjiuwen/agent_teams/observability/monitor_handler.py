# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenTelemetry handler that consumes TeamAgent EventMessage stream.

Registered via ``TeamAgent.add_event_listener``. Translates internal
``EventMessage`` instances (team / member / task / message events)
into OTel spans and span events.

Span vs event policy:
    - ``team_created`` -> ``team_cleaned``: long-lived team root span
      that hosts member / message events as span events.
    - ``task_created`` -> terminal task event: per-task span. Not nested
      under the team span because tasks may move between members and
      OTel parent linkage doesn't reflect that.
    - ``member_*`` and ``message`` / ``broadcast``: lightweight events
      attached to the team span; opening a span per chat message would
      explode trace cardinality.
"""

from __future__ import annotations

from typing import Any

from opentelemetry.trace import (
    Span,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
)

from openjiuwen.agent_teams.observability.config import ObservabilityConfig
from openjiuwen.agent_teams.observability.semconv import (
    AT_EVENT_TYPE,
    AT_MEMBER_NAME,
    AT_MEMBER_RESTART_COUNT,
    AT_MEMBER_RESTART_REASON,
    AT_MEMBER_SHUTDOWN_FORCE,
    AT_MEMBER_STATUS_NEW,
    AT_MEMBER_STATUS_OLD,
    AT_MESSAGE_BROADCAST,
    AT_MESSAGE_FROM,
    AT_MESSAGE_ID,
    AT_MESSAGE_TO,
    AT_TASK_ASSIGNEE,
    AT_TASK_ID,
    AT_TASK_STATUS,
    AT_TEAM_DISPLAY_NAME,
    AT_TEAM_NAME,
)
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.core.common.logging import team_logger


_TRACER_NAME = "openjiuwen.agent_teams.observability.monitor"

_TASK_OPEN_TYPES = frozenset({TeamEvent.TASK_CREATED})
_TASK_CLOSE_TYPES = frozenset(
    {
        TeamEvent.TASK_COMPLETED,
        TeamEvent.TASK_CANCELLED,
        TeamEvent.TASK_UNBLOCKED,
    },
)
_MEMBER_TYPES = frozenset(
    {
        TeamEvent.MEMBER_SPAWNED,
        TeamEvent.MEMBER_RESTARTED,
        TeamEvent.MEMBER_STATUS_CHANGED,
        TeamEvent.MEMBER_EXECUTION_CHANGED,
        TeamEvent.MEMBER_SHUTDOWN,
        TeamEvent.MEMBER_CANCELED,
    },
)
_MESSAGE_TYPES = frozenset({TeamEvent.MESSAGE, TeamEvent.BROADCAST})


class OtelTeamMonitorHandler:
    """Single async callable consumed by ``TeamAgent.add_event_listener``.

    Holds bookkeeping state for open team / task spans so terminal
    events can find their span. Multiple teams are supported in the
    same process: spans are keyed by team_name.
    """

    def __init__(
        self,
        config: ObservabilityConfig,
        *,
        tracer: Tracer | None = None,
    ) -> None:
        """Bind the handler to runtime configuration.

        Args:
            config: Active observability configuration.
            tracer: Optional explicit Tracer. When omitted the handler
                resolves the active observability tracer at call time,
                so it tracks the provider created by ``init_observability``
                even when the global TracerProvider has been frozen by
                an earlier test run.
        """
        self._config = config
        self._injected_tracer = tracer
        self._team_spans: dict[str, Span] = {}
        self._task_spans: dict[str, Span] = {}

    def _tracer(self) -> Tracer:
        """Resolve the tracer lazily so we follow the active provider."""
        if self._injected_tracer is not None:
            return self._injected_tracer
        from openjiuwen.agent_teams.observability.setup import get_tracer

        return get_tracer(_TRACER_NAME)

    async def __call__(self, event: EventMessage) -> None:
        """Dispatch on event_type to the appropriate branch."""
        try:
            etype = event.event_type
            payload: dict[str, Any] = event.payload or {}
            team_name = str(payload.get("team_name") or "")

            if etype == TeamEvent.CREATED:
                self._open_team_span(team_name, payload)
            elif etype == TeamEvent.CLEANED:
                self._close_team_span(team_name)
            elif etype == TeamEvent.STANDBY:
                self._record_team_event(team_name, etype, attrs={AT_EVENT_TYPE: etype})
            elif etype in _TASK_OPEN_TYPES:
                self._open_task_span(team_name, payload)
            elif etype in _TASK_CLOSE_TYPES:
                self._close_task_span(payload, etype)
            elif etype == TeamEvent.TASK_UPDATED or etype == TeamEvent.TASK_CLAIMED:
                self._record_task_event(payload, etype)
            elif etype in _MEMBER_TYPES:
                self._record_member_event(team_name, payload, etype)
            elif etype in _MESSAGE_TYPES:
                self._record_message_event(team_name, payload, etype)
            # Other internal events (worktree, workspace, plan_approval, ...)
            # are intentionally dropped. They can be added later by
            # extending the dispatch table.
        except Exception as exc:
            team_logger.warning(
                "otel monitor handler failed for {}: {}",
                event.event_type,
                exc,
            )

    # ------------------------------------------------------------------
    # Team span lifecycle
    # ------------------------------------------------------------------

    def _open_team_span(self, team_name: str, payload: dict[str, Any]) -> None:
        """Open a long-lived team root span."""
        if team_name in self._team_spans:
            return
        span = self._tracer().start_span(name=f"team.{team_name}", kind=SpanKind.INTERNAL)
        span.set_attribute(AT_TEAM_NAME, team_name)
        span.set_attribute(AT_TEAM_DISPLAY_NAME, str(payload.get("display_name", team_name)))
        span.set_attribute(AT_EVENT_TYPE, TeamEvent.CREATED)
        leader = payload.get("leader_member_name")
        if leader:
            span.set_attribute("agentteam.team.leader", str(leader))
        self._team_spans[team_name] = span

    def _close_team_span(self, team_name: str) -> None:
        """Close the long-lived team span if present."""
        span = self._team_spans.pop(team_name, None)
        if span is None:
            return
        span.set_status(Status(StatusCode.OK))
        span.end()

    def _record_team_event(
        self,
        team_name: str,
        name: str,
        *,
        attrs: dict[str, Any],
    ) -> None:
        """Add an event to the team span if it exists."""
        span = self._team_spans.get(team_name)
        if span is None:
            return
        span.add_event(name=name, attributes=attrs)

    # ------------------------------------------------------------------
    # Task span lifecycle
    # ------------------------------------------------------------------

    def _open_task_span(self, team_name: str, payload: dict[str, Any]) -> None:
        """Open a per-task span keyed by task_id."""
        task_id = str(payload.get("task_id") or "")
        if not task_id or task_id in self._task_spans:
            return
        span = self._tracer().start_span(name=f"task.{task_id}", kind=SpanKind.INTERNAL)
        span.set_attribute(AT_TASK_ID, task_id)
        if team_name:
            span.set_attribute(AT_TEAM_NAME, team_name)
        status = payload.get("status")
        if status:
            span.set_attribute(AT_TASK_STATUS, str(status))
        assignee = payload.get("assignee") or payload.get("member_name")
        if assignee:
            span.set_attribute(AT_TASK_ASSIGNEE, str(assignee))
        self._task_spans[task_id] = span

    def _close_task_span(self, payload: dict[str, Any], etype: str) -> None:
        """Close the task span on terminal event."""
        task_id = str(payload.get("task_id") or "")
        span = self._task_spans.pop(task_id, None)
        if span is None:
            return
        span.set_attribute(AT_TASK_STATUS, etype.replace("task_", ""))
        span.set_status(Status(StatusCode.OK))
        span.end()

    def _record_task_event(self, payload: dict[str, Any], etype: str) -> None:
        """Add an event onto the existing task span (update / claim)."""
        task_id = str(payload.get("task_id") or "")
        span = self._task_spans.get(task_id)
        if span is None:
            return
        attrs: dict[str, Any] = {AT_EVENT_TYPE: etype, AT_TASK_ID: task_id}
        member = payload.get("member_name")
        if member:
            attrs[AT_TASK_ASSIGNEE] = str(member)
        span.add_event(name=etype, attributes=attrs)

    # ------------------------------------------------------------------
    # Member / message events as span events on the team span
    # ------------------------------------------------------------------

    def _record_member_event(
        self,
        team_name: str,
        payload: dict[str, Any],
        etype: str,
    ) -> None:
        """Attach member event to the team span."""
        attrs: dict[str, Any] = {
            AT_EVENT_TYPE: etype,
            AT_MEMBER_NAME: str(payload.get("member_name") or ""),
        }
        if "old_status" in payload:
            attrs[AT_MEMBER_STATUS_OLD] = str(payload.get("old_status") or "")
        if "new_status" in payload:
            attrs[AT_MEMBER_STATUS_NEW] = str(payload.get("new_status") or "")
        if "reason" in payload:
            attrs[AT_MEMBER_RESTART_REASON] = str(payload.get("reason") or "")
        if "restart_count" in payload:
            attrs[AT_MEMBER_RESTART_COUNT] = int(payload.get("restart_count") or 0)
        if "force" in payload:
            attrs[AT_MEMBER_SHUTDOWN_FORCE] = bool(payload.get("force"))
        self._record_team_event(team_name, etype, attrs=attrs)

    def _record_message_event(
        self,
        team_name: str,
        payload: dict[str, Any],
        etype: str,
    ) -> None:
        """Attach message / broadcast event to the team span."""
        attrs: dict[str, Any] = {
            AT_EVENT_TYPE: etype,
            AT_MESSAGE_ID: str(payload.get("message_id") or ""),
            AT_MESSAGE_FROM: str(payload.get("from_member_name") or ""),
            AT_MESSAGE_TO: str(payload.get("to_member_name") or ""),
            AT_MESSAGE_BROADCAST: etype == TeamEvent.BROADCAST,
        }
        self._record_team_event(team_name, etype, attrs=attrs)
