# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Thin hooks between Team runtime lifecycle events and KV-cache management.

The Team runtime should only signal lifecycle moments here. Registry state,
provider actions, and capability checks stay in ``kv_cache_lifecycle``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from openjiuwen.agent_teams.kv_cache.kv_cache_lifecycle import (
    KVCAction,
    KVCacheRuntimeBinding,
    TeamKVCActionPlan,
    TeamKVCacheRegistry,
    build_runtime_binding,
    dispatch_action_plan as _dispatch_action_plan,
    execute_action_plan as _execute_action_plan,
    register_harness_binding as _register_runtime_binding,
)
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent


@dataclass
class _HarnessSessionHookState:
    """KVC-only state associated with one standalone worker Harness."""

    parent_session_id: str | None
    evict_on_finish: bool
    reason: str
    owner_id: str
    session: Any | None = None


_HARNESS_SESSION_HOOKS: WeakKeyDictionary[Any, _HarnessSessionHookState] = (
    WeakKeyDictionary()
)


def ensure_leader_registry(agent: "TeamAgent") -> None:
    """Create the process-local Team KVC registry for the leader runtime."""
    try:
        resources = agent.resources
        if resources.team_kv_cache_registry is None:
            resources.team_kv_cache_registry = TeamKVCacheRegistry()
    except Exception as exc:
        _log_best_effort_failure("ensure_leader_registry", agent, exc)


def share_registry_with_teammate(leader: "TeamAgent", teammate: "TeamAgent") -> None:
    """Share the leader-owned process-local registry with an in-process teammate."""
    try:
        teammate.resources.team_kv_cache_registry = leader.resources.team_kv_cache_registry
    except Exception as exc:
        _log_best_effort_failure("share_registry_with_teammate", teammate, exc)


async def register_harness_binding(host: Any, harness: Any | None = None) -> None:
    try:
        resources = getattr(host, "resources", None)
        if harness is None:
            harness = getattr(resources, "harness", None)
        registry = _registry_from_host(host)
        card = getattr(host, "card", None)
        member_id = getattr(card, "id", None)
        if harness is None or member_id is None:
            return
        await _register_runtime_binding(
            registry,
            member_id=member_id,
            member_name=getattr(host, "member_name", None),
            harness=harness,
            is_leader=_is_leader(host),
        )
    except Exception as exc:
        _log_best_effort_failure("register_harness_binding", host, exc)


async def mark_ready_resident(agent: "TeamAgent") -> None:
    try:
        registry = _registry_from_agent(agent)
        if registry is not None:
            await registry.mark_ready_resident(_member_id(agent))
    except Exception as exc:
        _log_best_effort_failure("mark_ready_resident", agent, exc)


async def evict_member(agent: "TeamAgent", *, reason: str) -> None:
    try:
        registry = _registry_from_agent(agent)
        if registry is not None:
            await registry.evict_member(_member_id(agent), reason=reason)
    except Exception as exc:
        _log_best_effort_failure("evict_member", agent, exc)


async def build_action_plan(
    agent_or_host: Any,
    action: KVCAction,
) -> TeamKVCActionPlan | None:
    """Snapshot provider bindings before the business runtime is torn down."""
    try:
        registry = _registry_from_agent_or_host(agent_or_host)
        if registry is None:
            return None
        return await registry.build_action_plan(action)
    except Exception as exc:
        _log_best_effort_failure(f"build_{action}_plan", agent_or_host, exc)
        return None


def dispatch_action_plan(plan: TeamKVCActionPlan | None, *, reason: str) -> bool:
    """Dispatch an immutable offload/prefetch plan as a managed signal."""
    if plan is None:
        return False
    try:
        return _dispatch_action_plan(plan, reason=reason)
    except Exception as exc:
        _log_plan_failure(f"dispatch_{plan.action}", plan, exc)
        return False


async def execute_action_plan(
    plan: TeamKVCActionPlan | None,
    *,
    reason: str,
) -> list[bool]:
    """Await an immutable evict plan without changing the business result."""
    if plan is None:
        return []
    try:
        return await _execute_action_plan(plan, reason=reason)
    except Exception as exc:
        _log_plan_failure(f"execute_{plan.action}", plan, exc)
        return []


async def has_manageable_member_binding(agent: "TeamAgent") -> bool:
    try:
        registry = _registry_from_agent(agent)
        if registry is None:
            return False
        return await registry.has_actionable_member(_member_id(agent), "evict")
    except Exception as exc:
        _log_best_effort_failure("has_manageable_member_binding", agent, exc)
        return False


async def evict_terminal_harness_session(
    harness: Any,
    session: Any,
    *,
    reason: str,
    owner_id: str,
) -> bool:
    """Evict one terminal standalone Session without owning its disposal."""
    if not is_harness_affinity_enabled(harness):
        return False
    try:
        binding = _build_harness_session_binding(harness, session)
        if binding is None:
            return False
        from openjiuwen.agent_teams.kv_cache.kv_cache_cleanup import (
            cancellation_safe_evict,
        )

        return await cancellation_safe_evict(
            binding=binding,
            reason=reason,
            owner_id=owner_id,
        )
    except Exception as exc:
        _log_best_effort_failure("evict_terminal_harness_session", harness, exc)
        return False


def configure_harness_session_hooks(
    harness: Any,
    *,
    product_session_id: Any,
    evict_on_finish: bool,
    reason: str = "",
    owner_id: str = "",
) -> bool:
    """Register standalone worker lineage and its owner-selected terminal action."""
    if not is_harness_affinity_enabled(harness):
        return False
    try:
        parent_session_id = _normalize_session_id(product_session_id)
        _HARNESS_SESSION_HOOKS[harness] = _HarnessSessionHookState(
            parent_session_id=parent_session_id,
            evict_on_finish=evict_on_finish,
            reason=reason,
            owner_id=owner_id,
        )
        return True
    except Exception as exc:
        _log_best_effort_failure("configure_harness_session_hooks", harness, exc)
        return False


def on_harness_session_created(harness: Any, session: Any) -> None:
    """Bind the actual child Session before pre-run or model inference starts."""
    try:
        state = _HARNESS_SESSION_HOOKS.get(harness)
        if state is None:
            return
        state.session = session
        if state.parent_session_id is None:
            return
        bind_parent = getattr(session, "bind_parent_session_id", None)
        if callable(bind_parent):
            bind_parent(state.parent_session_id)
    except Exception as exc:
        _log_best_effort_failure("on_harness_session_created", harness, exc)


async def after_harness_session_finished(harness: Any, session: Any) -> None:
    """Run an owner-selected terminal action after one-shot Session cleanup."""
    try:
        state = _HARNESS_SESSION_HOOKS.get(harness)
        if state is None or not state.evict_on_finish:
            return
        await evict_terminal_harness_session(
            harness,
            session,
            reason=state.reason,
            owner_id=state.owner_id,
        )
    finally:
        try:
            state = _HARNESS_SESSION_HOOKS.get(harness)
            if state is not None and state.evict_on_finish:
                _HARNESS_SESSION_HOOKS.pop(harness, None)
        except Exception as exc:
            _log_best_effort_failure(
                "after_harness_session_finished_cleanup",
                harness,
                exc,
            )


def build_harness_session_binding(
    harness: Any,
    session: Any,
) -> KVCacheRuntimeBinding | None:
    """Build a manageable binding from a Session owned by the caller."""
    if not is_harness_affinity_enabled(harness):
        return None
    try:
        return _build_harness_session_binding(harness, session)
    except Exception as exc:
        _log_best_effort_failure("build_harness_session_binding", harness, exc)
        return None


def build_current_harness_binding(harness: Any) -> KVCacheRuntimeBinding | None:
    """Snapshot the active Session registered for a stateful worker Harness."""
    if not is_harness_affinity_enabled(harness):
        return None
    try:
        state = _HARNESS_SESSION_HOOKS.get(harness)
        session = state.session if state is not None else None
        return _build_harness_session_binding(harness, session)
    except Exception as exc:
        _log_best_effort_failure("build_current_harness_binding", harness, exc)
        return None


def clear_harness_session_hooks(harness: Any) -> None:
    """Forget KVC-only state after a stateful Harness is physically closed."""
    try:
        _HARNESS_SESSION_HOOKS.pop(harness, None)
    except Exception as exc:
        _log_best_effort_failure("clear_harness_session_hooks", harness, exc)


def is_harness_affinity_enabled(harness: Any) -> bool:
    """Return False on any config failure so the baseline path stays intact."""
    try:
        deep_config = getattr(harness, "deep_config", None)
        config = getattr(deep_config, "kv_cache_affinity_config", None)
        return getattr(config, "enable_kv_cache_affinity", False) is True
    except Exception as exc:
        _log_best_effort_failure("is_harness_affinity_enabled", harness, exc)
        return False


def _build_harness_session_binding(
    harness: Any,
    session: Any,
) -> KVCacheRuntimeBinding | None:
    if session is None:
        return None
    identity_fn = getattr(session, "get_cache_identity", None)
    identity = identity_fn() if callable(identity_fn) else None
    if identity is None:
        return None
    return build_runtime_binding(harness, identity)


def _normalize_session_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _member_id(agent: "TeamAgent") -> str:
    card = getattr(agent, "card", None)
    return getattr(card, "id", None) or getattr(agent, "member_name", "?")


def _is_leader(agent_or_host: Any) -> bool:
    role = getattr(agent_or_host, "role", None)
    return str(getattr(role, "value", role) or "").strip().lower() == "leader"


def _registry_from_agent(agent: "TeamAgent") -> TeamKVCacheRegistry | None:
    resources = getattr(agent, "resources", None)
    return getattr(resources, "team_kv_cache_registry", None)


def _registry_from_host(host: Any) -> TeamKVCacheRegistry | None:
    resources = getattr(host, "resources", None)
    return getattr(resources, "team_kv_cache_registry", None)


def _registry_from_agent_or_host(agent_or_host: Any) -> TeamKVCacheRegistry | None:
    registry = _registry_from_agent(agent_or_host)
    if registry is not None:
        return registry
    return _registry_from_host(agent_or_host)


def _log_best_effort_failure(action: str, agent_or_host: Any, exc: Exception) -> None:
    card = getattr(agent_or_host, "card", None)
    member_id = getattr(card, "id", None)
    member_name = getattr(agent_or_host, "member_name", None)
    team_logger.warning(
        "[TeamKVC] best-effort hook failed: action={} member_id={} member_name={} error={}",
        action,
        member_id,
        member_name,
        exc,
    )


def _log_plan_failure(action: str, plan: TeamKVCActionPlan, exc: Exception) -> None:
    team_logger.warning(
        "[TeamKVC] best-effort plan hook failed: action={} root_cache_id={} error={}",
        action,
        plan.root_cache_id,
        exc,
    )
