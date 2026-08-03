# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure action scheduling helpers for member optimization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.rsi.member_optimizer.schema import (
    MemberOptimizationAction,
)


def build_action_waves(actions: list[MemberOptimizationAction]) -> list[list[str]]:
    """Build dependency waves via topological sort.

    Throws ValueError if a cycle is detected. Each action appears exactly once.
    """
    remaining: dict[str, set[str]] = {action.action_id: set(action.depends_on) for action in actions}
    waves: list[list[str]] = []
    completed: set[str] = set()

    while remaining:
        ready = sorted(action_id for action_id, deps in remaining.items() if deps <= completed)
        if not ready:
            deps_left = {action_id: sorted(deps) for action_id, deps in remaining.items()}
            raise ValueError(f"dependency cycle detected. Remaining: {deps_left}")
        waves.append(ready)
        completed.update(ready)
        for action_id in ready:
            remaining.pop(action_id)

    return waves


def build_action_waves_from_data(actions: list[dict[str, Any]]) -> list[list[str]]:
    """Build dependency waves from raw action dictionaries."""
    remaining = {action["action_id"]: set(action.get("depends_on", [])) for action in actions}
    waves: list[list[str]] = []
    completed: set[str] = set()

    while remaining:
        ready = sorted(action_id for action_id, deps in remaining.items() if deps <= completed)
        if not ready:
            deps_left = {action_id: sorted(deps) for action_id, deps in remaining.items()}
            raise ValueError(f"dependency cycle detected in raw actions. Remaining: {deps_left}")
        waves.append(ready)
        completed.update(ready)
        for action_id in ready:
            remaining.pop(action_id)

    return waves


def build_waves_from_deps_only(actions: list[dict[str, Any]]) -> list[list[str]]:
    """Fallback wave rebuild that ignores any provided action_waves field."""
    return build_action_waves_from_data(actions)


def build_role_subwaves(
    actions: list[MemberOptimizationAction],
) -> list[list[MemberOptimizationAction]]:
    """Group role-local actions by declared write path overlap.

    Actions with non-overlapping declared_write_paths can run in the same
    subwave. Overlapping actions go in later subwaves.
    """
    if not actions:
        return []

    remaining = list(actions)
    subwaves: list[list[MemberOptimizationAction]] = []

    while remaining:
        first = remaining.pop(0)
        current_subwave = [first]

        later = []
        for action in remaining:
            if any(
                _path_overlaps(
                    resolve_declared_paths(existing_action),
                    resolve_declared_paths(action),
                )
                for existing_action in current_subwave
            ):
                later.append(action)
            else:
                current_subwave.append(action)

        current_subwave.sort(key=lambda action: action.action_id)
        subwaves.append(current_subwave)
        remaining = later

    return subwaves


def resolve_declared_paths(action: MemberOptimizationAction) -> list[str]:
    """Resolve declared_write_paths for an action, defaulting to [target_path]."""
    if action.declared_write_paths:
        return list(action.declared_write_paths)
    return [action.target_path] if action.target_path else []


def _path_overlaps(paths_a: list[str], paths_b: list[str]) -> bool:
    """Return True if any path in a overlaps with any path in b."""
    normalized_a = {_normalize_path(path) for path in paths_a if path}
    normalized_b = {_normalize_path(path) for path in paths_b if path}
    return any(_single_path_overlaps(path_a, path_b) for path_a in normalized_a for path_b in normalized_b)


def _normalize_path(path: str) -> str:
    return Path(path).as_posix().strip("/")


def _single_path_overlaps(path_a: str, path_b: str) -> bool:
    return path_a == path_b or path_a.startswith(f"{path_b}/") or path_b.startswith(f"{path_a}/")


__all__ = [
    "build_action_waves",
    "build_action_waves_from_data",
    "build_role_subwaves",
    "build_waves_from_deps_only",
    "resolve_declared_paths",
]
