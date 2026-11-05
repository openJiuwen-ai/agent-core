# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-Session checkpointer injection keeps the default path unchanged."""

import pytest

from openjiuwen.core.session.agent import Session, create_agent_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory, NoopCheckpointer
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.single_agent import AgentCard


class SpyCheckpointer(NoopCheckpointer):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.calls = []

    async def pre_agent_execute(self, session, inputs):
        self.calls.append("pre")

    async def post_agent_execute(self, session):
        self.calls.append("post")


@pytest.fixture
def global_default(monkeypatch):
    """Swap the process default checkpointer and restore it afterwards."""
    monkeypatch.setattr(CheckpointerFactory, "_default_checkpointer", None)

    def install(checkpointer):
        CheckpointerFactory.set_default_checkpointer(checkpointer)
        return checkpointer

    return install


@pytest.mark.asyncio
async def test_injected_checkpointer_handles_pre_run_and_commit(global_default):
    global_spy = global_default(SpyCheckpointer("global"))
    injected = SpyCheckpointer("injected")
    session = Session(session_id="s-injected", checkpointer=injected)

    assert session.checkpointer() is injected
    await session.pre_run(inputs={"query": "x"})
    await session.commit()

    assert injected.calls == ["pre", "post"]
    assert global_spy.calls == []


@pytest.mark.asyncio
async def test_default_session_keeps_checkpointer_selection_timing(global_default):
    at_construction = global_default(SpyCheckpointer("construction"))
    session = Session(session_id="s-default")
    at_run_time = global_default(SpyCheckpointer("run-time"))

    assert session.checkpointer() is at_construction
    await session.pre_run(inputs={"query": "x"})
    await session.commit()

    # pre_run still looks the global default up when it runs; commit uses the one bound at construction.
    assert at_run_time.calls == ["pre"]
    assert at_construction.calls == ["post"]


@pytest.mark.asyncio
async def test_create_agent_session_passes_the_checkpointer():
    injected = NoopCheckpointer()
    assert create_agent_session(session_id="s-factory", checkpointer=injected).checkpointer() is injected
    assert not isinstance(create_agent_session(session_id="s-factory-default").checkpointer(), NoopCheckpointer)


@pytest.mark.asyncio
async def test_noop_session_neither_restores_nor_saves_global_state(global_default):
    global_default(InMemoryCheckpointer())
    session_id = "s-shared-id"
    card = AgentCard(id="agent", name="agent")

    earlier = Session(session_id=session_id, card=card)
    await earlier.pre_run()
    earlier.update_state({"marker": "earlier"})
    await earlier.commit()

    isolated = Session(session_id=session_id, card=card, checkpointer=NoopCheckpointer())
    await isolated.pre_run()
    assert isolated.get_state("marker") is None, "old checkpointer state must not overwrite the isolated Session"
    isolated.update_state({"marker": "isolated"})
    assert isolated.get_state("marker") == "isolated"
    await isolated.commit()

    later = Session(session_id=session_id, card=card)
    await later.pre_run()
    assert later.get_state("marker") == "earlier", "the isolated Session must not have saved its state"


@pytest.mark.asyncio
async def test_noop_checkpointer_does_no_storage_io():
    first, second = NoopCheckpointer(), NoopCheckpointer()
    session = Session(session_id="s-noop", checkpointer=first)
    inner = session._inner

    assert await first.pre_agent_execute(inner, {"query": "x"}) is None
    assert await first.post_agent_execute(inner) is None
    assert await first.interrupt_agent_execute(inner) is None
    assert await first.pre_agent_team_execute(inner, None) is None
    assert await first.post_agent_team_execute(inner) is None
    assert await first.pre_workflow_execute(inner, None) is None
    assert await first.post_workflow_execute(inner, None, None) is None
    assert await first.release("s-noop") is None
    assert await first.session_exists("s-noop") is False
    assert first.graph_store() is first.graph_store()
    assert first.graph_store() is not second.graph_store()
