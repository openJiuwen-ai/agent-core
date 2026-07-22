# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-level KV-cache actions kept outside the business runtime manager.

The runtime manager is passed in only as the source of active Team entries and
as the owner of the original delete/release operations.  Immutable binding
snapshots are stored per manager through weak keys, so stopped runtimes remain
manageable without adding KVC state to ``TeamRuntimeManager``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
from openjiuwen.agent_teams.kv_cache.kv_cache_lifecycle import (
    KVCAction,
    TeamKVCActionPlan,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager


_MANIFESTS: WeakKeyDictionary[
    "TeamRuntimeManager",
    dict[tuple[str, str], TeamKVCActionPlan],
] = WeakKeyDictionary()


def _manifests(manager: "TeamRuntimeManager") -> dict[tuple[str, str], TeamKVCActionPlan]:
    manifests = _MANIFESTS.get(manager)
    if manifests is None:
        manifests = {}
        _MANIFESTS[manager] = manifests
    return manifests


def _existing_manifests(manager: "TeamRuntimeManager") -> dict[tuple[str, str], TeamKVCActionPlan]:
    return _MANIFESTS.get(manager, {})


async def prepare_action(
    manager: "TeamRuntimeManager",
    *,
    action: KVCAction,
    team_name: str,
    session_id: str,
) -> bool:
    """Snapshot a plan before the Team runtime may leave the active pool."""
    if action not in {"offload", "prefetch", "evict"}:
        return False
    entry = await manager.pool.get(team_name)
    if entry is None or entry.current_session_id != session_id:
        return (team_name, session_id) in _existing_manifests(manager)
    plan = await kv_cache_hooks.build_action_plan(entry.agent, action)
    if plan is None:
        return False
    _manifests(manager)[(team_name, session_id)] = plan
    return True


async def dispatch_action(
    manager: "TeamRuntimeManager",
    *,
    action: KVCAction,
    team_name: str,
    session_id: str,
    reason: str,
) -> bool:
    """Dispatch a non-blocking Team offload/prefetch signal."""
    if action not in {"offload", "prefetch"}:
        return False
    plan = await _resolve_action_plan(
        manager,
        action=action,
        team_name=team_name,
        session_id=session_id,
    )
    return kv_cache_hooks.dispatch_action_plan(plan, reason=reason)


async def execute_action(
    manager: "TeamRuntimeManager",
    *,
    action: KVCAction,
    team_name: str,
    session_id: str,
    reason: str,
) -> bool:
    """Await a permanent Team evict without changing the business result."""
    if action != "evict":
        return False
    plan = await _resolve_action_plan(
        manager,
        action=action,
        team_name=team_name,
        session_id=session_id,
    )
    if plan is None:
        _discard(manager, team_name=team_name, session_ids=[session_id])
        return False
    await kv_cache_hooks.execute_action_plan(plan, reason=reason)
    _discard(manager, team_name=team_name, session_ids=[session_id])
    # True means this lifecycle event had a concrete Team plan. Provider
    # failures remain best-effort and must not trigger a duplicate root evict.
    return True


async def delete_team(
    manager: "TeamRuntimeManager",
    *,
    team_name: str,
    session_ids: list[str],
    force: bool,
) -> bool:
    """Run the original Team deletion and then evict its captured caches."""
    prepared: list[str] = []
    for session_id in session_ids:
        if await prepare_action(
            manager,
            action="evict",
            team_name=team_name,
            session_id=session_id,
        ):
            prepared.append(session_id)

    deleted = await manager.delete_team(
        team_name=team_name,
        session_ids=session_ids,
        force=force,
    )
    if not deleted:
        return deleted

    for session_id in prepared:
        await execute_action(
            manager,
            action="evict",
            team_name=team_name,
            session_id=session_id,
            reason="team_delete",
        )
    _discard(manager, team_name=team_name, session_ids=session_ids)
    return deleted


async def prepare_session_release(
    manager: "TeamRuntimeManager",
    *,
    team_names: list[str],
    session_id: str,
) -> tuple[str, ...]:
    """Capture every Team plan before the original session release."""
    prepared = []
    for team_name in team_names:
        if await prepare_action(
            manager,
            action="evict",
            team_name=team_name,
            session_id=session_id,
        ):
            prepared.append(team_name)
    return tuple(prepared)


async def complete_session_release(
    manager: "TeamRuntimeManager",
    *,
    team_names: tuple[str, ...],
    session_id: str,
) -> None:
    """Evict captured Team caches after the original release has succeeded."""
    for team_name in team_names:
        await execute_action(
            manager,
            action="evict",
            team_name=team_name,
            session_id=session_id,
            reason="team_session_release",
        )
    _discard(manager, session_ids=[session_id])


async def _resolve_action_plan(
    manager: "TeamRuntimeManager",
    *,
    action: KVCAction,
    team_name: str,
    session_id: str,
) -> TeamKVCActionPlan | None:
    entry = await manager.pool.get(team_name)
    if entry is not None and entry.current_session_id == session_id:
        plan = await kv_cache_hooks.build_action_plan(entry.agent, action)
        if plan is not None:
            _manifests(manager)[(team_name, session_id)] = plan
            return plan
    manifest = _existing_manifests(manager).get((team_name, session_id))
    return manifest.for_action(action) if manifest is not None else None


def _discard(
    manager: "TeamRuntimeManager",
    *,
    session_ids: list[str],
    team_name: str | None = None,
) -> None:
    manifests = _existing_manifests(manager)
    session_id_set = set(session_ids)
    for key in tuple(manifests):
        stored_team_name, stored_session_id = key
        if stored_session_id not in session_id_set:
            continue
        if team_name is not None and stored_team_name != team_name:
            continue
        manifests.pop(key, None)
    if not manifests:
        _MANIFESTS.pop(manager, None)


__all__ = [
    "complete_session_release",
    "delete_team",
    "dispatch_action",
    "execute_action",
    "prepare_action",
    "prepare_session_release",
]
