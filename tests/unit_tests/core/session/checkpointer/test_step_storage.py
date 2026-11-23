# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.foundation.store import DbBasedKVStore
from openjiuwen.core.graph.store import create_serializer
from openjiuwen.core.session.checkpointer.step_storage import (
    SESSION_NAMESPACE_AGENT_STEP,
    AgentStepStorage,
    StepResume,
)
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent import AgentSession
from tests.unit_tests.core.session.checkpointer.test_persistence_storage import MockKVStore

_SERDE = create_serializer("pickle")


def make_session(sid: str, aid: str) -> AgentSession:
    session = AgentSession(session_id=sid, config=Config())
    session.agent_id = Mock(return_value=aid)
    return session


def set_msgs(session: AgentSession, texts: list[str]) -> None:
    session.state().update_global({
        "context": {
            "cid": {
                "messages": [UserMessage(content=t) for t in texts],
                "offload_messages": {},
            }
        }
    })


def contents(session: AgentSession) -> list[str]:
    state = session.state().get_global("context") or {}
    return [m.content for m in (state.get("cid") or {}).get("messages", [])]


def prefix(sid: str = "s1", aid: str = "agent-1") -> str:
    return f"{sid}:{SESSION_NAMESPACE_AGENT_STEP}:{aid}:"


def keys(kv: MockKVStore, sid: str = "s1", aid: str = "agent-1") -> list[str]:
    p = prefix(sid, aid)
    return sorted(k for k in kv._store if k.startswith(p))


def load(kv: MockKVStore, key: str):
    return _SERDE.loads_typed(("pickle", kv._store[key]))


class _FlakyKVStore(MockKVStore):
    """MockKVStore whose first ``set`` fails, mimicking a full disk."""

    def __init__(self):
        super().__init__()
        self.fail_next = True

    async def set(self, key: str, value):
        if self.fail_next:
            self.fail_next = False
            raise OSError("disk full")
        await super().set(key, value)


@pytest.mark.asyncio
async def test_first_append_is_reset_then_append():
    kv = MockKVStore()
    storage = AgentStepStorage(kv)
    session = make_session("s1", "agent-1")

    set_msgs(session, ["q", "a1"])
    await storage.append(session, kind="step", iteration=0, query="q")
    set_msgs(session, ["q", "a1", "a2"])
    await storage.append(session, kind="step", iteration=1, query="q")

    ks = keys(kv)
    assert ks == [prefix() + "0" * 20, prefix() + "0" * 19 + "1"]
    assert [d.kind for d in load(kv, ks[0]).context] == ["reset"]
    assert [d.kind for d in load(kv, ks[1]).context] == ["append"]


@pytest.mark.asyncio
async def test_failed_write_keeps_baseline_and_next_record_covers_both_rounds():
    kv = _FlakyKVStore()
    storage = AgentStepStorage(kv)
    session = make_session("s1", "agent-1")

    set_msgs(session, ["q", "a1"])
    with pytest.raises(OSError):
        await storage.append(session, kind="step", iteration=0, query="q")

    set_msgs(session, ["q", "a1", "a2"])
    await storage.append(session, kind="step", iteration=1, query="q")

    ks = keys(kv)
    assert ks == [prefix() + "0" * 20]
    record = load(kv, ks[0])
    assert [d.kind for d in record.context] == ["reset"]
    messages = record.context[0].messages
    assert [m["content"] for m in messages] == ["q", "a1", "a2"]
    assert record.iteration == 1


@pytest.mark.asyncio
async def test_recover_in_fresh_process_replays_and_continues_seq():
    kv = MockKVStore()
    writer = AgentStepStorage(kv)
    session_a = make_session("s1", "agent-1")
    set_msgs(session_a, ["q"])
    await writer.append(session_a, kind="step", iteration=0, query="q")
    set_msgs(session_a, ["q", "a1", "a2"])
    await writer.append(session_a, kind="step", iteration=1, query="q")

    storage_b = AgentStepStorage(kv)
    session_b = make_session("s1", "agent-1")
    await storage_b.recover(session_b)

    assert contents(session_b) == ["q", "a1", "a2"]
    assert storage_b.status(session_b) == "resume_pending"
    assert storage_b.peek_resume(session_b) == StepResume(iteration=1, query="q")
    assert storage_b.begin(session_b) == StepResume(1, "q")
    assert storage_b.peek_resume(session_b) is None

    set_msgs(session_b, ["q", "a1", "a2", "a3"])
    await storage_b.append(session_b, kind="step", iteration=2, query="q")
    assert keys(kv)[-1] == prefix() + "0" * 19 + "2"


@pytest.mark.asyncio
async def test_recover_truncates_from_first_corrupt_record():
    kv = MockKVStore()
    writer = AgentStepStorage(kv)
    session_a = make_session("s1", "agent-1")
    for i, texts in enumerate([["q"], ["q", "a1"], ["q", "a1", "a2"]]):
        set_msgs(session_a, texts)
        await writer.append(session_a, kind="step", iteration=i, query="q")

    ks = keys(kv)
    assert len(ks) == 3
    kv._store[ks[1]] = b"garbage"

    storage_b = AgentStepStorage(kv)
    session_b = make_session("s1", "agent-1")
    await storage_b.recover(session_b)

    assert contents(session_b) == ["q"]
    assert keys(kv) == [prefix() + "0" * 20]
    await storage_b.append(session_b, kind="step", iteration=0, query="q")
    assert keys(kv)[-1] == prefix() + "0" * 19 + "1"


@pytest.mark.asyncio
async def test_like_wildcards_and_agent_prefix_do_not_leak(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kv.db'}")
    kv = DbBasedKVStore(engine)
    try:
        storage = AgentStepStorage(kv)
        for sid, aid in [("a_b", "agent"), ("axb", "agent"), ("a_b", "agent:x")]:
            session = make_session(sid, aid)
            set_msgs(session, ["q"])
            await storage.append(session, kind="step", iteration=0, query="q")

        await storage.clear("a_b", "agent")

        remaining = await kv.get_by_prefix("a")
        assert sorted(remaining) == [
            "a_b:agent-step:agent:x:" + "0" * 20,
            "axb:agent-step:agent:" + "0" * 20,
        ]

        fresh = AgentStepStorage(kv)
        session = make_session("a_b", "agent")
        await fresh.recover(session)
        assert fresh.status(session) is None
        assert contents(session) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_writer_process_recover_uses_baseline_without_reading():
    kv = MockKVStore()
    storage = AgentStepStorage(kv)
    session = make_session("s1", "agent-1")
    set_msgs(session, ["q", "a1"])
    await storage.append(session, kind="step", iteration=0, query="q")

    reads: list[str] = []
    original = kv.get_by_prefix

    async def spy(p):
        reads.append(p)
        return await original(p)

    kv.get_by_prefix = spy

    temp_session = make_session("s1", "agent-1")
    await storage.recover(temp_session)

    assert contents(temp_session) == ["q", "a1"]
    assert reads == []


@pytest.mark.asyncio
async def test_end_writes_interrupt_even_without_baseline_and_final_only_with_records():
    kv = MockKVStore()
    storage = AgentStepStorage(kv)

    interrupted = make_session("s-int", "agent-1")
    await storage.end(interrupted, interrupted=True)
    assert [load(kv, k).kind for k in keys(kv, "s-int")] == ["interrupt"]
    assert storage.status(interrupted) == "interrupted"

    finished = make_session("s-fin", "agent-1")
    storage.begin(finished)
    set_msgs(finished, ["q"])
    await storage.append(finished, kind="step", iteration=0, query="q")
    await storage.end(finished, interrupted=False)
    assert [load(kv, k).kind for k in keys(kv, "s-fin")] == ["step", "final"]
    assert storage.status(finished) == "idle"
    await storage.end(finished, interrupted=False)
    assert [load(kv, k).kind for k in keys(kv, "s-fin")] == ["step", "final"]

    untouched = make_session("s-idle", "agent-1")
    storage.begin(untouched)
    await storage.end(untouched, interrupted=False)
    assert keys(kv, "s-idle") == []


@pytest.mark.asyncio
async def test_session_without_agent_card_is_ignored():
    """Card-less sessions have no agent id; step storage must not crash on them."""
    kv = MockKVStore()
    storage = AgentStepStorage(kv)
    session = AgentSession(session_id="s-nocard", config=Config())

    assert storage.begin(session) is None
    assert storage.peek_resume(session) is None
    assert storage.has_records(session) is False
    assert storage.status(session) is None
    await storage.append(session, kind="step", iteration=0, query="q")
    await storage.recover(session)
    await storage.end(session, interrupted=False)

    assert kv._store == {}
