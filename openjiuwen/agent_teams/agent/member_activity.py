# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-side in-memory view of every member's activity, the leader included.

The team database already stores each member's status, but reading it back on
every question is both a full table scan and a lie by omission: the leader's
own transitions never reach it as events (``CoordinationKernel._filter_self``
drops self-published events), so no single event stream carries the whole
roster. This registry is the join point — the leader folds *its own* status
transitions in locally and every other member's in from ``MEMBER_*`` events,
against a baseline seeded from the database at run-cycle start.

Its one question is "is anything in this team still moving?". Every answer
comes back as an :class:`IdleSignal` telling the caller what the pending
``team.idle`` marker owes; the marker itself, and the debounce window it waits
out, belong to ``StreamController`` (``TeamAgent.observe_member_status`` wires
the two together). **No timing concept lives in here** — this stays a plain
data structure that can be reasoned about one call at a time.

Membership of the "not moving" set is ``MEMBER_QUIESCENT_STATUSES`` —
deliberately a different set from the completion check's
``MEMBER_SETTLED_STATUSES``; see the comment on both in ``schema/status.py``.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

from openjiuwen.agent_teams.schema.status import (
    MEMBER_QUIESCENT_STATUSES,
    MemberStatus,
)


class IdleSignal(Enum):
    """What an observed status change means for the pending team-idle marker.

    The registry decides *whether* the marker is owed; the caller owns the
    timer that actually delivers it. Keeping the two apart is what lets the
    marker be debounced without any timing concept leaking in here.

    Values:
        NONE: Nothing to do — the marker's pending state is unchanged.
        SCHEDULE: Everything is at rest and a marker is owed; arm the timer.
        CANCEL: Something is moving; drop any marker still waiting to fire.
    """

    NONE = "none"
    SCHEDULE = "schedule"
    CANCEL = "cancel"


def parse_member_status(value: str | None) -> MemberStatus | None:
    """Convert a raw event-payload status string into a ``MemberStatus``.

    Args:
        value: Status string carried by a ``MEMBER_*`` event payload.

    Returns:
        The matching ``MemberStatus``, or ``None`` when the value is empty or
        not a known status (a newer peer publishing a status this process does
        not know about must not crash the observer).
    """
    if not value:
        return None
    try:
        return MemberStatus(value)
    except ValueError:
        return None


class MemberActivityRegistry:
    """Roster of member statuses held in the leader's memory.

    Not persisted and not shared across processes: it is a projection the
    leader rebuilds from the database at every run-cycle start and keeps
    current from events. A member the leader never hears about simply keeps
    its seeded status.

    Attributes:
        self_member_name: The leader's own member name, always present in the
            roster so "including myself" needs no special case at the call
            sites.
    """

    def __init__(self, self_member_name: str) -> None:
        """Initialize the registry with only the owning member in it.

        Args:
            self_member_name: The owning (leader) member name.
        """
        self.self_member_name = self_member_name
        self._statuses: dict[str, MemberStatus] = {self_member_name: MemberStatus.UNSTARTED}
        # Whether a "team went idle" edge is still owed to the consumer.
        # Starts disarmed on purpose: a freshly built team is trivially
        # quiescent (nobody started yet), and announcing idle before anyone
        # ever worked would be noise. Something has to move first; the fall
        # back to rest is the signal.
        self._armed = False

    def seed(self, statuses: Mapping[str, MemberStatus]) -> None:
        """Replace the roster with a database-read baseline.

        The owning member is preserved when the baseline does not carry it —
        a leader's own row only materializes once ``build_team`` runs, and an
        empty or missing team is an ordinary state, not an error.

        Args:
            statuses: Member name to status, as read from the team database.
        """
        merged = dict(statuses)
        if self.self_member_name not in merged:
            merged[self.self_member_name] = self._statuses.get(self.self_member_name, MemberStatus.UNSTARTED)
        self._statuses = merged

    def record(self, member_name: str, status: MemberStatus) -> IdleSignal:
        """Record one member's current status and report what the marker owes.

        Any observation of movement returns ``CANCEL`` unconditionally rather
        than tracking whether a marker is actually pending: cancelling nothing
        is free, and the alternative is this object second-guessing a timer it
        does not own.

        Args:
            member_name: The member whose status is being observed.
            status: Its current status.

        Returns:
            ``CANCEL`` while anything is moving, ``SCHEDULE`` exactly once per
            idle edge (every member at rest, with at least one having been
            active since the previous ``SCHEDULE``), ``NONE`` otherwise.
        """
        self._statuses[member_name] = status
        if not self.is_idle():
            self._armed = True
            return IdleSignal.CANCEL
        if not self._armed:
            return IdleSignal.NONE
        self._armed = False
        return IdleSignal.SCHEDULE

    def is_idle(self) -> bool:
        """Return whether every known member is currently at rest."""
        return all(status in MEMBER_QUIESCENT_STATUSES for status in self._statuses.values())

    def snapshot(self) -> dict[str, str]:
        """Return a name to status-value copy of the roster, for reporting."""
        return {name: status.value for name, status in self._statuses.items()}


__all__ = ["IdleSignal", "MemberActivityRegistry", "parse_member_status"]
