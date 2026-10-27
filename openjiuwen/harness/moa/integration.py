# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MoA startup and owned-resource lifecycle at the DeepAgent boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from openjiuwen.core.single_agent.rail.base import AgentRail, init_rail
from openjiuwen.harness.rails.moa_rail import MoARail
from openjiuwen.harness.moa.state import (
    InputOrigin,
    ProposalStore,
    active_moa_input_origin,
    active_moa_work_binding,
    active_proposal_store,
)


_SCOPE_KEY = "_moa_proposal_scope"
_ORIGIN_KEY = "_moa_input_origin"
_WORK_KEY = "_moa_owner_work"


@dataclass
class _ProposalScope:
    session_id: str
    store: ProposalStore
    current_work: str | None = None


@dataclass(frozen=True)
class _WorkBinding:
    identity: str
    scope_id: str
    session_id: str
    store: ProposalStore
    origin: InputOrigin


class MoAProposalOwner:
    """Own only private advice across the works belonging to one user task.

    Opaque scope and work IDs travel through trusted scheduling metadata. Stores
    and callback/context objects never enter task metadata or session state.
    """

    def __init__(self) -> None:
        self._scopes: dict[str, _ProposalScope] = {}
        self._bindings: dict[str, _WorkBinding] = {}
        self._suspended: dict[str, str] = {}

    def begin_work(
        self,
        session_id: str,
        *,
        work: Any = None,
        interaction_response: bool = False,
    ) -> _WorkBinding:
        """Bind a fresh work state to its task's existing or new advice store."""
        context = work.context if work is not None else {}
        scope_id = context.get(_SCOPE_KEY)
        automatic = work is not None and (
            work.kind == "goal" or context.get(_ORIGIN_KEY) == "internal"
        )
        origin: InputOrigin = "internal" if scope_id is not None or automatic else "user_request"
        if interaction_response:
            origin = "interaction_response"
            scope_id = self._suspended.pop(session_id, None)
        if scope_id is None:
            if origin == "user_request":
                self.close_session(session_id, "new_user_request")
            scope_id = uuid4().hex
            self._scopes[scope_id] = _ProposalScope(session_id, ProposalStore())
        scope = self._scopes.get(scope_id)
        if scope is None or scope.session_id != session_id:
            # A discarded or cancelled continuation cannot resurrect its task.
            store = ProposalStore()
            store.close("retired_scope")
        else:
            store = scope.store
        if origin == "interaction_response" and not store.closed:
            store.invalidate("interaction_response", clear=False)
        if work is not None:
            context[_SCOPE_KEY] = scope_id
            context[_ORIGIN_KEY] = origin
        binding = _WorkBinding(uuid4().hex, scope_id, session_id, store, origin)
        self._bindings[binding.identity] = binding
        if scope is not None and scope.session_id == session_id:
            scope.current_work = binding.identity
        if work is not None:
            context[_WORK_KEY] = binding.identity
        return binding

    @staticmethod
    def inherit_work(parent: Any, child: Any) -> None:
        """Carry task ownership only for a continuation created by the owner."""
        scope_id = parent.context.get(_SCOPE_KEY)
        if scope_id is not None:
            child.context[_SCOPE_KEY] = scope_id
            child.context[_ORIGIN_KEY] = "internal"

    @staticmethod
    def mark_automatic(work: Any) -> None:
        """Mark autonomous work without attaching it to a previous user task."""
        work.context[_ORIGIN_KEY] = "internal"

    def resolve(self, identity: str | None) -> _WorkBinding | None:
        return self._bindings.get(identity) if identity is not None else None

    def activate(self, identity: str, *, internal: bool = False) -> tuple[Any, Any, Any]:
        """Install an atomic work binding in this task's copied context."""
        binding = self._bindings[identity]
        origin = "internal" if internal else binding.origin
        return (
            active_proposal_store.set(binding.store),
            active_moa_work_binding.set(binding.identity),
            active_moa_input_origin.set(origin),
        )

    @staticmethod
    def reset(tokens: tuple[Any, Any, Any]) -> None:
        active_moa_input_origin.reset(tokens[2])
        active_moa_work_binding.reset(tokens[1])
        active_proposal_store.reset(tokens[0])

    def finish_work(
        self,
        identity: str,
        *,
        result: dict[str, Any] | None,
        succeeded: bool,
        continuing: bool = False,
    ) -> None:
        """Release work references; retain advice only for confirmed live work."""
        binding = self._bindings.pop(identity, None)
        if binding is None:
            return
        scope = self._scopes.get(binding.scope_id)
        if scope is None or scope.current_work != identity:
            return
        if not succeeded or result is None or result.get("result_type") == "error":
            self.close_scope(binding.scope_id, "work_failed")
        elif result.get("result_type") == "interrupt":
            self._suspend(binding)
        elif not continuing:
            self.close_scope(binding.scope_id, "request_completed")

    def suspend_work(self, identity: str) -> None:
        """Bind an emitted interaction before a caller can immediately reply."""
        binding = self._bindings.get(identity)
        if binding is not None:
            self._suspend(binding)

    def _suspend(self, binding: _WorkBinding) -> None:
        scope = self._scopes.get(binding.scope_id)
        if scope is None or scope.current_work != binding.identity or binding.store.closed:
            return
        if self._suspended.get(binding.session_id) != binding.scope_id:
            binding.store.invalidate("interaction_suspended", clear=False)
            self._suspended[binding.session_id] = binding.scope_id

    def close_scope(self, scope_id: str, reason: str) -> None:
        scope = self._scopes.pop(scope_id, None)
        if scope is not None:
            scope.store.close(reason)
            if self._suspended.get(scope.session_id) == scope_id:
                self._suspended.pop(scope.session_id, None)
        for identity, binding in list(self._bindings.items()):
            if binding.scope_id == scope_id:
                self._bindings.pop(identity, None)
        # In-flight ContextVars still reference the closed, empty store. The
        # registry need not retain them while cancellation settles.

    def close_work(self, work: Any, reason: str) -> None:
        scope_id = work.context.get(_SCOPE_KEY)
        if isinstance(scope_id, str):
            scope = self._scopes.get(scope_id)
            identity = work.context.get(_WORK_KEY)
            if scope is not None and identity is not None and scope.current_work != identity:
                return
            self.close_scope(scope_id, reason)

    def is_suspended(self, work: Any, session_id: str) -> bool:
        scope_id = work.context.get(_SCOPE_KEY)
        return scope_id is not None and self._suspended.get(session_id) == scope_id

    def close_session(self, session_id: str, reason: str) -> None:
        for scope_id, scope in list(self._scopes.items()):
            if scope.session_id == session_id:
                self.close_scope(scope_id, reason)

    def discard_queued(self, session_id: str, reason: str, *, active_work: Any = None) -> None:
        """Retire discarded continuations without cancelling a live round/pause."""
        retained = {self._suspended.get(session_id)}
        if active_work is not None:
            retained.add(active_work.context.get(_SCOPE_KEY))
        for scope_id, scope in list(self._scopes.items()):
            if scope.session_id == session_id and scope_id not in retained:
                self.close_scope(scope_id, reason)

    def close_all(self, reason: str) -> None:
        for scope_id in list(self._scopes):
            self.close_scope(scope_id, reason)
        self._bindings.clear()


class MoAIntegration:
    """Keep MoA-specific startup policy out of the host's rail registration loop."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.rails = list({id(rail): rail for rail in agent.find_rails_by_type((MoARail,))}.values())

    def prepare(self) -> None:
        """Select the native loader after every factory rail has been queued."""
        for rail in self.rails:
            rail.prepare_skill_loading(self.agent)

    async def finalize(self, initialized_rails: list[AgentRail]) -> None:
        """Refresh bindings on retained rails and validate before the first request."""
        for rail in self.rails:
            if rail not in initialized_rails:
                init_rail(rail, self.agent)
            await rail.validate_skill_loading(refresh=True)

    async def rollback(self, initializing_rail: AgentRail | None) -> None:
        """Withdraw MoA resources while preserving successfully registered user rails."""
        agent = self.agent
        if not self.rails:
            return
        owned = [item for rail in self.rails for item in (rail, rail.owned_skill_rail)]
        if initializing_rail is not None:
            await agent.unregister_rail(initializing_rail)
            if initializing_rail not in owned:
                agent.add_rail(initializing_rail)
        for rail in self.rails:
            await agent.unregister_rail(rail)
            agent.add_rail(rail)
        # Successful user callbacks are already live; replaying their pending
        # entries on retry would register the same callback a second time.
        for rail in agent.configured_rails():
            if agent.is_registered_rail(rail):
                agent.remove_pending_rail(rail)

    @staticmethod
    async def retire_companions(agent: Any, rail: AgentRail) -> None:
        """Retire only the native loader created by this MoA instance."""
        if isinstance(rail, MoARail):
            owner = getattr(agent, "_moa_scope_owner", None)
            if owner is not None:
                owner.close_all("rail_retired")
            if rail.owned_skill_rail is not None:
                await agent.unregister_rail(rail.owned_skill_rail)
