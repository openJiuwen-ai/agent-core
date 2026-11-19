# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pattern matching functions for the team extension events.

A pattern declares which calls a handler wants to see. Matching is strict
and side-effect free: it grants no permission and reads no team config.

Rules (shared by every team event):
    * every specified condition must hold (AND); values compare by exact
      string equality;
    * an empty pattern matches every legal call of that event type;
    * an omitted field does not restrict that dimension — even when the
      call's input has no value for it;
    * a specified field requires the input to carry the same value; an
      input that is missing or ``None`` for that field does not match;
    * pattern values must be non-empty strings; role-like and
      monitor-event-type-like fields must additionally name a member of
      their public enum;
    * unknown keys, wrong value types, ``None`` values, empty strings and
      a wrong input type make the whole pattern match nothing (``False``).

The functions never raise: the event bus treats a raising matcher as a
registration fault, so every illegal shape folds into ``False`` here.
There are no wildcards and no regexes.
"""

from __future__ import annotations

from typing import Any, Mapping

from openjiuwen.agent_teams.extensions.models import TeamMonitorEventInput, TeamPolicyBuildInput
from openjiuwen.agent_teams.monitor.models import MonitorEventType
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.events.types import EventInput, MatchPattern

# Public enum domains a pattern value must belong to when the field is
# enum-backed (plain string fields like team/member names have none).
_MONITOR_EVENT_TYPE_VALUES = frozenset(e.value for e in MonitorEventType)
_ROLE_VALUES = frozenset(r.value for r in TeamRole)


def _is_non_empty_str(value: Any) -> bool:
    """Return whether ``value`` is a legal pattern condition value."""
    return isinstance(value, str) and bool(value)


def _match_all_specified(
    pattern: Mapping[str, Any],
    *,
    enum_domains: Mapping[str, frozenset[str]],
    input_fields: Mapping[str, str | None],
) -> bool:
    """Apply the shared AND/exact/strict rules to one pattern.

    ``input_fields`` maps the fields one call's input carries to their
    values; its keys are exactly the fields the event supports, so a
    pattern key missing from them is an unknown field.
    """
    for key, expected in pattern.items():
        if key not in input_fields:
            return False
        if not _is_non_empty_str(expected):
            return False
        enum_values = enum_domains.get(key)
        if enum_values is not None and expected not in enum_values:
            return False
        actual = input_fields.get(key)
        if actual is None or actual != expected:
            return False
    return True


def match_team_monitor_event(event_input: EventInput, match_pattern: MatchPattern) -> bool:
    """Match a ``team.monitor_event`` pattern against one notification.

    ``member_name`` is the member the notification is *about* (taken from
    ``MonitorEvent.member_name``) — not the receiver, and never inferred
    from a sender or recipient field. ``monitor_event_type`` matches
    ``MonitorEvent.event_type``, a public monitor notification type; the
    framework event type stays fixed at ``team.monitor_event``. A
    notification without a member id cannot match a pattern that specifies
    one; omitting the condition leaves members unrestricted.
    """
    if not isinstance(event_input, TeamMonitorEventInput):
        return False
    return _match_all_specified(
        match_pattern,
        enum_domains={"monitor_event_type": _MONITOR_EVENT_TYPE_VALUES},
        input_fields={
            "monitor_event_type": event_input.monitor_event.event_type.value
            if isinstance(event_input.monitor_event.event_type, MonitorEventType)
            else None,
            "team_name": event_input.team_name,
            "member_name": event_input.member_name,
        },
    )


def match_team_build_policy(event_input: EventInput, match_pattern: MatchPattern) -> bool:
    """Match a ``team.build_policy`` pattern against one build target.

    A build target carries no member id when the policy is role-scoped (a
    fresh leader prefix, for instance); such builds never match a pattern
    that specifies ``member_name``.
    """
    if not isinstance(event_input, TeamPolicyBuildInput):
        return False
    return _match_all_specified(
        match_pattern,
        enum_domains={"role": _ROLE_VALUES},
        input_fields={
            "role": event_input.role.value if isinstance(event_input.role, TeamRole) else None,
            "team_name": event_input.team_name,
            "member_name": event_input.member_name,
        },
    )


__all__ = [
    "match_team_build_policy",
    "match_team_monitor_event",
]
