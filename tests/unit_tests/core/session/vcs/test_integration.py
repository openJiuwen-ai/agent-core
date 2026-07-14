# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""End-to-end integration with a real AgentSession + ContextEngine.

Exercises ``for_session``: append message-level history, restore, overwrite
rewind (same session), and fork (new session_id, independent, source intact).
"""
import pytest

from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UserMessage
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.vcs import VersioningConfig, for_session

_CID = "default_context_id"


async def _add(context_engine, session, *messages):
    context = context_engine.get_context(_CID, session.get_session_id())
    if context is None:
        context = await context_engine.create_context(_CID, session)
    for message in messages:
        await context.add_messages(message)


def _contents(snapshot):
    return [m["content"] for m in snapshot["context"][_CID]["messages"]]


@pytest.mark.asyncio
async def test_append_restore_rewind_fork(tmp_path):
    engine = ContextEngine()
    session = create_agent_session(session_id="src")
    await engine.create_context(_CID, session)

    kv = InMemoryKVStore()
    vc = for_session(session, engine, config=VersioningConfig(backend="kv"), kv_store=kv)

    await _add(engine, session, UserMessage(content="hello"), AssistantMessage(content="hi there"))
    await vc.append()
    await _add(engine, session, UserMessage(content="more"))
    await vc.append()

    head = vc.current_head().event_id
    restored = await vc.restore(f"e{head}")
    assert _contents(restored) == ["hello", "hi there", "more"]

    # overwrite-rewind to e1 (state after first turn), same session_id
    await vc.rewind("e1")
    assert _contents(await vc.restore(f"e{vc.current_head().event_id}")) == ["hello", "hi there"]
    live = engine.get_context(_CID, "src")
    assert [m.content for m in live.get_messages()] == ["hello", "hi there"]

    # fork: new session_id, seeded with the (rewound) history; source untouched
    result = await vc.fork()
    assert result.session_id != "src"
    forked_head = result.version_control.current_head().event_id
    forked = await result.version_control.restore(f"e{forked_head}")
    assert _contents(forked) == ["hello", "hi there"]
    # fork does not move the source head; it stays at the rewound point (e1)
    assert vc.current_head().event_id == 1
