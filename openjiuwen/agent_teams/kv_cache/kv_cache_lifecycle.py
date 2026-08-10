# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-local KV cache lifecycle registry for agent teams."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.kv_cache import (
    KVCacheIdentity,
    evict_session_kv_cache,
    offload_session_kv_cache,
    prefetch_session_kv_cache,
)


class TeamKVCState(str, Enum):
    ACTIVE = "active"
    READY_RESIDENT = "ready_resident"
    OFFLOADED = "offloaded"
    EVICTED = "evicted"


@dataclass(frozen=True, slots=True)
class KVCacheRuntimeBinding:
    identity: KVCacheIdentity
    model: Any
    enabled: bool


KVCAction = Literal["offload", "prefetch", "evict"]


@dataclass(frozen=True, slots=True)
class TeamKVCActionStep:
    """One provider-facing request in an immutable Team action plan."""

    binding: KVCacheRuntimeBinding
    member_ids: tuple[str, ...]
    uses_root_identity: bool


@dataclass(frozen=True, slots=True)
class TeamKVCActionPlan:
    """Immutable binding snapshot that remains valid after Team teardown."""

    action: KVCAction
    root_cache_id: str
    steps: tuple[TeamKVCActionStep, ...]

    def for_action(self, action: KVCAction) -> "TeamKVCActionPlan":
        return TeamKVCActionPlan(
            action=action,
            root_cache_id=self.root_cache_id,
            steps=self.steps,
        )


@dataclass(slots=True)
class TeamKVCRecord:
    member_id: str
    member_name: str | None
    binding: KVCacheRuntimeBinding
    is_leader: bool = False
    state: TeamKVCState = TeamKVCState.ACTIVE
    last_action: str | None = None
    last_error: str | None = None
    action_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class TeamKVCacheRegistry:
    """Team-runtime-scoped process-local registry of member KVC bindings."""

    def __init__(self, *, action_concurrency: int = 8) -> None:
        self._records_by_member_id: dict[str, TeamKVCRecord] = {}
        self._member_name_to_member_id: dict[str, str] = {}
        self._registry_lock = asyncio.Lock()
        self._registration_frozen = False
        self._closing = False
        self._action_concurrency = max(1, action_concurrency)

    @property
    def registration_frozen(self) -> bool:
        return self._registration_frozen

    @property
    def closing(self) -> bool:
        return self._closing

    async def freeze_registration(self) -> None:
        async with self._registry_lock:
            self._registration_frozen = True

    async def unfreeze_registration(self) -> None:
        async with self._registry_lock:
            if not self._closing:
                self._registration_frozen = False

    async def set_closing(self) -> None:
        async with self._registry_lock:
            self._registration_frozen = True
            self._closing = True

    async def clear(self) -> None:
        async with self._registry_lock:
            self._records_by_member_id.clear()
            self._member_name_to_member_id.clear()
            self._registration_frozen = False
            self._closing = False

    async def register_or_update(
        self,
        *,
        member_id: str,
        member_name: str | None,
        binding: KVCacheRuntimeBinding,
        is_leader: bool = False,
    ) -> TeamKVCRecord | None:
        if not member_id or not is_binding_manageable(binding):
            return None
        async with self._registry_lock:
            if self._registration_frozen or self._closing:
                team_logger.debug(
                    "[TeamKVC] skip registration while frozen/closing: member_id={} closing={}",
                    member_id,
                    self._closing,
                )
                return None
            record = self._records_by_member_id.get(member_id)
            if record is None:
                record = TeamKVCRecord(
                    member_id=member_id,
                    member_name=member_name,
                    binding=binding,
                    is_leader=is_leader,
                    state=TeamKVCState.ACTIVE,
                )
                self._records_by_member_id[member_id] = record
            else:
                if record.binding.identity.cache_id != binding.identity.cache_id:
                    team_logger.warning(
                        "[TeamKVC] member identity changed; keep existing record identity: "
                        "member_id={} old_cache_id={} new_cache_id={}",
                        member_id,
                        record.binding.identity.cache_id,
                        binding.identity.cache_id,
                    )
                    return record
                record.binding = binding
                record.is_leader = record.is_leader or is_leader
                if record.state is not TeamKVCState.EVICTED:
                    record.state = TeamKVCState.ACTIVE
            if member_name:
                self._member_name_to_member_id[member_name] = member_id
            return record

    async def get_record(self, member_id: str) -> TeamKVCRecord | None:
        async with self._registry_lock:
            return self._records_by_member_id.get(member_id)

    async def get_record_by_member_name(self, member_name: str) -> TeamKVCRecord | None:
        async with self._registry_lock:
            member_id = self._member_name_to_member_id.get(member_name)
            return self._records_by_member_id.get(member_id) if member_id else None

    async def mark_ready_resident(self, member_id: str) -> None:
        record = await self.get_record(member_id)
        if record is None:
            return
        async with record.action_lock:
            if record.state is not TeamKVCState.EVICTED:
                record.state = TeamKVCState.READY_RESIDENT

    async def mark_active(self, member_id: str) -> None:
        record = await self.get_record(member_id)
        if record is None:
            return
        async with record.action_lock:
            if record.state is not TeamKVCState.EVICTED:
                record.state = TeamKVCState.ACTIVE

    async def offload_member(self, member_id: str, *, reason: str) -> bool:
        if self._closing:
            return False
        record = await self.get_record(member_id)
        return await self._run_record_action(record, "offload", reason=reason)

    async def prefetch_member(self, member_id: str, *, reason: str) -> bool:
        if self._closing:
            return False
        record = await self.get_record(member_id)
        return await self._run_record_action(record, "prefetch", reason=reason)

    async def evict_member(self, member_id: str, *, reason: str) -> bool:
        record = await self.get_record(member_id)
        return await self._run_record_action(record, "evict", reason=reason)

    async def offload_all(self, *, reason: str) -> list[bool]:
        if self._closing:
            return []
        return await self._run_all("offload", reason=reason)

    async def prefetch_offloaded(self, *, reason: str) -> list[bool]:
        if self._closing:
            return []
        return await self._run_all("prefetch", reason=reason, states={TeamKVCState.OFFLOADED})

    async def evict_all(self, *, reason: str) -> list[bool]:
        return await self._run_all("evict", reason=reason)

    async def snapshot(self) -> list[TeamKVCRecord]:
        async with self._registry_lock:
            return list(self._records_by_member_id.values())

    async def build_action_plan(self, action: KVCAction) -> TeamKVCActionPlan | None:
        """Snapshot a conservative root/child plan without retaining live records."""
        if action not in {"offload", "prefetch", "evict"}:
            raise ValueError(f"Unsupported KVC action: {action}")

        records = await self.snapshot()
        snapshots: list[tuple[str, KVCacheRuntimeBinding, bool]] = []
        for record in records:
            async with record.action_lock:
                if record.state is TeamKVCState.EVICTED:
                    continue
                if is_binding_manageable(record.binding):
                    snapshots.append((record.member_id, record.binding, record.is_leader))
        if not snapshots:
            return None

        leader = next((item for item in snapshots if item[2]), None)
        if leader is None:
            # Without a known leader binding, a guessed root endpoint could
            # cross control domains. Individual child requests are safer.
            steps = tuple(
                TeamKVCActionStep(
                    binding=binding,
                    member_ids=(member_id,),
                    uses_root_identity=False,
                )
                for member_id, binding, _ in snapshots
            )
            return TeamKVCActionPlan(action=action, root_cache_id="", steps=steps)

        leader_member_id, leader_binding, _ = leader
        root_cache_id = leader_binding.identity.parent_cache_id
        root_domain = binding_control_domain(leader_binding)
        root_member_ids: list[str] = []
        child_steps: list[TeamKVCActionStep] = []
        for member_id, binding, _ in snapshots:
            same_root = binding.identity.parent_cache_id == root_cache_id
            if same_root and binding_control_domain(binding) == root_domain:
                root_member_ids.append(member_id)
                continue
            child_steps.append(
                TeamKVCActionStep(
                    binding=binding,
                    member_ids=(member_id,),
                    uses_root_identity=False,
                )
            )

        root_binding = KVCacheRuntimeBinding(
            identity=KVCacheIdentity(
                cache_id=root_cache_id,
                parent_cache_id=root_cache_id,
            ),
            model=leader_binding.model,
            enabled=leader_binding.enabled,
        )
        steps = [
            TeamKVCActionStep(
                binding=root_binding,
                member_ids=tuple(root_member_ids or [leader_member_id]),
                uses_root_identity=True,
            ),
            *child_steps,
        ]
        return TeamKVCActionPlan(
            action=action,
            root_cache_id=root_cache_id,
            steps=tuple(steps),
        )

    async def has_actionable_records(self, action: str) -> bool:
        """Whether at least one record can perform the action now."""
        records = await self.snapshot()
        for record in records:
            async with record.action_lock:
                if _record_actionable(record, action):
                    return True
        return False

    async def has_actionable_member(self, member_id: str, action: str) -> bool:
        """Whether one member record can perform the action now."""
        record = await self.get_record(member_id)
        if record is None:
            return False
        async with record.action_lock:
            return _record_actionable(record, action)

    async def has_records(self) -> bool:
        async with self._registry_lock:
            return bool(self._records_by_member_id)

    async def _run_all(
        self,
        action: str,
        *,
        reason: str,
        states: set[TeamKVCState] | None = None,
    ) -> list[bool]:
        records = await self.snapshot()
        if states is not None:
            records = [record for record in records if record.state in states]
        sem = asyncio.Semaphore(self._action_concurrency)

        async def _one(record: TeamKVCRecord) -> bool:
            async with sem:
                return await self._run_record_action(record, action, reason=reason)

        return await asyncio.gather(*(_one(record) for record in records), return_exceptions=False)

    async def _run_record_action(
        self,
        record: TeamKVCRecord | None,
        action: str,
        *,
        reason: str,
    ) -> bool:
        if record is None:
            return False
        async with record.action_lock:
            if record.state is TeamKVCState.EVICTED:
                return False
            if action == "offload" and record.state is TeamKVCState.OFFLOADED:
                return False
            if action == "prefetch" and record.state is not TeamKVCState.OFFLOADED:
                return False

            binding = record.binding
            ok = await run_binding_action(binding, action, member_id=record.member_id, reason=reason)
            record.last_action = action
            if ok:
                record.last_error = None
                if action == "offload":
                    record.state = TeamKVCState.OFFLOADED
                elif action == "prefetch":
                    record.state = TeamKVCState.ACTIVE
                elif action == "evict":
                    record.state = TeamKVCState.EVICTED
            else:
                record.last_error = f"{action} failed"
                if action == "prefetch":
                    record.state = TeamKVCState.ACTIVE
            return ok


def _record_actionable(record: TeamKVCRecord, action: str) -> bool:
    if not is_binding_manageable(record.binding):
        return False
    if record.state is TeamKVCState.EVICTED:
        return False
    if action == "offload":
        return record.state is not TeamKVCState.OFFLOADED
    if action == "prefetch":
        return record.state is TeamKVCState.OFFLOADED
    if action == "evict":
        return True
    raise ValueError(f"Unsupported KVC action: {action}")


def is_binding_manageable(binding: KVCacheRuntimeBinding | None) -> bool:
    if binding is None or not binding.enabled or binding.model is None:
        return False
    identity = binding.identity
    if not identity.cache_id or not identity.parent_cache_id:
        return False
    supports = getattr(binding.model, "supports_kv_cache_affinity", None)
    if not callable(supports):
        return False
    try:
        if not supports():
            return False
    except Exception as exc:
        team_logger.warning(
            "[TeamKVC] KV affinity capability check failed closed: cache_id={} error={}",
            identity.cache_id,
            exc,
        )
        return False
    return True


def binding_control_domain(binding: KVCacheRuntimeBinding) -> tuple[str, ...]:
    """Return the endpoint/model/cache namespace that can share one root action."""
    model = binding.model
    client_config = getattr(model, "model_client_config", None)
    request_config = getattr(model, "model_config", None)

    provider = _normalized_config_value(getattr(client_config, "client_provider", None)).lower()
    api_base = _normalized_config_value(getattr(client_config, "api_base", None)).rstrip("/")
    model_name = _normalized_config_value(
        getattr(request_config, "model_name", None)
        or getattr(request_config, "model", None)
    )
    namespace_values = (
        _normalized_config_value(getattr(client_config, "cache_namespace", None)),
        _normalized_config_value(getattr(client_config, "tenant_id", None)),
        _normalized_config_value(getattr(client_config, "router_identity", None)),
    )
    if not provider or not api_base or not model_name:
        # Missing routing metadata must not accidentally group two different
        # Model instances. Reusing the exact Model object remains safe.
        return ("model-object", str(id(model)))
    return (provider, api_base, model_name, *namespace_values)


def _normalized_config_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


async def execute_action_plan(
    plan: TeamKVCActionPlan,
    *,
    reason: str,
    action_concurrency: int = 8,
) -> list[bool]:
    """Execute a previously snapshotted Team plan with bounded request concurrency."""
    signal_key = plan.root_cache_id or "|".join(
        step.binding.identity.parent_cache_id for step in plan.steps
    )
    if plan.action == "evict":
        previous = _SIGNAL_TAILS.get(signal_key)
        if previous is not None:
            await asyncio.gather(asyncio.shield(previous), return_exceptions=True)
    sem = asyncio.Semaphore(max(1, action_concurrency))

    async def _run(step: TeamKVCActionStep) -> bool:
        async with sem:
            return await run_binding_action(
                step.binding,
                plan.action,
                member_id=",".join(step.member_ids),
                reason=reason,
            )

    return await asyncio.gather(*(_run(step) for step in plan.steps), return_exceptions=False)


_SIGNAL_TASKS: set[asyncio.Task[list[bool]]] = set()
_SIGNAL_TAILS: dict[str, asyncio.Task[list[bool]]] = {}


def dispatch_action_plan(plan: TeamKVCActionPlan, *, reason: str) -> bool:
    """Schedule offload/prefetch without making the product lifecycle wait."""
    if plan.action not in {"offload", "prefetch"} or not plan.steps:
        return False
    signal_key = plan.root_cache_id or "|".join(
        step.binding.identity.parent_cache_id for step in plan.steps
    )
    previous = _SIGNAL_TAILS.get(signal_key)

    async def _run_ordered() -> list[bool]:
        if previous is not None:
            await asyncio.gather(asyncio.shield(previous), return_exceptions=True)
        return await execute_action_plan(plan, reason=reason)

    task = asyncio.create_task(
        _run_ordered(),
        name=f"team-kvc-{plan.action}[{plan.root_cache_id or 'children'}]",
    )
    _SIGNAL_TASKS.add(task)
    _SIGNAL_TAILS[signal_key] = task

    def _consume_result(done: asyncio.Task[list[bool]]) -> None:
        _SIGNAL_TASKS.discard(done)
        if _SIGNAL_TAILS.get(signal_key) is done:
            _SIGNAL_TAILS.pop(signal_key, None)
        try:
            results = done.result()
            if not all(results):
                team_logger.warning(
                    "[TeamKVC] background action returned partial failure: action={} reason={} results={}",
                    plan.action,
                    reason,
                    results,
                )
        except asyncio.CancelledError:
            team_logger.debug(
                "[TeamKVC] background action cancelled: action={} reason={}",
                plan.action,
                reason,
            )
        except Exception as exc:
            team_logger.warning(
                "[TeamKVC] background action failed: action={} reason={} error={}",
                plan.action,
                reason,
                exc,
            )

    task.add_done_callback(_consume_result)
    return True


async def cancel_pending_signal_tasks() -> None:
    """Cancel and consume outstanding Team KVC signals during process shutdown/tests."""
    tasks = tuple(_SIGNAL_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)
    _SIGNAL_TAILS.clear()


async def run_binding_action(
    binding: KVCacheRuntimeBinding,
    action: str,
    *,
    member_id: str | None = None,
    reason: str,
    timeout: float | None = None,
) -> bool:
    """Run one provider action for a binding.

    ``reason`` is lifecycle-origin context for logs. It is deliberately kept
    out of the provider arguments and all action/identity decisions.
    """
    if not is_binding_manageable(binding):
        return False
    identity = binding.identity
    kwargs = {
        "session_id": identity.cache_id,
        "parent_session_id": identity.parent_cache_id,
        "enabled": binding.enabled,
        "timeout": timeout,
    }
    if action == "offload":
        ok = await offload_session_kv_cache(binding.model, **kwargs)
    elif action == "prefetch":
        ok = await prefetch_session_kv_cache(binding.model, **kwargs)
    elif action == "evict":
        ok = await evict_session_kv_cache(binding.model, **kwargs)
    else:
        raise ValueError(f"Unsupported KVC action: {action}")
    team_logger.info(
        "[TeamKVC] action={} reason={} member_id={} cache_id={} parent_cache_id={} result={}",
        action,
        reason,
        member_id,
        identity.cache_id,
        identity.parent_cache_id,
        ok,
    )
    return ok


def build_runtime_binding(runtime: Any, identity: KVCacheIdentity) -> KVCacheRuntimeBinding | None:
    model = getattr(runtime, "model", None)
    deep_config = getattr(runtime, "deep_config", None)
    kv_config = getattr(deep_config, "kv_cache_affinity_config", None)
    binding = KVCacheRuntimeBinding(
        identity=identity,
        model=model,
        enabled=bool(getattr(kv_config, "enable_kv_cache_affinity", False)),
    )
    return binding if is_binding_manageable(binding) else None


async def register_harness_binding(
    registry: TeamKVCacheRegistry | None,
    *,
    member_id: str,
    member_name: str | None,
    harness: Any,
    is_leader: bool = False,
) -> TeamKVCRecord | None:
    if registry is None or harness is None:
        return None
    current_session = getattr(harness, "current_session", None)
    session = current_session() if callable(current_session) else None
    identity_fn = getattr(session, "get_cache_identity", None)
    if callable(identity_fn):
        identity = identity_fn()
    else:
        # Compatibility for third-party harnesses implementing the previous
        # identity accessor; TeamHarness itself now exposes only its Session.
        legacy_identity_fn = getattr(harness, "current_kv_cache_identity", None)
        identity = legacy_identity_fn() if callable(legacy_identity_fn) else None
    if identity is None:
        team_logger.debug("[TeamKVC] skip binding registration: missing identity member_id={}", member_id)
        return None
    binding = build_runtime_binding(harness, identity)
    if binding is None:
        return None
    return await registry.register_or_update(
        member_id=member_id,
        member_name=member_name,
        binding=binding,
        is_leader=is_leader,
    )


async def cancellation_safe_best_effort_evict(
    binding: KVCacheRuntimeBinding | None,
    *,
    reason: str,
    worker_id: str,
) -> bool:
    if binding is None:
        return False
    try:
        return await run_binding_action(binding, "evict", member_id=worker_id, reason=reason)
    except Exception as exc:
        team_logger.warning(
            "[TeamKVC] worker evict failed: worker_id={} cache_id={} error={}",
            worker_id,
            binding.identity.cache_id,
            exc,
        )
        return False
