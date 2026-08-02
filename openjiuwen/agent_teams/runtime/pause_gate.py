# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-local gate: team/member is pausing or paused.

Used by task mutations (start/complete/claim) so in-flight tools that already
passed ``before_tool_call`` still cannot flip task state after pause arms.
Coordination sets the gate at the start of pause and clears it when pause
finishes / on ``coordination.start``.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
# (team_name, member_name) entries currently pausing/paused.
_paused_members: set[tuple[str, str]] = set()
# Whole-team pause (leader path): blocks every member of that team.
_paused_teams: set[str] = set()


def mark_team_pausing(team_name: str | None) -> None:
    if not team_name:
        return
    with _lock:
        _paused_teams.add(team_name)


def mark_member_pausing(team_name: str | None, member_name: str | None) -> None:
    if not team_name or not member_name:
        return
    with _lock:
        _paused_members.add((team_name, member_name))


def clear_member_pausing(team_name: str | None, member_name: str | None) -> None:
    if not team_name or not member_name:
        return
    with _lock:
        _paused_members.discard((team_name, member_name))


def clear_team_pausing(team_name: str | None) -> None:
    if not team_name:
        return
    with _lock:
        _paused_teams.discard(team_name)
        doomed = [key for key in _paused_members if key[0] == team_name]
        for key in doomed:
            _paused_members.discard(key)


def is_pause_blocking(*, team_name: str | None, member_name: str | None = None) -> bool:
    """True when task start/complete/claim must be rejected."""
    if not team_name:
        return False
    with _lock:
        if team_name in _paused_teams:
            return True
        if member_name and (team_name, member_name) in _paused_members:
            return True
    return False


def pause_block_reason(task_op: str) -> str:
    return (
        f"paused: {task_op} rejected while the team is pausing/paused; "
        "on resume, review context and prior tool results, then retry "
        f"{task_op} and push any products"
    )
