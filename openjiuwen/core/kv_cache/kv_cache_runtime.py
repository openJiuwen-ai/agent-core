# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterable
from collections.abc import Callable
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.kv_cache.kv_cache_runtime_config import KVCacheRuntimeConfig
from openjiuwen.core.kv_cache.kv_cache_types import (
    ActionKey,
    ActionKind,
    ActionScope,
    ActionState,
    Admission,
    BindingKey,
    BindingState,
    InferenceLease,
    KVCacheBinding,
    KVCacheControlDomain,
    KVCacheIdentity,
    PendingAction,
    Residency,
    RootKey,
)


class KVCacheRuntime:
    """Application-scoped executor for Session-level KV-cache actions."""

    def __init__(
        self,
        config: KVCacheRuntimeConfig | None = None,
        *,
        binding_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._config = config or KVCacheRuntimeConfig()
        self._binding_provider = binding_provider
        self._bindings: dict[BindingKey, BindingState] = {}
        self._root_index: dict[RootKey, set[BindingKey]] = {}
        self._action_states: dict[ActionKey, ActionState] = {}
        self._tasks: set[asyncio.Task[bool]] = set()
        self._condition = asyncio.Condition()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def register_binding(
        self,
        identity: KVCacheIdentity,
        model: Any,
        *,
        model_name: str | None = None,
    ) -> KVCacheBinding | None:
        return await self._register_binding(identity, model, model_name=model_name)

    async def _register_binding(
        self,
        identity: KVCacheIdentity,
        model: Any,
        *,
        model_name: str | None = None,
        fallback: bool = False,
    ) -> KVCacheBinding | None:
        if model is None or not self._valid_identity(identity):
            return None
        domain = self._control_domain(model, model_name)
        binding = KVCacheBinding(identity=identity, model=model, control_domain=domain)
        binding_key = BindingKey(identity.cache_id, domain)
        root_key = RootKey(identity.parent_cache_id, domain)
        async with self._condition:
            if self._closed:
                return None
            fallback_key = BindingKey(identity.parent_cache_id, domain)
            fallback_state = self._bindings.get(fallback_key)
            has_replaceable_fallback = fallback_state is not None and fallback_state.fallback
            if not fallback and fallback_key != binding_key and has_replaceable_fallback:
                self._remove_binding_locked(fallback_key, preserve_root_state=True)
            previous = self._bindings.get(binding_key)
            if previous is not None:
                previous_root = RootKey(
                    previous.binding.identity.parent_cache_id,
                    domain,
                )
                if previous_root != root_key:
                    indexed = self._root_index.get(previous_root)
                    if indexed is not None:
                        indexed.discard(binding_key)
                        if not indexed:
                            self._root_index.pop(previous_root, None)
                            self._action_states.pop(
                                self._root_action_key(previous_root),
                                None,
                            )
            revision = 0 if previous is None else previous.revision + 1
            self._bindings[binding_key] = BindingState(
                binding=binding,
                residency=previous.residency if previous is not None else Residency.UNKNOWN,
                revision=revision,
                fallback=fallback,
            )
            self._root_index.setdefault(root_key, set()).add(binding_key)
            self._state(self._binding_action_key(binding_key))
            self._state(self._root_action_key(root_key))
        return binding

    async def begin_inference(
        self,
        identity: KVCacheIdentity,
        model: Any,
        *,
        model_name: str | None = None,
    ) -> InferenceLease | None:
        binding = await self.register_binding(identity, model, model_name=model_name)
        if binding is None:
            return None
        child_key = ActionKey(ActionScope.BINDING, identity.cache_id, binding.control_domain)
        root_key = ActionKey(ActionScope.ROOT, identity.parent_cache_id, binding.control_domain)

        for _ in range(2):
            async with self._condition:
                if self._closed:
                    return None
                child_state = self._state(child_key)
                root_state = self._state(root_key)
                if child_state.admission is Admission.TERMINAL or root_state.admission is Admission.TERMINAL:
                    return None
                blocked = [
                    key
                    for key, state in ((root_key, root_state), (child_key, child_state))
                    if state.admission is Admission.BLOCKED
                ]
                if not blocked:
                    child_state.active_inference_count += 1
                    root_state.active_inference_count += 1
                    return InferenceLease(child_key=child_key, root_key=root_key)

            prepared = True
            for key in blocked:
                prepared = await self._prepare_key(key) and prepared
            if not prepared:
                await self._fail_open(blocked)

        await self._fail_open((root_key, child_key))
        async with self._condition:
            self._state(child_key).active_inference_count += 1
            self._state(root_key).active_inference_count += 1
        return InferenceLease(child_key=child_key, root_key=root_key)

    async def end_inference(
        self,
        lease: InferenceLease | None,
        *,
        succeeded: bool,
    ) -> None:
        if lease is None or lease.released:
            return
        lease.released = True
        async with self._condition:
            for key in (lease.child_key, lease.root_key):
                state = self._action_states.get(key)
                if state is None:
                    continue
                state.active_inference_count = max(0, state.active_inference_count - 1)
            binding_key = BindingKey(
                lease.child_key.cache_id,
                lease.child_key.control_domain,
            )
            binding = self._bindings.get(binding_key)
            if binding is not None:
                binding.residency = Residency.RESIDENT if succeeded else Residency.UNKNOWN
            self._condition.notify_all()

    async def prepare(self, identity: KVCacheIdentity) -> bool:
        keys = await self._identity_action_keys(identity)
        if not keys:
            return False
        keys = await self._prepare_action_keys(keys)
        results = [await self._prepare_key(key) for key in keys]
        return any(results)

    async def suspend(self, identity: KVCacheIdentity) -> bool:
        keys = await self._identity_action_keys(identity)
        if not keys:
            return False
        scheduled = False
        for key in keys:
            async with self._condition:
                state = self._state(key)
                if state.admission is Admission.TERMINAL:
                    continue
                if state.fail_open:
                    state.admission = Admission.OPEN
                    self._condition.notify_all()
                    continue
                bindings = self._bindings_for_action_locked(key)
                already_offloaded = bool(bindings) and all(
                    binding.residency is Residency.OFFLOADED
                    for binding in bindings
                )
                if (
                    state.admission is Admission.BLOCKED
                    and state.pending_action is None
                    and already_offloaded
                ):
                    continue
                state.admission = Admission.BLOCKED
                pending = self._enqueue_locked(key, ActionKind.OFFLOAD)
                scheduled = pending is not None or scheduled
        return scheduled

    async def release(self, identity: KVCacheIdentity) -> bool:
        keys = await self._identity_action_keys(identity)
        actions: list[asyncio.Task[bool]] = []
        for key in keys:
            async with self._condition:
                state = self._state(key)
                state.admission = Admission.TERMINAL
                pending = self._enqueue_locked(key, ActionKind.EVICT)
                if pending is not None:
                    actions.append(pending.task)

        succeeded = False
        if actions:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*actions, return_exceptions=True),
                    timeout=self._config.action_timeout + self._config.evict_timeout,
                )
                succeeded = any(result is True for result in results)
            except asyncio.TimeoutError:
                logger.warning(
                    "[KVCacheRuntime] Session evict timed out: cache_id=%s",
                    identity.cache_id,
                )
        await self._forget(identity)
        return succeeded

    async def close(self) -> None:
        async with self._condition:
            if self._closed:
                return
            self._closed = True
            tasks = tuple(self._tasks)
            bindings = tuple(state.binding for state in self._bindings.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        roots: dict[tuple[str, KVCacheControlDomain], KVCacheBinding] = {}
        for binding in bindings:
            roots.setdefault(
                (binding.identity.parent_cache_id, binding.control_domain),
                binding,
            )
        evictions = [
            self._call_model(
                binding,
                ActionKind.EVICT,
                cache_id=parent_cache_id,
                parent_cache_id=parent_cache_id,
            )
            for (parent_cache_id, _), binding in roots.items()
        ]
        if evictions:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*evictions, return_exceptions=True),
                    timeout=self._config.close_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("[KVCacheRuntime] bounded shutdown eviction timed out")
        async with self._condition:
            self._bindings.clear()
            self._root_index.clear()
            self._action_states.clear()
            self._tasks.clear()

    async def _prepare_key(self, key: ActionKey) -> bool:
        started_offload: asyncio.Task[bool] | None = None
        async with self._condition:
            if self._closed:
                return False
            state = self._state(key)
            if state.admission is Admission.TERMINAL:
                return False
            pending = state.pending_action
            if (
                pending is not None
                and pending.kind is ActionKind.OFFLOAD
                and not pending.provider_call_started.is_set()
            ):
                pending.task.cancel()
                state.admission = Admission.OPEN
                return True
            if pending is not None and pending.kind is ActionKind.OFFLOAD:
                started_offload = pending.task

        if started_offload is not None:
            await self._finish_or_cancel(started_offload)

        async with self._condition:
            if self._closed:
                return False
            state = self._state(key)
            if state.admission is Admission.TERMINAL:
                return False
            pending = state.pending_action
            bindings = self._bindings_for_action_locked(key)
            needs_prefetch = (
                started_offload is not None
                or (pending is not None and pending.kind is ActionKind.OFFLOAD)
                or self._related_scope_is_blocked_locked(key)
                or any(item.residency is not Residency.RESIDENT for item in bindings)
            )
            if not needs_prefetch:
                state.admission = Admission.OPEN
                return True
            action = self._enqueue_locked(key, ActionKind.PREFETCH)
            if action is None:
                state.admission = Admission.OPEN
                return False

        return await self._wait_until_provider_call_started(action)

    async def _finish_or_cancel(self, task: asyncio.Task[bool]) -> None:
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._config.action_timeout,
            )
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        except Exception as exc:
            logger.warning(
                "[KVCacheRuntime] failed to finish pending action: error=%s",
                exc,
            )

    async def _wait_until_provider_call_started(self, action: PendingAction) -> bool:
        waiter = asyncio.create_task(action.provider_call_started.wait())
        try:
            done, _ = await asyncio.wait(
                (waiter, action.task),
                timeout=self._config.action_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if waiter in done and action.provider_call_started.is_set():
                return True
            if action.task in done:
                return action.task.result()
            return False
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            return False
        except Exception as exc:
            logger.warning("[KVCacheRuntime] prefetch admission failed: %s", exc)
            return False
        finally:
            if not waiter.done():
                waiter.cancel()

    def _enqueue_locked(
        self,
        key: ActionKey,
        kind: ActionKind,
    ) -> PendingAction | None:
        state = self._state(key)
        current = state.pending_action
        if current is not None and not current.task.done() and current.kind is kind:
            return current
        previous = self._action_dependencies_locked(key)
        provider_call_started = asyncio.Event()
        task = asyncio.create_task(
            self._run_action(key, kind, previous, provider_call_started),
            name=f"kvc-{kind.value}[{key.cache_id}]",
        )
        pending = PendingAction(
            kind=kind,
            task=task,
            provider_call_started=provider_call_started,
        )
        state.pending_action = pending
        state.action_tail = task
        self._tasks.add(task)
        task.add_done_callback(self._consume_task)
        return pending

    async def _run_action(
        self,
        key: ActionKey,
        kind: ActionKind,
        previous: tuple[asyncio.Task[bool], ...],
        provider_call_started: asyncio.Event,
    ) -> bool:
        try:
            if previous:
                await asyncio.gather(
                    *(asyncio.shield(task) for task in previous),
                    return_exceptions=True,
                )
            if kind in (ActionKind.OFFLOAD, ActionKind.EVICT):
                await asyncio.wait_for(
                    self._wait_for_idle(key),
                    timeout=(self._config.evict_timeout if kind is ActionKind.EVICT else self._config.action_timeout),
                )

            async with self._condition:
                bindings = self._bindings_for_action_locked(key)
                snapshots = [(item.binding, item.revision) for item in bindings]
            if not snapshots:
                return False

            binding = snapshots[0][0]
            cache_id = key.cache_id
            parent_cache_id = key.cache_id if key.scope is ActionScope.ROOT else binding.identity.parent_cache_id
            succeeded = await asyncio.wait_for(
                self._call_model(
                    binding,
                    kind,
                    cache_id=cache_id,
                    parent_cache_id=parent_cache_id,
                    provider_call_started=provider_call_started,
                    action_key=key,
                ),
                timeout=(self._config.evict_timeout if kind is ActionKind.EVICT else self._config.action_timeout),
            )
            await self._record_result(key, kind, snapshots, succeeded)
            return succeeded
        except Exception as exc:
            logger.warning(
                "[KVCacheRuntime] action failed: action=%s cache_id=%s error=%s",
                kind.value,
                key.cache_id,
                exc,
            )
            await self._record_result(key, kind, (), False)
            return False
        finally:
            async with self._condition:
                state = self._action_states.get(key)
                if state is not None:
                    pending = state.pending_action
                    is_current = pending is not None and pending.task is asyncio.current_task()
                    if is_current:
                        state.pending_action = None
                    if is_current and kind is ActionKind.PREFETCH and state.admission is Admission.BLOCKED:
                        self._open_prefetched_scope_locked(key)
                    self._condition.notify_all()

    async def _record_result(
        self,
        key: ActionKey,
        kind: ActionKind,
        snapshots: Iterable[tuple[KVCacheBinding, int]],
        succeeded: bool,
    ) -> None:
        target = (
            Residency.OFFLOADED
            if succeeded and kind is ActionKind.OFFLOAD
            else Residency.RESIDENT
            if succeeded and kind is ActionKind.PREFETCH
            else Residency.UNKNOWN
        )
        async with self._condition:
            for binding, revision in snapshots:
                binding_key = BindingKey(binding.identity.cache_id, binding.control_domain)
                current = self._bindings.get(binding_key)
                if current is not None and current.revision == revision:
                    current.residency = target
            state = self._action_states.get(key)
            if state is not None and not succeeded:
                state.fail_open = True

    async def _call_model(
        self,
        binding: KVCacheBinding,
        kind: ActionKind,
        *,
        cache_id: str,
        parent_cache_id: str,
        provider_call_started: asyncio.Event | None = None,
        action_key: ActionKey | None = None,
    ) -> bool:
        method = getattr(binding.model, f"{kind.value}_kvc", None)
        if not callable(method):
            return False
        if provider_call_started is not None:
            async with self._condition:
                if kind is ActionKind.PREFETCH and action_key is not None:
                    self._open_prefetched_scope_locked(action_key)
                provider_call_started.set()
        return bool(
            await method(
                session_id=cache_id,
                parent_session_id=parent_cache_id,
                target="session",
                model=binding.control_domain.model_name or None,
            )
        )

    async def _wait_for_idle(self, key: ActionKey) -> None:
        async with self._condition:
            await self._condition.wait_for(
                lambda: (
                    self._closed
                    or self._action_states.get(key) is None
                    or self._action_states[key].active_inference_count == 0
                )
            )

    async def _fail_open(self, keys: Iterable[ActionKey]) -> None:
        async with self._condition:
            for key in keys:
                state = self._state(key)
                if state.admission is not Admission.TERMINAL:
                    state.admission = Admission.OPEN
                    state.fail_open = True
            self._condition.notify_all()

    async def _identity_action_keys(self, identity: KVCacheIdentity) -> list[ActionKey]:
        if not self._valid_identity(identity):
            return []
        keys = await self._registered_action_keys(identity)
        if keys or self._binding_provider is None:
            return keys
        try:
            model = self._binding_provider()
            if inspect.isawaitable(model):
                model = await model
            if model is not None:
                await self._register_binding(identity, model, fallback=True)
        except Exception as exc:
            logger.warning(
                "[KVCacheRuntime] fallback binding unavailable: cache_id=%s error=%s",
                identity.cache_id,
                exc,
            )
        return await self._registered_action_keys(identity)

    async def _registered_action_keys(self, identity: KVCacheIdentity) -> list[ActionKey]:
        async with self._condition:
            if self._closed:
                return []
            if identity.cache_id == identity.parent_cache_id:
                return [
                    self._root_action_key(root_key)
                    for root_key in self._root_index
                    if root_key.parent_cache_id == identity.parent_cache_id
                ]
            return [
                self._binding_action_key(binding_key)
                for binding_key in self._bindings
                if binding_key.cache_id == identity.cache_id
            ]

    async def _prepare_action_keys(self, keys: list[ActionKey]) -> list[ActionKey]:
        async with self._condition:
            roots: list[ActionKey] = []
            for key in keys:
                if key.scope is not ActionScope.BINDING:
                    continue
                binding = self._bindings.get(BindingKey(key.cache_id, key.control_domain))
                if binding is None:
                    continue
                root_key = self._root_action_key(
                    RootKey(
                        binding.binding.identity.parent_cache_id,
                        key.control_domain,
                    )
                )
                root_state = self._action_states.get(root_key)
                if root_state is not None and root_state.admission is Admission.BLOCKED and root_key not in roots:
                    roots.append(root_key)
            return roots + keys

    async def _forget(self, identity: KVCacheIdentity) -> None:
        async with self._condition:
            if identity.cache_id == identity.parent_cache_id:
                binding_keys = []
                for root_key, indexed_keys in self._root_index.items():
                    if root_key.parent_cache_id == identity.parent_cache_id:
                        binding_keys.extend(indexed_keys)
            else:
                binding_keys = [key for key in self._bindings if key.cache_id == identity.cache_id]
            for binding_key in binding_keys:
                self._remove_binding_locked(binding_key)
            self._condition.notify_all()

    def _remove_binding_locked(
        self,
        binding_key: BindingKey,
        *,
        preserve_root_state: bool = False,
    ) -> None:
        binding = self._bindings.pop(binding_key, None)
        if binding is None:
            return
        root_key = RootKey(
            binding.binding.identity.parent_cache_id,
            binding_key.control_domain,
        )
        indexed = self._root_index.get(root_key)
        if indexed is not None:
            indexed.discard(binding_key)
            if not indexed:
                self._root_index.pop(root_key, None)
                if not preserve_root_state:
                    self._action_states.pop(self._root_action_key(root_key), None)
        self._action_states.pop(self._binding_action_key(binding_key), None)

    def _action_dependencies_locked(self, key: ActionKey) -> tuple[asyncio.Task[bool], ...]:
        dependencies: list[asyncio.Task[bool]] = []

        def add(state: ActionState | None) -> None:
            if state is not None and state.action_tail is not None and state.action_tail not in dependencies:
                dependencies.append(state.action_tail)

        add(self._action_states.get(key))
        if key.scope is ActionScope.ROOT:
            root_key = RootKey(key.cache_id, key.control_domain)
            for binding_key in self._root_index.get(root_key, set()):
                add(self._action_states.get(self._binding_action_key(binding_key)))
        else:
            binding = self._bindings.get(BindingKey(key.cache_id, key.control_domain))
            if binding is not None:
                root_key = RootKey(
                    binding.binding.identity.parent_cache_id,
                    key.control_domain,
                )
                add(self._action_states.get(self._root_action_key(root_key)))
        return tuple(dependencies)

    def _related_scope_is_blocked_locked(self, key: ActionKey) -> bool:
        if key.scope is ActionScope.ROOT:
            root_key = RootKey(key.cache_id, key.control_domain)
            for binding_key in self._root_index.get(root_key, set()):
                state = self._action_states.get(self._binding_action_key(binding_key))
                if state is not None and state.admission is Admission.BLOCKED:
                    return True
            return False
        binding = self._bindings.get(BindingKey(key.cache_id, key.control_domain))
        if binding is None:
            return False
        root_key = RootKey(
            binding.binding.identity.parent_cache_id,
            key.control_domain,
        )
        root_state = self._action_states.get(self._root_action_key(root_key))
        return root_state is not None and root_state.admission is Admission.BLOCKED

    def _open_prefetched_scope_locked(self, key: ActionKey) -> None:
        keys = [key]
        if key.scope is ActionScope.ROOT:
            root_key = RootKey(key.cache_id, key.control_domain)
            keys.extend(self._binding_action_key(binding_key) for binding_key in self._root_index.get(root_key, set()))
        for action_key in keys:
            state = self._action_states.get(action_key)
            if state is not None and state.admission is not Admission.TERMINAL:
                state.admission = Admission.OPEN
        self._condition.notify_all()

    def _bindings_for_action_locked(self, key: ActionKey) -> list[BindingState]:
        if key.scope is ActionScope.BINDING:
            state = self._bindings.get(BindingKey(key.cache_id, key.control_domain))
            return [] if state is None else [state]
        root_key = RootKey(key.cache_id, key.control_domain)
        return [
            self._bindings[binding_key]
            for binding_key in self._root_index.get(root_key, set())
            if binding_key in self._bindings
        ]

    def _state(self, key: ActionKey) -> ActionState:
        return self._action_states.setdefault(key, ActionState())

    def _consume_task(self, task: asyncio.Task[bool]) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive callback boundary
            logger.warning("[KVCacheRuntime] background action raised: %s", exc)

    @staticmethod
    def _binding_action_key(key: BindingKey) -> ActionKey:
        return ActionKey(ActionScope.BINDING, key.cache_id, key.control_domain)

    @staticmethod
    def _root_action_key(key: RootKey) -> ActionKey:
        return ActionKey(ActionScope.ROOT, key.parent_cache_id, key.control_domain)

    @staticmethod
    def _valid_identity(identity: KVCacheIdentity | None) -> bool:
        return bool(
            identity is not None
            and str(identity.cache_id or "").strip()
            and str(identity.parent_cache_id or "").strip()
        )

    @staticmethod
    def _control_domain(model: Any, model_name: str | None) -> KVCacheControlDomain:
        client_config = getattr(model, "model_client_config", None)
        request_config = getattr(model, "model_config", None)
        provider = getattr(client_config, "client_provider", "")
        provider = getattr(provider, "value", provider)
        resolved_model = model_name or getattr(request_config, "model_name", "") or ""
        extensions = getattr(client_config, "extensions", None)
        kv_cache = getattr(extensions, "kv_cache", None)
        namespace = getattr(kv_cache, "cache_namespace", "") if kv_cache is not None else ""
        return KVCacheControlDomain(
            provider=str(provider or ""),
            api_base=str(getattr(client_config, "api_base", "") or "").rstrip("/"),
            model_name=str(resolved_model),
            cache_namespace=str(namespace or ""),
        )


__all__ = ["KVCacheRuntime"]
