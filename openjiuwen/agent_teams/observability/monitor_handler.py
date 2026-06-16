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

# Events that need special handling (not generic child span)
# - CREATED/CLEANED/TEAM_COMPLETED: team span lifecycle management
# - TASK_*: task span lifecycle management
# - MEMBER_*: member-specific attributes
# - MESSAGE/BROADCAST: message routing attributes
_SPECIAL_HANDLED_TYPES = frozenset(
    {
        TeamEvent.CREATED,
        TeamEvent.CLEANED,
        TeamEvent.TEAM_COMPLETED,
        TeamEvent.STANDBY,
        TeamEvent.PLAN_APPROVAL,
    }
)

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
            return

        team_span.set_attribute(AT_TEAM_DISPLAY_NAME, str(payload.get("display_name", team_name)))
        team_span.set_attribute(AT_EVENT_TYPE, TeamEvent.CREATED)

        session_id = payload.get("session_id")
        if not session_id:
            try:
                from openjiuwen.agent_teams.context import get_session_id as get_ctx_session_id
                session_id = get_ctx_session_id()
            except Exception as exc:
                team_logger.warning("monitor_handler: failed to get session_id: {}", exc)
        if session_id:
            team_span.set_attribute(LANGFUSE_SESSION_ID, str(session_id))

        leader = payload.get("leader_member_name")
        if leader:
            team_span.set_attribute(AT_TEAM_LEADER, str(leader))

        team_input = payload.get("input") or payload.get("query") or ""
        if team_input:
            team_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, str(team_input))

    def _record_team_cleaned(self, team_name: str) -> None:
        self._record_team_event(team_name, "team.cleaned", attrs={AT_EVENT_TYPE: TeamEvent.CLEANED})

        from openjiuwen.agent_teams.observability.span_context import close_team_agent_spans
        close_team_agent_spans(team_name)

        team_span = get_team_span()
        if team_span is not None and team_span.is_recording():
            if not team_span.attributes.get(LANGFUSE_OBSERVATION_OUTPUT):
                team_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, "team_cleaned")
            team_span.set_status(Status(StatusCode.OK))
            team_span.end()
            team_logger.info("otel: team span closed for team_name={}", team_name)
            from openjiuwen.agent_teams.observability.span_context import remove_team_span
            remove_team_span()

        self._force_flush_provider()

    def _record_team_completed(self, team_name: str, payload: dict[str, Any]) -> None:
        member_count = payload.get("member_count")
        task_count = payload.get("task_count")
        attrs: dict[str, Any] = {AT_EVENT_TYPE: TeamEvent.TEAM_COMPLETED}
        if member_count is not None:
            attrs["agentteam.team.member_count"] = int(member_count)
        if task_count is not None:
            attrs["agentteam.team.task_count"] = int(task_count)
        out_val = f"completed members={member_count} tasks={task_count}"
        self._record_team_event(team_name, "team.completed", attrs=attrs, output_val=out_val)

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
        try:
            from openjiuwen.agent_teams.context import get_session_id as get_ctx_session_id
            sid = get_ctx_session_id()
            if sid:
                span.set_attribute(LANGFUSE_SESSION_ID, sid)
        except Exception as exc:
            team_logger.warning("monitor_handler: failed to get session_id: {}", exc)
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
            team_logger.warning("monitor: _close_task_span: no span for task={}", task_id)
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
        if etype == TeamEvent.TASK_CANCELLED:
            cancel_reason = payload.get("reason") or payload.get("cancel_reason") or "cancelled"
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

    def _record_task_status_span(self, team_name: str, payload: dict[str, Any], etype: str) -> None:
        task_id = str(payload.get("task_id") or "")
        member = payload.get("member_name") or ""
        task_span = self._task_spans.get(task_id)

        # Only operate on task_span if it exists and is still recording
        if task_span is not None and task_span.is_recording():
            if member:
                task_span.set_attribute(AT_TASK_ASSIGNEE, str(member))
            if etype == TeamEvent.TASK_CLAIMED:
                task_span.set_attribute(AT_TASK_STATUS, "claimed")
            elif etype == TeamEvent.TASK_UNBLOCKED:
                task_span.set_attribute(AT_TASK_STATUS, "unblocked")
            elif etype == TeamEvent.TASK_UPDATED:
                status = payload.get("status")
                if status:
                    task_span.set_attribute(AT_TASK_STATUS, str(status))

        status_label = etype.replace("task_", "")
        span_name = f"task.{task_id}.{status_label}" if task_id else f"task.{status_label}"
        attrs: dict[str, Any] = {
            AT_EVENT_TYPE: etype,
            AT_TASK_STATUS: status_label,
        }
        if task_id:
            attrs[AT_TASK_ID] = task_id
        if member:
            attrs[AT_TASK_ASSIGNEE] = str(member)
        in_val = f"task:{task_id}" if task_id else status_label
        out_val = f"{status_label}" + (f" by {member}" if member else "")

        if task_span is not None and task_span.is_recording():
            task_ctx = set_span_in_context(task_span, otel_context.get_current())
            status_span = self._tracer().start_span(
                name=span_name,
                context=task_ctx,
                kind=SpanKind.INTERNAL,
            )
            status_span.set_attributes(attrs)
            status_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, in_val)
            status_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, out_val)
            status_span.set_status(Status(StatusCode.OK))
            status_span.end()
        else:
            self._record_team_event(team_name, span_name, attrs=attrs,
                                    input_val=in_val, output_val=out_val)

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
        in_val = f"plan by {member}" if member else "plan"
        out_val = "approved" if approved else "rejected"
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
        old_s = payload.get("old_status", "")
        new_s = payload.get("new_status", "")
        if etype == TeamEvent.MEMBER_SPAWNED:
            in_val = f"spawn:{member_name}"
            out_val = f"{member_name} spawned"
        elif etype == TeamEvent.MEMBER_SHUTDOWN:
            force = payload.get("force", False)
            in_val = f"{member_name}:running"
            out_val = f"{member_name}:shutdown(force={force})"
        elif etype == TeamEvent.MEMBER_CANCELED:
            in_val = f"{member_name}:running"
            out_val = f"{member_name}:canceled"
        elif etype == TeamEvent.MEMBER_RESTARTED:
            reason = payload.get("reason", "")
            in_val = f"{member_name}:stopped"
            out_val = f"{member_name}:restarted(reason={reason})"
        else:
            in_val = f"{member_name}:{old_s}" if old_s else None
            out_val = f"{member_name}:{new_s}" if new_s else etype.replace("member_", "")
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
        in_val = f"from:{from_name}" + (f" to:{to_name}" if to_name else "")
        out_val = f"broadcast:{from_name}" if is_broadcast else f"delivered:{from_name}->{to_name}"
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

        # Special handling for common event types
        input_val: str | None = None
        output_val: str = etype

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
            # Custom display format
            input_val = f"workflow:{workflow_name or 'unknown'}"
            output_val = f"phase:{phase or 'unknown'}" if phase else etype

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
            input_val = f"worktree:{wt_name}"
            output_val = "created" if not existed else "recovered"

        elif etype == TeamEvent.WORKTREE_REMOVED:
            wt_name = payload.get("worktree_name") or payload.get("name") or ""
            wt_path = payload.get("worktree_path") or payload.get("path") or ""
            if wt_name:
                attrs["agentteam.worktree.name"] = str(wt_name)
            if wt_path:
                attrs["agentteam.worktree.path"] = str(wt_path)
            input_val = f"worktree:{wt_name}"
            output_val = "removed"

        elif etype == TeamEvent.WORKSPACE_ARTIFACT_UPDATED:
            artifact_path = payload.get("artifact_path") or payload.get("path") or ""
            commit_sha = payload.get("commit_sha") or ""
            if artifact_path:
                attrs["agentteam.workspace.artifact_path"] = str(artifact_path)
            if commit_sha:
                attrs["agentteam.workspace.commit_sha"] = str(commit_sha)
            input_val = f"artifact:{artifact_path}"
            output_val = "updated"

        elif etype == TeamEvent.WORKSPACE_CONFLICT:
            file_path = payload.get("file_path") or payload.get("path") or ""
            conflicting = payload.get("conflicting_commit") or ""
            if file_path:
                attrs["agentteam.workspace.file_path"] = str(file_path)
            if conflicting:
                attrs["agentteam.workspace.conflicting_commit"] = str(conflicting)
            input_val = f"file:{file_path}"
            output_val = "conflict"

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
            input_val = f"lock:{file_path}" if file_path else None
            output_val = f"{action}:{holder}" if holder else action

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
            input_val = f"lock:{file_path}" if file_path else None
            output_val = "granted" if granted else "denied"

        else:
            # For other events, serialize the full payload as input (no truncation)
            import json as _json
            try:
                input_val = _json.dumps(payload, ensure_ascii=False, default=str)
            except Exception:
                input_val = str(payload)

        self._record_team_event(team_name, span_name, attrs=attrs,
                                input_val=input_val, output_val=output_val)
