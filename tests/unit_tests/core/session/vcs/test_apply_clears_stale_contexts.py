# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A restored snapshot replaces the context set, not just its listed contexts."""

from types import SimpleNamespace

import pytest

from openjiuwen.core.session.vcs.adapter import _apply


class _Context:
    def __init__(self) -> None:
        self.loaded: list[dict] = []

    def load_state(self, state: dict) -> None:
        self.loaded.append(state)


class _ContextEngine:
    def __init__(self) -> None:
        self.contexts = {"a": _Context(), "stale": _Context()}

    def get_context(self, cid: str, session_id: str):
        return self.contexts.get(cid)

    async def create_context(self, cid: str, session):
        context = _Context()
        self.contexts[cid] = context
        return context

    async def clear_context(self, *, context_id=None, session_id=None):
        self.contexts.clear()


class _State:
    def set_state(self, state: dict) -> None:
        self.state = state


class _Inner:
    def state(self) -> _State:
        return _State()


class _Session:
    def __init__(self) -> None:
        self._inner = _Inner()

    def get_session_id(self) -> str:
        return "s1"


@pytest.mark.asyncio
async def test_apply_drops_contexts_absent_from_snapshot() -> None:
    session = _Session()
    engine = _ContextEngine()

    await _apply(
        session,
        engine,
        {"context": {"a": {"messages": [], "offload_messages": {}}}, "state": {}},
    )

    assert set(engine.contexts) == {"a"}
