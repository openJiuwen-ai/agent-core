# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime trajectory registry.

This module stores shared runtime trajectory evidence windows. It is
not a persistence layer and should not be used as an execution audit log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from threading import RLock
from typing import Protocol

from openjiuwen.agent_evolving.trajectory.aggregator import aggregate_member_trajectories
from openjiuwen.agent_evolving.trajectory.types import Trajectory


@dataclass(frozen=True)
class MemberTrajectorySnapshot:
    """Latest bounded trajectory view for one team member in one session."""

    team_id: str
    session_id: str
    member_id: str
    member_role: str | None
    trajectory: Trajectory
    recorded_at_ms: int

    @classmethod
    def make(
        cls,
        *,
        team_id: str,
        member_id: str,
        trajectory: Trajectory,
        member_role: str | None = None,
        session_id: str | None = None,
        recorded_at_ms: int | None = None,
    ) -> "MemberTrajectorySnapshot":
        """Create a snapshot with runtime defaults filled in."""
        return cls(
            team_id=team_id,
            session_id=session_id if session_id is not None else trajectory.session_id or "",
            member_id=member_id,
            member_role=member_role,
            trajectory=trajectory,
            recorded_at_ms=recorded_at_ms if recorded_at_ms is not None else now_ms(),
        )


class TrajectorySink(Protocol):
    """Write endpoint for member trajectory snapshots."""

    def publish_member_trajectory(self, snapshot: MemberTrajectorySnapshot) -> None:
        """Publish the latest trajectory snapshot for one member."""


class TrajectorySource(Protocol):
    """Read endpoint for aggregated trajectory evidence."""

    def get_trajectory(
        self,
        *,
        team_id: str,
        session_id: str,
        filter_collaborative: bool = True,
    ) -> Trajectory | None:
        """Return the aggregated team trajectory for a session."""


@dataclass(frozen=True)
class _SnapshotEntry:
    """Registry-local ordering metadata for a received snapshot."""

    snapshot: MemberTrajectorySnapshot
    sequence: int


class InMemoryTrajectoryRegistry:
    """In-memory implementation of trajectory source and sink."""

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], dict[str, _SnapshotEntry]] = {}
        self._sequence = 0
        self._lock = RLock()

    def publish_member_trajectory(self, snapshot: MemberTrajectorySnapshot) -> None:
        key = (snapshot.team_id, snapshot.session_id)
        with self._lock:
            self._sequence += 1
            incoming = _SnapshotEntry(snapshot=snapshot, sequence=self._sequence)
            members = self._snapshots.setdefault(key, {})
            current = members.get(snapshot.member_id)
            if current is not None and _should_keep_current(current, incoming):
                return
            members[snapshot.member_id] = incoming

    def get_trajectory(
        self,
        *,
        team_id: str,
        session_id: str,
        filter_collaborative: bool = True,
    ) -> Trajectory | None:
        key = (team_id, session_id)
        with self._lock:
            snapshots = [entry.snapshot for entry in self._snapshots.get(key, {}).values()]
        if not snapshots:
            return None
        return aggregate_member_trajectories(
            [_trajectory_for_snapshot(snapshot) for snapshot in snapshots],
            team_id=team_id,
            session_id=session_id,
            filter_collaborative=filter_collaborative,
        )

    def clear_session(self, *, team_id: str, session_id: str) -> None:
        with self._lock:
            self._snapshots.pop((team_id, session_id), None)


def now_ms() -> int:
    """Return current wall-clock time in milliseconds."""

    return int(time.time() * 1000)


def _trajectory_for_snapshot(snapshot: MemberTrajectorySnapshot) -> Trajectory:
    meta = dict(snapshot.trajectory.meta)
    meta["member_id"] = snapshot.member_id
    if snapshot.member_role is not None:
        meta["member_role"] = snapshot.member_role
    return replace(snapshot.trajectory, meta=meta)


def _should_keep_current(
    current: _SnapshotEntry,
    incoming: _SnapshotEntry,
) -> bool:
    if incoming.snapshot.recorded_at_ms != current.snapshot.recorded_at_ms:
        return current.snapshot.recorded_at_ms > incoming.snapshot.recorded_at_ms
    return current.sequence >= incoming.sequence


__all__ = [
    "InMemoryTrajectoryRegistry",
    "MemberTrajectorySnapshot",
    "TrajectorySink",
    "TrajectorySource",
    "now_ms",
]
