# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``team`` namespace of the ``ops.build`` extension capability.

``register`` wires the team Ops build handler onto a bus as a BUILTIN
handler of ``ops.build``: the handler first continues the chain, validates
that the result is the shared ``EventHandlerOpsBuilder``, then adds the
``team`` namespace and its read-only query methods. It registers no event
type and no CORE of its own — the platform's ``ops.build`` CORE creates
the builder.

Every method is a noun that reads or computes a read-only view; none
triggers a new event or advances the current chain (continuing the chain
is ``next_handler(event)``'s job). Resource queries take an optional
``team_name``: omitted or ``None`` means the team the Team business entry
bound for the current call — never a guess, so without a bound team the
caller must pass the name explicitly, even when the session holds only
one team.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_teams.extensions.models import TeamPolicyContext
from openjiuwen.agent_teams.extensions.ops import (
    MessagePage,
    TaskDependencyInfo,
    TeamReadView,
    TeamResourceResolver,
    current_policy_context,
    current_team_name,
)
from openjiuwen.agent_teams.monitor.models import MemberInfo, MessageInfo, TaskInfo, TeamInfo
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.types import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    HandlerLevel,
)

# Bounds of the ``messages`` page size.
_MESSAGE_PAGE_LIMIT_MIN = 1
_MESSAGE_PAGE_LIMIT_MAX = 200
_MESSAGE_PAGE_LIMIT_DEFAULT = 50

_TEAM_NAMESPACE = "team"


@dataclass(frozen=True, slots=True)
class _MessageCursor:
    """Server-side pagination state, opaque to developers."""

    session_id: str
    team_name: str
    filters: dict[str, Any]
    offset: int


def _encode_cursor(cursor: _MessageCursor) -> str:
    """Serialize a cursor into its opaque URL-safe form."""
    payload = json.dumps(
        {
            "session_id": cursor.session_id,
            "team_name": cursor.team_name,
            "filters": cursor.filters,
            "offset": cursor.offset,
        },
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(encoded: str) -> _MessageCursor:
    """Parse a cursor, raising on any malformed payload.

    Raises:
        BaseError: ``AGENT_TEAM_CONFIG_INVALID`` when the cursor is not a
            valid cursor for this query surface.
    """
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
        cursor = _MessageCursor(
            session_id=payload["session_id"],
            team_name=payload["team_name"],
            filters=dict(payload["filters"]),
            offset=int(payload["offset"]),
        )
    except Exception as error:
        raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID, reason=f"invalid message cursor: {error}")
        return None  # unreachable; raise_error always raises
    if not isinstance(cursor.session_id, str) or not isinstance(cursor.team_name, str):
        raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID, reason="invalid message cursor payload")
    return cursor


def _require_session_id(ops: EventHandlerOps) -> str:
    """Return the session id of the current call, or raise."""
    session = ops.session_context
    session_id = session.session_id if session is not None else None
    if not session_id:
        raise_error(
            StatusCode.AGENT_TEAM_STATE_INVALID,
            reason="team ops queries require a valid session context",
        )
    return session_id


def _resolve_team_name(team_name: str | None) -> str:
    """Resolve the query target: explicit name, else the bound current team."""
    resolved = team_name if team_name is not None else current_team_name()
    if not resolved:
        raise_error(
            StatusCode.AGENT_TEAM_STATE_INVALID,
            reason="no current team bound for this call; pass team_name explicitly",
        )
    return resolved


async def _resolve_view(
    resolver: TeamResourceResolver,
    ops: EventHandlerOps,
    team_name: str | None,
) -> tuple[str, TeamReadView]:
    """Resolve the session's read view for the target team.

    Raises:
        BaseError: ``AGENT_TEAM_STATE_INVALID`` when the session is
            missing, no team can be determined, or the team is not
            accessible in this session.
    """
    session_id = _require_session_id(ops)
    resolved_team = _resolve_team_name(team_name)
    view = await resolver.resolve(session_id, resolved_team)
    if view is None:
        raise_error(
            StatusCode.AGENT_TEAM_STATE_INVALID,
            reason=f"team '{resolved_team}' is not accessible in this session",
        )
    return resolved_team, view


def _message_filters(
    from_member_name: str | None,
    to_member_name: str | None,
    broadcast: bool | None,
) -> dict[str, Any]:
    """Normalize the message filters for matching and cursor binding."""
    return {
        "from_member_name": from_member_name,
        "to_member_name": to_member_name,
        "broadcast": broadcast,
    }


def _matches_filters(message: MessageInfo, filters: dict[str, Any]) -> bool:
    """Apply the message filters with AND semantics."""
    if filters["from_member_name"] is not None and message.from_member_name != filters["from_member_name"]:
        return False
    if filters["to_member_name"] is not None and message.to_member_name != filters["to_member_name"]:
        return False
    if filters["broadcast"] is not None and message.broadcast != filters["broadcast"]:
        return False
    return True


def _build_team_methods(resolver: TeamResourceResolver) -> dict[str, Any]:
    """Create the ``team`` namespace methods bound to one resolver."""

    async def info(ops: EventHandlerOps, *, team_name: str | None = None) -> TeamInfo | None:
        """Query the accessible team's info."""
        _, view = await _resolve_view(resolver, ops, team_name)
        return await view.team_info()

    async def members(ops: EventHandlerOps, *, team_name: str | None = None) -> list[MemberInfo]:
        """Query the team's member roster as read-only views."""
        _, view = await _resolve_view(resolver, ops, team_name)
        return list(await view.members())

    async def member(
        ops: EventHandlerOps,
        member_name: str,
        *,
        team_name: str | None = None,
    ) -> MemberInfo | None:
        """Query one member of the team; None when not found."""
        _, view = await _resolve_view(resolver, ops, team_name)
        return await view.member(member_name)

    async def tasks(ops: EventHandlerOps, *, team_name: str | None = None) -> list[TaskInfo]:
        """Query the team's task list as read-only views."""
        _, view = await _resolve_view(resolver, ops, team_name)
        return list(await view.tasks())

    async def task(ops: EventHandlerOps, task_id: str, *, team_name: str | None = None) -> TaskInfo:
        """Query one task, validating it belongs to the target team.

        Raises:
            BaseError: ``AGENT_TEAM_STATE_INVALID`` when the task does not
                exist or belongs to another team.
        """
        resolved_team, view = await _resolve_view(resolver, ops, team_name)
        task_info = await view.task(task_id)
        if task_info is None:
            raise_error(
                StatusCode.AGENT_TEAM_STATE_INVALID,
                reason=f"task '{task_id}' not found in team '{resolved_team}'",
            )
        if task_info.team_name != resolved_team:
            raise_error(
                StatusCode.AGENT_TEAM_STATE_INVALID,
                reason=f"task '{task_id}' does not belong to team '{resolved_team}'",
            )
        return task_info

    async def task_dependencies(
        ops: EventHandlerOps,
        task_id: str,
        *,
        team_name: str | None = None,
    ) -> list[TaskDependencyInfo]:
        """Query one task's direct dependencies.

        Both the task and every depended-on task must belong to the target
        team; an empty list means the task has no dependencies.

        Raises:
            BaseError: ``AGENT_TEAM_STATE_INVALID`` when the task or any
                dependency endpoint is missing or belongs to another team.
        """
        resolved_team, view = await _resolve_view(resolver, ops, team_name)
        dependencies = await view.task_dependencies(task_id)
        checked: list[TaskDependencyInfo] = []
        for dependency in dependencies:
            if dependency.task_id != task_id:
                raise_error(
                    StatusCode.AGENT_TEAM_STATE_INVALID,
                    reason=f"dependency row {dependency.task_id} does not belong to task '{task_id}'",
                )
            depended_on = await view.task(dependency.depends_on_task_id)
            if depended_on is None or depended_on.team_name != resolved_team:
                raise_error(
                    StatusCode.AGENT_TEAM_STATE_INVALID,
                    reason=(
                        f"dependency endpoint '{dependency.depends_on_task_id}' of task "
                        f"'{task_id}' is not part of team '{resolved_team}'"
                    ),
                )
            checked.append(dependency)
        return checked

    async def messages(
        ops: EventHandlerOps,
        *,
        from_member_name: str | None = None,
        to_member_name: str | None = None,
        broadcast: bool | None = None,
        limit: int = _MESSAGE_PAGE_LIMIT_DEFAULT,
        cursor: str | None = None,
        team_name: str | None = None,
    ) -> MessagePage:
        """Query the team's messages with filters, ordering and pagination.

        Filters combine with AND and never widen the caller's visibility:
        with direct messages hidden, only broadcast traffic is visible. The
        page is ordered by ``timestamp`` then ``message_id``, both
        descending. Neither this query nor :func:`message` marks anything
        read.

        Raises:
            BaseError: ``AGENT_TEAM_CONFIG_INVALID`` when ``limit`` is out
                of range or the cursor is invalid or bound to different
                session, team or filters.
        """
        if not _MESSAGE_PAGE_LIMIT_MIN <= limit <= _MESSAGE_PAGE_LIMIT_MAX:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason=f"limit must be between {_MESSAGE_PAGE_LIMIT_MIN} and {_MESSAGE_PAGE_LIMIT_MAX}",
            )
        session_id = _require_session_id(ops)
        resolved_team, view = await _resolve_view(resolver, ops, team_name)
        filters = _message_filters(from_member_name, to_member_name, broadcast)

        offset = 0
        if cursor is not None:
            decoded = _decode_cursor(cursor)
            if decoded.session_id != session_id or decoded.team_name != resolved_team:
                raise_error(
                    StatusCode.AGENT_TEAM_CONFIG_INVALID,
                    reason="message cursor belongs to a different session or team",
                )
            if decoded.filters != filters:
                raise_error(
                    StatusCode.AGENT_TEAM_CONFIG_INVALID,
                    reason="message cursor does not match the current filters",
                )
            if decoded.offset < 0:
                raise_error(StatusCode.AGENT_TEAM_CONFIG_INVALID, reason="message cursor offset is negative")
            offset = decoded.offset

        visible = await view.messages()
        if view.hide_dm and to_member_name is not None:
            # A DM query under a hide-dm visibility policy is empty by
            # construction (mirrors TeamMonitor.get_messages).
            return MessagePage(items=(), next_cursor=None)
        if view.hide_dm:
            visible = [message for message in visible if message.broadcast]
        filtered = [message for message in visible if _matches_filters(message, filters)]
        filtered.sort(key=lambda message: (message.timestamp, message.message_id), reverse=True)
        page = filtered[offset : offset + limit]
        next_cursor = None
        if offset + limit < len(filtered):
            next_cursor = _encode_cursor(
                _MessageCursor(session_id=session_id, team_name=resolved_team, filters=filters, offset=offset + limit),
            )
        return MessagePage(items=tuple(page), next_cursor=next_cursor)

    async def message(
        ops: EventHandlerOps,
        message_id: str,
        *,
        team_name: str | None = None,
    ) -> MessageInfo:
        """Query one message, validating team ownership and visibility.

        Raises:
            BaseError: ``AGENT_TEAM_STATE_INVALID`` when the message is
                missing, belongs to another team, or is not visible to
                this caller (e.g. a direct message under hide-dm).
        """
        resolved_team, view = await _resolve_view(resolver, ops, team_name)
        message_info = await view.message(message_id)
        if message_info is None:
            raise_error(
                StatusCode.AGENT_TEAM_STATE_INVALID,
                reason=f"message '{message_id}' not found",
            )
        if message_info.team_name != resolved_team:
            raise_error(
                StatusCode.AGENT_TEAM_STATE_INVALID,
                reason=f"message '{message_id}' does not belong to team '{resolved_team}'",
            )
        if view.hide_dm and not message_info.broadcast:
            raise_error(
                StatusCode.AGENT_TEAM_STATE_INVALID,
                reason=f"message '{message_id}' is not visible to this caller",
            )
        return message_info

    async def policy_context(_ops: EventHandlerOps) -> TeamPolicyContext:
        """Return the read-only config bound to the current policy build.

        Raises:
            BaseError: ``AGENT_TEAM_STATE_INVALID`` when queried outside a
                policy build — the context exists only inside the build
                entry's scope.
        """
        context = current_policy_context()
        if context is None:
            raise_error(
                StatusCode.AGENT_TEAM_STATE_INVALID,
                reason="policy_context is only available inside a team.build_policy call",
            )
        return context

    return {
        "info": info,
        "members": members,
        "member": member,
        "tasks": tasks,
        "task": task,
        "task_dependencies": task_dependencies,
        "messages": messages,
        "message": message,
        "policy_context": policy_context,
    }


async def _team_ops_build_handler_with(
    resolver: TeamResourceResolver,
    event: Event,
    _ops: EventHandlerOps | None,
    next_handler: EventNext,
) -> EventHandlerOpsBuilder:
    """Extend the ``ops.build`` result with the ``team`` namespace."""
    output = await next_handler(event)
    if not isinstance(output, EventHandlerOpsBuilder):
        raise_error(
            StatusCode.AGENT_TEAM_STATE_INVALID,
            reason="ops.build must yield an EventHandlerOpsBuilder before team ops are added",
        )
    output.add_namespace(_TEAM_NAMESPACE)
    for method_name, method in _build_team_methods(resolver).items():
        output.add_method(_TEAM_NAMESPACE, method_name, method)
    return output


def register(bus, resolver: TeamResourceResolver) -> None:
    """Register the team Ops build handler as an ``ops.build`` BUILTIN.

    The empty match pattern is required by the ``ops.build`` matcher: only
    an empty pattern matches the build call. Must be called during host
    initialization (``Registry.init(host_handlers=...)``) — the bus freezes
    ``ops.build`` handlers once the build has run.

    Args:
        bus: The extension event bus the host initializes.
        resolver: Resolver the team query methods read through.
    """

    async def team_ops_build_handler(
        event: Event,
        ops: EventHandlerOps | None,
        next_handler: EventNext,
    ) -> EventHandlerOpsBuilder:
        return await _team_ops_build_handler_with(resolver, event, ops, next_handler)

    bus.register_event_handler(
        OPS_BUILD_EVENT_TYPE,
        team_ops_build_handler,
        match_pattern={},
        handler_level=HandlerLevel.BUILTIN,
    )


__all__ = [
    "register",
]
