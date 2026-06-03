# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Adapter wiring a VersioningManager to a real Session + ContextEngine.

``for_session`` builds the three manager callbacks without modifying any
existing code:

- snapshot_provider: reads the live snapshot = encoded context (via
  ``ContextEngine.save_contexts``) plus the agent state with the ``context``
  key stripped (it is owned by the context track, not the state track).
- applier: writes a ``{context, state}`` snapshot back — context via each
  context's ``load_state``, state via ``state().set_state``.
- forker: clones a new Session through the public ``create_agent_session``,
  seeds it, and returns a fresh VersioningManager bound to the new session_id.
"""
import uuid
from copy import deepcopy
from typing import Callable

from openjiuwen.core.session.vcs import constants as const
from openjiuwen.core.session.vcs.backend import VersioningBackend
from openjiuwen.core.session.vcs.codec import decode_context_state, encode_context_state
from openjiuwen.core.session.vcs.config import VersioningConfig, build_backend
from openjiuwen.core.session.vcs.manager import VersioningManager
from openjiuwen.core.session.vcs.models import ForkResult, Head, Snapshot


def _strip_context(full_state: dict) -> dict:
    """Return the agent state with ``global_state.context`` removed.

    ``get_state()`` already returns a deep copy, so mutating it is safe.
    """
    global_state = full_state.get(const.GLOBAL_STATE_KEY)
    if isinstance(global_state, dict):
        global_state.pop(const.CONTEXT_KEY, None)
    return full_state


async def _snapshot(session, context_engine) -> dict:
    """Build the live ``{context, state}`` snapshot for `session`."""
    states = await context_engine.save_contexts(session)
    context = {cid: encode_context_state(cstate) for cid, cstate in (states or {}).items()}
    state = _strip_context(session._inner.state().get_state())
    return {const.CONTEXT_KEY: context, const.STATE_KEY: state}


async def _apply(session, context_engine, snapshot: dict) -> None:
    """Apply a ``{context, state}`` snapshot back onto the live `session`."""
    session_id = session.get_session_id()
    for cid, cstate in snapshot.get(const.CONTEXT_KEY, {}).items():
        context = context_engine.get_context(cid, session_id)
        if context is None:
            context = await context_engine.create_context(cid, session)
        context.load_state({cid: decode_context_state(cstate)})
    session._inner.state().set_state(deepcopy(snapshot.get(const.STATE_KEY, {})))


def for_session(
    session,
    context_engine,
    *,
    backend_factory: Callable[[str], VersioningBackend] | None = None,
    config: VersioningConfig | None = None,
    kv_store=None,
) -> VersioningManager:
    """Build a VersioningManager bound to `session`'s state + context.

    Args:
        session: An ``openjiuwen.core.session.agent.Session``.
        context_engine: The ``ContextEngine`` owning this session's contexts.
        backend_factory: Optional ``session_id -> VersioningBackend`` factory;
            defaults to ``build_backend`` driven by ``config`` (+ ``kv_store``).
        config: vcs configuration; a default is used when omitted.
        kv_store: A ``BaseKVStore`` instance, required for the kv backend when
            no explicit ``backend_factory`` is given.

    Returns:
        A VersioningManager whose fork produces brand-new sessions of the same
        kind, seeded from the chosen history point.
    """
    config = config or VersioningConfig()
    if backend_factory is None:
        def backend_factory(sid: str) -> VersioningBackend:
            return build_backend(sid, config, kv_store=kv_store)

    return _build_manager(session, context_engine, backend_factory, config)


def _build_manager(session, context_engine, backend_factory, config) -> VersioningManager:
    session_id = session.get_session_id()

    async def provider() -> dict:
        return await _snapshot(session, context_engine)

    async def applier(snapshot: dict) -> None:
        await _apply(session, context_engine, snapshot)

    async def forker(new_id: str, seed: dict, forked_from: tuple[str, str]) -> ForkResult:
        return await _fork_session(session, context_engine, backend_factory, config, new_id, seed, forked_from)

    return VersioningManager(
        session_id,
        backend_factory(session_id),
        snapshot_provider=provider,
        applier=applier,
        forker=forker,
        snapshot_every=config.snapshot_every,
    )


async def _fork_session(source_session, context_engine, backend_factory, config, new_id, seed, forked_from) -> ForkResult:
    """Clone a new Session seeded from `seed` and bind a fresh VersioningManager."""
    from openjiuwen.core.session.agent import create_agent_session

    new_session = create_agent_session(
        session_id=new_id,
        envs=source_session.get_envs(),
        card=source_session._card,
    )
    await _apply(new_session, context_engine, seed)

    new_backend = backend_factory(new_id)
    await new_backend.put_snapshot(
        Snapshot(
            snapshot_id=uuid.uuid4().hex,
            event_id_high=0,
            context=deepcopy(seed.get(const.CONTEXT_KEY, {})),
            state=deepcopy(seed.get(const.STATE_KEY, {})),
        ),
    )
    await new_backend.put_head(Head(event_id=0, forked_from=forked_from))

    new_vc = _build_manager(new_session, context_engine, backend_factory, config)
    return ForkResult(session_id=new_id, session=new_session, version_control=new_vc)
