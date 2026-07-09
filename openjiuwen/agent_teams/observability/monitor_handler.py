# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenTelemetry handler that consumes TeamAgent EventMessage stream.

Team span is managed by team_trace context manager.
Monitor handler only creates child spans (task/member/message) under the team span.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import context as otel_context
from opentelemetry.trace import (
    Span,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
    set_span_in_context,
)

from openjiuwen.agent_teams.observability.config import ObservabilityConfig
from openjiuwen.agent_teams.observability.semconv import (
    AT_EVENT_TYPE,
    AT_MEMBER_ID,
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
    AT_PLAN_APPROVED,
    AT_PLAN_SUBMITTED_BY,
    AT_TASK_ASSIGNEE,
    AT_TASK_ID,
    AT_TASK_STATUS,
    AT_TEAM_DISPLAY_NAME,
    AT_TEAM_ID,
    AT_TEAM_LEADER,
    AT_TEAM_NAME,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_SESSION_ID,
)
from openjiuwen.agent_teams.observability.span_context import (
    get_team_span,
)
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.core.common.logging import team_logger

_TRACER_NAME = "openjiuwen.agent_teams.observability.monitor"

_TASK_OPEN_TYPES = frozenset({TeamEvent.TASK_CREATED})
_TASK_CLOSE_TYPES = frozenset(
    {
        TeamEvent.TASK_COMPLETED,
        TeamEvent.TASK_CANCELLED,
    },
)
_TASK_EVENT_TYPES = frozenset(
    {
        TeamEvent.TASK_CLAIMED,
        TeamEvent.TASK_UPDATED,
        TeamEvent.TASK_UNBLOCKED,
        # Plan-mode flow: submit_plan publishes TASK_PLAN_REQUEST (status=claimed),
        # approve_plan publishes TASK_PLAN_RESPONSE (status=plan_approved/claimed).
        # plan_mode never publishes TASK_CLAIMED — see task_manager.submit_plan.
        TeamEvent.TASK_PLAN_REQUEST,
        TeamEvent.TASK_PLAN_RESPONSE,
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

# All known TeamEvent types (auto-synced via reflection)
_ALL_TEAM_EVENT_TYPES: frozenset[str] = frozenset(
    getattr(TeamEvent, name)
    for name in dir(TeamEvent)
    if not name.startswith("_") and isinstance(getattr(TeamEvent, name), str)
)


class OtelTeamMonitorHandler:
    """Single async callable consumed by ``TeamAgent.add_event_listener``.

    Team span is created by team_trace context manager in Runner.run.
    This handler only creates child spans (task/member/message) under it.
    """

    def __init__(
            self,
            config: ObservabilityConfig,
            *,
            tracer: Tracer | None = None,
    ) -> None:
        self._config = config
        self._injected_tracer = tracer
        self._task_spans: dict[str, Span] = {}

    def _tracer(self) -> Tracer:
        if self._injected_tracer is not None:
            return self._injected_tracer
        from openjiuwen.agent_teams.observability.setup import get_tracer
        return get_tracer(_TRACER_NAME)

    @staticmethod
    def _get_ctx_session_id() -> str:
        """Get session_id from ContextVar, or empty string on failure."""
        try:
            from openjiuwen.agent_teams.context import get_session_id as get_ctx_session_id
            return get_ctx_session_id() or ""
        except Exception as exc:
            team_logger.warning("monitor_handler: failed to get session_id: {}", exc)
            return ""

    def close_all_spans(self) -> None:
        """Close every open task span, then force-flush."""
        team_logger.info("otel monitor: close_all_spans - closing {} task spans", len(self._task_spans))
        for task_id, span in list(self._task_spans.items()):
            if span.is_recording():
                team_logger.info("otel monitor: closing task span {}, is_recording={}, span_id={}",
                                 task_id, span.is_recording(), span.context.span_id)
                if not span.attributes.get(LANGFUSE_OBSERVATION_OUTPUT):
                    status_val = span.attributes.get(AT_TASK_STATUS, "unknown")
                    span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT,
                                       f"task_{status_val}")
                span.set_status(Status(StatusCode.OK))
                span.end()
                team_logger.info("otel monitor: task span ended for {}", task_id)
        self._task_spans.clear()
        self._force_flush_provider()

    def close_team_spans(self, team_name: str) -> None:
        """Close task spans for a specific team."""
        for task_id, span in list(self._task_spans.items()):
            if span.is_recording():
                span_team = span.attributes.get(AT_TEAM_NAME, "")
                if span_team == team_name:
                    span.set_status(Status(StatusCode.OK))
                    span.end()
                    self._task_spans.pop(task_id, None)
        self._force_flush_provider()

    @staticmethod
    def _force_flush_provider() -> None:
        from openjiuwen.agent_teams.observability.setup import force_flush_provider
        force_flush_provider()

    @staticmethod
    def _event_span_io(etype: str, payload: dict[str, Any]) -> tuple[str | None, str | None]:
        """Return (input_val, output_val) for a monitor event span.

        Splits the payload semantically when the event has a clear
        "request → response" or "before → after" boundary:
          - input  = the event's context / old state / trigger
          - output = the event's result / new state / outcome

        For events without such a boundary (pure notifications), the
        full payload goes to input and output is left empty — no
        duplication.
        """
        import json as _json
        p = _json.dumps(payload, ensure_ascii=False, default=str)

        # --- Task events — split by event type ---
        if etype == TeamEvent.TASK_CREATED:
            return p, None  # notification: full payload → input only
        if etype == TeamEvent.TASK_CLAIMED:
            return _json.dumps({"task_id": payload.get("task_id")},
                               ensure_ascii=False), f"claimed by {payload.get('member_name', '?')}"
        if etype == TeamEvent.TASK_COMPLETED:
            return _json.dumps({"task_id": payload.get("task_id")},
                               ensure_ascii=False), f"completed by {payload.get('member_name', '?')}"
        if etype == TeamEvent.TASK_CANCELLED:
            return _json.dumps({"task_id": payload.get("task_id")}, ensure_ascii=False), "cancelled"
        if etype == TeamEvent.TASK_UNBLOCKED:
            return _json.dumps({"task_id": payload.get("task_id")}, ensure_ascii=False), "unblocked"
        if etype == TeamEvent.TASK_UPDATED:
            return _json.dumps({"task_id": payload.get("task_id")}, ensure_ascii=False), p  # payload carries the update
        if etype == TeamEvent.TASK_PLAN_REQUEST:
            return p, _json.dumps({"plan_id": payload.get("plan_id"), "status": payload.get("status", "planning")},
                                  ensure_ascii=False)
        if etype == TeamEvent.TASK_PLAN_RESPONSE:
            return _json.dumps({"plan_id": payload.get("plan_id")}, ensure_ascii=False), _json.dumps(
                {"approved": payload.get("approved"), "feedback": payload.get("feedback", "")}, ensure_ascii=False)

        # --- Member events — before → after split ---
        if etype == TeamEvent.MEMBER_SPAWNED:
            return None, _json.dumps({"member": payload.get("member_name")}, ensure_ascii=False)
        if etype == TeamEvent.MEMBER_SHUTDOWN:
            return _json.dumps({"member": payload.get("member_name")}, ensure_ascii=False), _json.dumps(
                {"shutdown": True, "force": payload.get("force", False)}, ensure_ascii=False)
        if etype == TeamEvent.MEMBER_RESTARTED:
            return _json.dumps({"member": payload.get("member_name"), "reason": payload.get("reason", "")},
                               ensure_ascii=False), _json.dumps(
                {"restarted": True, "count": payload.get("restart_count", 1)}, ensure_ascii=False)
        if etype == TeamEvent.MEMBER_CANCELED:
            return _json.dumps({"member": payload.get("member_name")}, ensure_ascii=False), None
        if etype in (TeamEvent.MEMBER_STATUS_CHANGED, TeamEvent.MEMBER_EXECUTION_CHANGED):
            # Natural before/after: old_status → new_status
            return _json.dumps({"status": payload.get("old_status", "")}, ensure_ascii=False), _json.dumps(
                {"status": payload.get("new_status", "")}, ensure_ascii=False)

        # --- Message events --- routing → delivery split ---
        if etype == TeamEvent.MESSAGE:
            return _json.dumps({"from": payload.get("from_member_name"), "to": payload.get("to_member_name")},
                               ensure_ascii=False), None
        if etype == TeamEvent.BROADCAST:
            return _json.dumps({"from": payload.get("from_member_name")}, ensure_ascii=False), _json.dumps(
                {"broadcast": True}, ensure_ascii=False)

        # --- Team-level lifecycle — notification, input only ---
        if etype in (TeamEvent.CREATED, TeamEvent.CLEANED, TeamEvent.STANDBY, TeamEvent.TEAM_COMPLETED):
            return p, None

        # --- Plan approval (currently unused but routed) ---
        if etype == TeamEvent.PLAN_APPROVAL:
            return _json.dumps({"plan": payload.get("member_name")}, ensure_ascii=False), _json.dumps(
                {"approved": payload.get("approved")}, ensure_ascii=False)

        # --- Drained / anomaly — notification ---
        if etype in (TeamEvent.TASK_LIST_DRAINED, TeamEvent.ANOMALY_DETECTED):
            return p, None

        # --- Generic: workflow/worktree/workspace/all others — full payload, input only ---
        return p, None

    async def __call__(self, event: EventMessage) -> None:
        try:
            etype = event.event_type
            payload: dict[str, Any] = event.payload or {}
            team_name = str(payload.get("team_name") or "")

            if etype == TeamEvent.CREATED:
                self._record_team_created(team_name, payload)
            elif etype == TeamEvent.CLEANED:
                self._record_team_cleaned(team_name)
            elif etype == TeamEvent.TEAM_COMPLETED:
                self._record_team_completed(team_name, payload)
            elif etype == TeamEvent.STANDBY:
                self._record_team_event(team_name, etype, attrs={AT_EVENT_TYPE: etype})
            elif etype in _TASK_OPEN_TYPES:
                self._open_task_span(team_name, payload)
            elif etype in _TASK_CLOSE_TYPES:
                self._close_task_span(team_name, payload, etype)
            elif etype in _TASK_EVENT_TYPES:
                self._record_task_status_span(team_name, payload, etype)
            elif etype == TeamEvent.PLAN_APPROVAL:
                self._record_plan_approval(team_name, payload)
            elif etype in _MEMBER_TYPES:
                self._record_member_event(team_name, payload, etype)
            elif etype in _MESSAGE_TYPES:
                self._record_message_event(team_name, payload, etype)
            elif etype in _ALL_TEAM_EVENT_TYPES:
                # Fallback: auto-record any TeamEvent not explicitly handled above
                self._record_generic_event(team_name, etype, payload)
        except Exception as exc:
            team_logger.warning(
                "otel monitor handler failed for {}: {}",
                event.event_type,
                exc,
            )

    # ------------------------------------------------------------------
    # Team span lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _record_team_created(team_name: str, payload: dict[str, Any]) -> None:
        team_span = get_team_span()
        if team_span is None:
            team_logger.warning(
                "monitor_handler:_record_team_created: team_span is None! "
                "TeamCreatedEvent arrived BEFORE _maybe_attach_observability. "
                "team={}", team_name,
            )
            return

        team_span.set_attribute(AT_TEAM_DISPLAY_NAME, str(payload.get("display_name", team_name)))
        team_span.set_attribute(AT_EVENT_TYPE, TeamEvent.CREATED)

        session_id = payload.get("session_id") or OtelTeamMonitorHandler._get_ctx_session_id()
        if session_id:
            team_span.set_attribute(LANGFUSE_SESSION_ID, str(session_id))

        leader = payload.get("leader_member_name")
        if leader:
            team_span.set_attribute(AT_TEAM_LEADER, str(leader))

        team_input = payload.get("input") or payload.get("query") or ""
        if team_input:
            team_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, str(team_input))

    def _record_team_cleaned(self, team_name: str) -> None:
        """Record team.cleaned event span and close dangling agent spans.

        Does NOT close the team span — that is owned by Runner's
        _maybe_finalize_trace (finally block).  Closing it here would
        prematurely end the shared team span while the leader may still
        be running, causing all subsequent LLM/tool spans to become
        orphans.
        """
        self._record_team_event(team_name, "team.cleaned", attrs={AT_EVENT_TYPE: TeamEvent.CLEANED})

        # Close any dangling agent span for this member.
        from openjiuwen.agent_teams.observability.span_context import close_team_agent_spans
        close_team_agent_spans(team_name)

    def _record_team_completed(self, team_name: str, payload: dict[str, Any]) -> None:
        member_count = payload.get("member_count")
        task_count = payload.get("task_count")
        attrs: dict[str, Any] = {AT_EVENT_TYPE: TeamEvent.TEAM_COMPLETED}
        if member_count is not None:
            attrs["agentteam.team.member_count"] = int(member_count)
        if task_count is not None:
            attrs["agentteam.team.task_count"] = int(task_count)
        in_val, out_val = self._event_span_io(TeamEvent.TEAM_COMPLETED, payload)
        self._record_team_event(team_name, "team.completed", attrs=attrs, input_val=in_val, output_val=out_val)

    def _record_team_event(
            self,
            team_name: str,
            name: str,
            *,
            attrs: dict[str, Any],
            input_val: str | None = None,
            output_val: str | None = None,
    ) -> None:
        team_span = get_team_span()
        if team_span is None:
            return
        parent_ctx = set_span_in_context(team_span, otel_context.get_current())
        span = self._tracer().start_span(
            name=name,
            context=parent_ctx,
            kind=SpanKind.INTERNAL,
        )
        for k, v in attrs.items():
            span.set_attribute(k, v)
        if team_name:
            span.set_attribute(AT_TEAM_ID, team_name)
            span.set_attribute(AT_TEAM_NAME, team_name)
        if input_val:
            span.set_attribute(LANGFUSE_OBSERVATION_INPUT, input_val)
        if output_val:
            span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, output_val)
        span.set_status(Status(StatusCode.OK))
        span.end()

    # ------------------------------------------------------------------
    # Task span lifecycle
    # ------------------------------------------------------------------

    def _open_task_span(self, team_name: str, payload: dict[str, Any]) -> None:
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            team_logger.warning("otel monitor: _open_task_span: no task_id in payload")
            return
        if task_id in self._task_spans:
            team_logger.debug("otel monitor: _open_task_span: task {} already exists", task_id)
            return

        # Team span is ONLY created in callback_handler.on_agent_invoke_input.
        # Monitor must never create a team span — only use the existing one.
        team_span = get_team_span()
        if team_span is None:
            team_logger.warning(
                "monitor: no team span for team={}, skip task={}",
                team_name, task_id
            )
            return

        team_logger.debug(
            "monitor: creating task span, task={}, team={}",
            task_id, team_name
        )
        parent_ctx = set_span_in_context(team_span, otel_context.get_current())
        span = self._tracer().start_span(
            name=f"task.{task_id}",
            context=parent_ctx,
            kind=SpanKind.INTERNAL,
        )
        team_logger.debug(
            "monitor: task span created, task={}, span_id={}",
            task_id, span.context.span_id
        )
        span.set_attribute(AT_TASK_ID, task_id)
        if team_name:
            span.set_attribute(AT_TEAM_ID, team_name)
            span.set_attribute(AT_TEAM_NAME, team_name)
        span.set_attribute("agentteam.task.tag", f"task:{task_id}")
        status = payload.get("status")
        if status:
            span.set_attribute(AT_TASK_STATUS, str(status))
        assignee = payload.get("assignee") or payload.get("member_name")
        if assignee:
            span.set_attribute(AT_TASK_ASSIGNEE, str(assignee))
        task_content = payload.get("content") or payload.get("title") or ""
        if not task_content:
            task_content = payload.get("description") or ""
        if task_content:
            span.set_attribute(LANGFUSE_OBSERVATION_INPUT, str(task_content))
        else:
            import json as _json
            span.set_attribute(LANGFUSE_OBSERVATION_INPUT,
                               _json.dumps(payload, ensure_ascii=False, default=str))
        sid = self._get_ctx_session_id()
        if sid:
            span.set_attribute(LANGFUSE_SESSION_ID, sid)
        self._task_spans[task_id] = span

        created_attrs: dict[str, Any] = {
            AT_EVENT_TYPE: TeamEvent.TASK_CREATED,
            AT_TASK_ID: task_id,
            AT_TASK_STATUS: "pending",
        }
        created_ctx = set_span_in_context(span, otel_context.get_current())
        created_span = self._tracer().start_span(
            name=f"task.{task_id}.created",
            context=created_ctx,
            kind=SpanKind.INTERNAL,
        )
        created_span.set_attributes(created_attrs)
        created_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, f"task:{task_id}")
        created_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, "created")
        created_span.set_status(Status(StatusCode.OK))
        created_span.end()

    def _close_task_span(self, team_name: str, payload: dict[str, Any], etype: str) -> None:
        task_id = str(payload.get("task_id") or "")
        span = self._task_spans.pop(task_id, None)
        if span is None:
            # Task span may have been cleaned up by a prior trace finalization
            # (e.g. pause/resume cycle).  Create an on-the-fly span so the
            # completion/cancellation is still recorded in the current trace.
            team_logger.debug(
                "monitor: _close_task_span: no existing span for task={}, "
                "creating on-the-fly span for event={}",
                task_id, etype,
            )
            span = self._create_on_the_fly_task_span(team_name, payload)
            if span is None:
                team_logger.warning("monitor: _close_task_span: cannot create span for task={}", task_id)
                return

        # Check if span is still recording before operating
        if not span.is_recording():
            team_logger.warning("monitor: _close_task_span: task {} span already ended", task_id)
            return

        status_label = etype.replace("task_", "")
        member = payload.get("member_name") or ""

        close_attrs: dict[str, Any] = {
            AT_EVENT_TYPE: etype,
            AT_TASK_ID: task_id,
            AT_TASK_STATUS: status_label,
        }
        if member:
            close_attrs[AT_TASK_ASSIGNEE] = str(member)
        cancel_reason = payload.get("reason") or payload.get("cancel_reason") or "cancelled"
        if etype == TeamEvent.TASK_CANCELLED:
            close_attrs["agentteam.task.cancel_reason"] = str(cancel_reason)

        close_ctx = set_span_in_context(span, otel_context.get_current())
        close_span = self._tracer().start_span(
            name=f"task.{task_id}.{status_label}",
            context=close_ctx,
            kind=SpanKind.INTERNAL,
        )
        close_span.set_attributes(close_attrs)
        close_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, f"task:{task_id}")
        close_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, status_label)
        close_span.set_status(Status(StatusCode.OK))
        close_span.end()

        span.set_attribute(AT_TASK_STATUS, status_label)
        task_result = payload.get("result") or payload.get("output") or etype
        span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, str(task_result))
        if etype == TeamEvent.TASK_CANCELLED:
            span.set_status(Status(StatusCode.ERROR, str(cancel_reason)))
        else:
            span.set_status(Status(StatusCode.OK))
        span.end()

    def _create_on_the_fly_task_span(
            self, team_name: str, payload: dict[str, Any]
    ) -> Span | None:
        """Create a minimal task span when no pre-existing span is found.

        Used in pause/resume scenarios where a task created in a prior trace
        completes in the current trace — the original span was finalized with
        the prior trace, so we create a short-lived span here to hold the
        completion/cancellation event.
        """
        team_span = get_team_span()
        if team_span is None:
            return None

        task_id = str(payload.get("task_id") or "")
        parent_ctx = set_span_in_context(team_span, otel_context.get_current())
        span = self._tracer().start_span(
            name=f"task.{task_id}",
            context=parent_ctx,
            kind=SpanKind.INTERNAL,
        )
        span.set_attribute(AT_TASK_ID, task_id)
        if team_name:
            span.set_attribute(AT_TEAM_ID, team_name)
            span.set_attribute(AT_TEAM_NAME, team_name)
        span.set_attribute("agentteam.task.tag", f"task:{task_id}")
        # Mark as recovered so the trace viewer can distinguish tasks that
        # were created in a prior trace from those created in the current one.
        span.set_attribute("agentteam.task.recovered", True)
        sid = self._get_ctx_session_id()
        if sid:
            span.set_attribute(LANGFUSE_SESSION_ID, sid)
        return span

    def _record_task_status_span(self, team_name: str, payload: dict[str, Any], etype: str) -> None:
        task_id = str(payload.get("task_id") or "")
        member = payload.get("member_name") or ""
        task_span = self._task_spans.get(task_id)

        if task_span is None:
            # Task span may have been cleaned up by a prior trace finalization
            # (e.g. pause/resume cycle).  Create an on-the-fly span and store
            # it so subsequent events (e.g. claimed→completed) in the same
            # trace can find it.
            task_span = self._create_on_the_fly_task_span(team_name, payload)
            if task_span is not None:
                self._task_spans[task_id] = task_span
                team_logger.debug(
                    "monitor: _record_task_status_span: created on-the-fly span "
                    "for task={} event={}", task_id, etype,
                )

        # Effective task status carried by this event. Plan-mode events carry
        # it in the payload `status` field (claimed / plan_approved); the
        # legacy events derive it from the event type. This is the value
        # written to both the task span and the child status span attribute.
        effective_status = self._effective_task_status(etype, payload)

        # Only operate on task_span if it exists and is still recording
        if task_span is not None and task_span.is_recording():
            if member:
                task_span.set_attribute(AT_TASK_ASSIGNEE, str(member))
            if effective_status:
                task_span.set_attribute(AT_TASK_STATUS, effective_status)
            if etype == TeamEvent.TASK_PLAN_REQUEST:
                plan_id = payload.get("plan_id")
                if plan_id:
                    task_span.set_attribute("agentteam.task.plan_id", str(plan_id))
                member_plan_md = payload.get("member_plan_md")
                if member_plan_md:
                    task_span.set_attribute("agentteam.task.member_plan_md", str(member_plan_md))
            elif etype == TeamEvent.TASK_PLAN_RESPONSE:
                approved = bool(payload.get("approved", False))
                task_span.set_attribute(AT_PLAN_APPROVED, approved)

        # Child status span name uses the event suffix (task.{id}.plan_request
        # / plan_response / claimed / ...). The AT_TASK_STATUS attribute on it
        # carries the effective status, NOT the event-name suffix.
        status_label = etype.replace("task_", "")
        attrs: dict[str, Any] = {
            AT_EVENT_TYPE: etype,
            AT_TASK_STATUS: effective_status or status_label,
            AT_TASK_ID: task_id,
        }
        if member:
            attrs[AT_TASK_ASSIGNEE] = str(member)
        if etype == TeamEvent.TASK_PLAN_REQUEST:
            plan_id = payload.get("plan_id")
            if plan_id:
                attrs["agentteam.task.plan_id"] = str(plan_id)
        elif etype == TeamEvent.TASK_PLAN_RESPONSE:
            attrs[AT_PLAN_APPROVED] = bool(payload.get("approved", False))
        span_name = f"task.{task_id}.{status_label}"

        in_val, out_val = self._event_span_io(etype, payload)

        if task_span is not None and task_span.is_recording():
            task_ctx = set_span_in_context(task_span, otel_context.get_current())
            status_span = self._tracer().start_span(
                name=span_name,
                context=task_ctx,
                kind=SpanKind.INTERNAL,
            )
            status_span.set_attributes(attrs)
            if in_val is not None:
                status_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, in_val)
            if out_val is not None:
                status_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, out_val)
            status_span.set_status(Status(StatusCode.OK))
            status_span.end()
        else:
            self._record_team_event(team_name, span_name, attrs=attrs,
                                    input_val=in_val, output_val=out_val)

    @staticmethod
    def _effective_task_status(etype: str, payload: dict[str, Any]) -> str:
        """Return the task status this event advances the task to.

        Plan-mode events carry the effective status in ``payload['status']``
        (planning / in_progress). Legacy events derive it from the type.
        Falls back to "" when unknown.
        """
        if etype in (TeamEvent.TASK_PLAN_REQUEST, TeamEvent.TASK_PLAN_RESPONSE):
            status = payload.get("status")
            if status:
                return str(status)
            # plan_response without an explicit status: approved -> in_progress
            if etype == TeamEvent.TASK_PLAN_RESPONSE:
                return "in_progress" if bool(payload.get("approved", False)) else "planning"
            return "planning"
        if etype == TeamEvent.TASK_CLAIMED:
            return "in_progress"
        if etype == TeamEvent.TASK_UNBLOCKED:
            return "unblocked"
        if etype == TeamEvent.TASK_UPDATED:
            status = payload.get("status")
            return str(status) if status else ""
        return ""

    def _record_plan_approval(self, team_name: str, payload: dict[str, Any]) -> None:
        approved = payload.get("approved", False)
        member = payload.get("member_name") or ""
        span_name = "plan.approved" if approved else "plan.rejected"
        attrs: dict[str, Any] = {
            AT_EVENT_TYPE: TeamEvent.PLAN_APPROVAL,
            AT_PLAN_APPROVED: bool(approved),
        }
        if member:
            attrs[AT_MEMBER_ID] = str(member)
            attrs[AT_MEMBER_NAME] = str(member)
            attrs[AT_PLAN_SUBMITTED_BY] = str(member)
        in_val, out_val = self._event_span_io(TeamEvent.PLAN_APPROVAL, payload)
        self._record_team_event(team_name, span_name, attrs=attrs,
                                input_val=in_val, output_val=out_val)

    # ------------------------------------------------------------------
    # Member / message events as short-lived child spans
    # ------------------------------------------------------------------

    def _record_member_event(
            self,
            team_name: str,
            payload: dict[str, Any],
            etype: str,
    ) -> None:
        member_name = str(payload.get("member_name") or "")

        attrs: dict[str, Any] = {
            AT_EVENT_TYPE: etype,
            AT_MEMBER_ID: member_name,
            AT_MEMBER_NAME: member_name,
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
        span_name = f"member.{etype.replace('member_', '')}"
        if member_name:
            span_name = f"member.{member_name}.{etype.replace('member_', '')}"
        in_val, out_val = self._event_span_io(etype, payload)
        self._record_team_event(team_name, span_name, attrs=attrs,
                                input_val=in_val, output_val=out_val)

    def _record_message_event(
            self,
            team_name: str,
            payload: dict[str, Any],
            etype: str,
    ) -> None:
        from_name = str(payload.get("from_member_name") or "")
        to_name = str(payload.get("to_member_name") or "")
        is_broadcast = etype == TeamEvent.BROADCAST

        attrs: dict[str, Any] = {
            AT_EVENT_TYPE: etype,
            AT_MESSAGE_ID: str(payload.get("message_id") or ""),
            AT_MESSAGE_FROM: from_name,
            AT_MESSAGE_TO: to_name,
            AT_MESSAGE_BROADCAST: is_broadcast,
        }
        if is_broadcast:
            span_name = f"msg.broadcast.{from_name}"
        else:
            span_name = f"msg.{from_name}->{to_name}"
        in_val, out_val = self._event_span_io(etype, payload)
        self._record_team_event(team_name, span_name, attrs=attrs,
                                input_val=in_val, output_val=out_val)

    def _record_generic_event(
            self,
            team_name: str,
            etype: str,
            payload: dict[str, Any],
    ) -> None:
        """Fallback handler for any TeamEvent not explicitly handled above.

        This ensures new TeamEvent types are automatically recorded without
        requiring manual updates to monitor_handler. Creates a short-lived
        child span under the team span with basic attributes.
        """
        # Extract event name from etype (e.g., "workflow_progress" -> "workflow.progress")
        event_name = etype.replace("_", ".")
        span_name = f"event.{event_name}"

        attrs: dict[str, Any] = {
            AT_EVENT_TYPE: etype,
        }

        # Add common payload fields as attributes
        member_name = payload.get("member_name")
        if member_name:
            attrs[AT_MEMBER_NAME] = str(member_name)

        # Special handling for common event types — structured attributes only.
        # Input/output are handled via _event_span_io below.

        if etype == TeamEvent.WORKFLOW_PROGRESS:
            workflow_name = payload.get("workflow_name")
            if workflow_name:
                attrs["agentteam.workflow.name"] = str(workflow_name)
            phase = payload.get("phase")
            if phase:
                attrs["agentteam.workflow.phase"] = str(phase)
            label = payload.get("label")
            if label:
                attrs["agentteam.workflow.label"] = str(label)
            outcome = payload.get("outcome")
            if outcome:
                attrs["agentteam.workflow.outcome"] = str(outcome)

        elif etype == TeamEvent.WORKTREE_CREATED:
            wt_name = payload.get("worktree_name") or payload.get("name") or ""
            wt_path = payload.get("worktree_path") or payload.get("path") or ""
            if wt_name:
                attrs["agentteam.worktree.name"] = str(wt_name)
            if wt_path:
                attrs["agentteam.worktree.path"] = str(wt_path)
            existed = payload.get("existed")
            if existed is not None:
                attrs["agentteam.worktree.existed"] = bool(existed)

        elif etype == TeamEvent.WORKTREE_REMOVED:
            wt_name = payload.get("worktree_name") or payload.get("name") or ""
            wt_path = payload.get("worktree_path") or payload.get("path") or ""
            if wt_name:
                attrs["agentteam.worktree.name"] = str(wt_name)
            if wt_path:
                attrs["agentteam.worktree.path"] = str(wt_path)

        elif etype == TeamEvent.WORKSPACE_ARTIFACT_UPDATED:
            artifact_path = payload.get("artifact_path") or payload.get("path") or ""
            commit_sha = payload.get("commit_sha") or ""
            if artifact_path:
                attrs["agentteam.workspace.artifact_path"] = str(artifact_path)
            if commit_sha:
                attrs["agentteam.workspace.commit_sha"] = str(commit_sha)

        elif etype == TeamEvent.WORKSPACE_CONFLICT:
            file_path = payload.get("file_path") or payload.get("path") or ""
            conflicting = payload.get("conflicting_commit") or ""
            if file_path:
                attrs["agentteam.workspace.file_path"] = str(file_path)
            if conflicting:
                attrs["agentteam.workspace.conflicting_commit"] = str(conflicting)

        elif etype == TeamEvent.WORKSPACE_LOCK_REQUEST:
            action = payload.get("action") or ""
            file_path = payload.get("file_path") or payload.get("path") or ""
            holder = payload.get("holder_name") or payload.get("holder") or ""
            timeout = payload.get("timeout_seconds")
            if action:
                attrs["agentteam.workspace.action"] = str(action)
            if file_path:
                attrs["agentteam.workspace.file_path"] = str(file_path)
            if holder:
                attrs["agentteam.workspace.holder_name"] = str(holder)
            if timeout is not None:
                attrs["agentteam.workspace.timeout_seconds"] = timeout

        elif etype == TeamEvent.WORKSPACE_LOCK_RESPONSE:
            file_path = payload.get("file_path") or payload.get("path") or ""
            granted = payload.get("granted")
            holder = payload.get("holder") or ""
            if file_path:
                attrs["agentteam.workspace.file_path"] = str(file_path)
            if granted is not None:
                attrs["agentteam.workspace.granted"] = bool(granted)
            if holder:
                attrs["agentteam.workspace.holder"] = str(holder)

        # Every event span carries the raw payload as both input and output.
        # No per-event-type narration, no synthesized status labels, no
        # hardcoded member names.
        in_val, out_val = self._event_span_io(etype, payload)

        self._record_team_event(team_name, span_name, attrs=attrs,
                                input_val=in_val, output_val=out_val)
