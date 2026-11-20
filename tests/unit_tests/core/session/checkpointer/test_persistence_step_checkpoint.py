# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.session.checkpointer.persistence import (
    PersistenceCheckpointer,
    PersistenceCheckpointerProvider,
    _commit_durability_issue,
)
from openjiuwen.core.session.checkpointer.step_storage import SESSION_NAMESPACE_AGENT_STEP, StepResume
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.single_agent import AgentCard
from tests.unit_tests.core.session.checkpointer.test_persistence_storage import MockKVStore
from tests.unit_tests.core.session.checkpointer.test_step_storage import contents, keys, load, set_msgs


def make_session(sid: str = "s1", aid: str = "agent-1") -> AgentSession:
    session = AgentSession(session_id=sid, config=Config())
    session.agent_id = Mock(return_value=aid)
    return session


@pytest.mark.asyncio
async def test_pre_agent_execute_replays_steps_over_older_blob():
    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    writer = make_session()
    set_msgs(writer, ["q"])
    await cp.post_agent_execute(writer)
    assert await cp._agent_storage.exists(writer) is True

    assert cp.begin_agent_steps(writer) is None
    set_msgs(writer, ["q", "a1"])
    await cp.save_agent_step(writer, 0, query="q")
    set_msgs(writer, ["q", "a1", "a2"])
    await cp.save_agent_step(writer, 1, query="q")

    reader = make_session()
    restarted = PersistenceCheckpointer(kv)
    await restarted.pre_agent_execute(reader, None)

    assert contents(reader) == ["q", "a1", "a2"]
    assert restarted.peek_agent_step_resume(reader) == StepResume(iteration=1, query="q")


@pytest.mark.asyncio
async def test_interrupt_appends_record_and_skips_blob():
    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    session = make_session()
    set_msgs(session, ["q"])

    await cp.interrupt_agent_execute(session)

    assert await cp._agent_storage.exists(session) is False
    assert load(kv, keys(kv)[-1]).kind == "interrupt"

    reader = make_session()
    restarted = PersistenceCheckpointer(kv)
    await restarted.pre_agent_execute(reader, None)
    assert restarted._step_storage.status(reader) == "interrupted"
    assert restarted.peek_agent_step_resume(reader) is None


@pytest.mark.asyncio
async def test_mid_task_commit_keeps_records_and_skips_blob():
    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    writer = make_session()
    set_msgs(writer, ["q"])
    await cp.post_agent_execute(writer)
    _, blob_key = cp._agent_storage._build_state_keys("s1", "agent-1")
    blob_before = kv._store[blob_key]

    cp.begin_agent_steps(writer)
    set_msgs(writer, ["q", "a1"])
    await cp.save_agent_step(writer, 0, query="q")

    temp = make_session()
    await cp.pre_agent_execute(temp, None)
    set_msgs(temp, ["q", "a1", "temporary"])
    await cp.post_agent_execute(temp)

    assert len(keys(kv)) == 1
    assert kv._store[blob_key] == blob_before

    set_msgs(writer, ["q", "a1", "a2"])
    await cp.save_agent_step(writer, 1, query="q")
    assert [d.kind for d in load(kv, keys(kv)[-1]).context] == ["append"]


@pytest.mark.asyncio
async def test_post_after_end_saves_blob_then_clears_records():
    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    session = make_session()
    cp.begin_agent_steps(session)
    set_msgs(session, ["q"])
    await cp.save_agent_step(session, 0, query="q")
    await cp.end_agent_steps(session, interrupted=False)

    await cp.post_agent_execute(session)

    assert keys(kv) == []
    assert await cp._agent_storage.exists(session) is True


@pytest.mark.asyncio
async def test_post_after_end_keeps_records_when_blob_serialization_fails(monkeypatch):
    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    session = make_session()
    cp.begin_agent_steps(session)
    set_msgs(session, ["q"])
    await cp.save_agent_step(session, 0, query="q")
    await cp.end_agent_steps(session, interrupted=False)
    monkeypatch.setattr(cp._agent_storage, "_serialize_state", lambda state: None)

    await cp.post_agent_execute(session)

    assert len(keys(kv)) >= 1


@pytest.mark.asyncio
async def test_post_on_interrupted_appends_interrupt_without_blob():
    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    session = make_session()
    cp.begin_agent_steps(session)
    set_msgs(session, ["q"])
    await cp.save_agent_step(session, 0, query="q")
    await cp.end_agent_steps(session, interrupted=True)
    set_msgs(session, ["q", "a1"])

    await cp.post_agent_execute(session)

    assert [load(kv, k).kind for k in keys(kv)][-2:] == ["interrupt", "interrupt"]
    assert await cp._agent_storage.exists(session) is False


@pytest.mark.asyncio
async def test_final_record_recovers_idle_and_next_commit_clears():
    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    session = make_session()
    cp.begin_agent_steps(session)
    set_msgs(session, ["q", "a1"])
    await cp.save_agent_step(session, 0, query="q", final=True)

    reader = make_session()
    restarted = PersistenceCheckpointer(kv)
    await restarted.pre_agent_execute(reader, None)

    assert restarted._step_storage.status(reader) == "idle"
    assert restarted.peek_agent_step_resume(reader) is None
    assert contents(reader) == ["q", "a1"]

    await restarted.post_agent_execute(reader)
    assert keys(kv) == []


@pytest.mark.asyncio
async def test_release_clears_step_records():
    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    session = make_session()
    cp.begin_agent_steps(session)
    set_msgs(session, ["q"])
    await cp.save_agent_step(session, 0, query="q")

    await cp.release("s1", "agent-1")

    assert keys(kv) == []


@pytest.mark.asyncio
async def test_session_wrapper_and_inmemory_defaults():
    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    original = CheckpointerFactory._default_checkpointer
    try:
        CheckpointerFactory.set_default_checkpointer(cp)
        session = create_agent_session(
            session_id="s-wrap", card=AgentCard(id="agent-wrap", name="wrap")
        )
        assert session.begin_agent_steps() is None
        assert session.peek_agent_step_resume() is None
        session.update_state({
            "context": {"cid": {"messages": [UserMessage(content="q")], "offload_messages": {}}}
        })
        await session.save_agent_step(0, query="q")
        await session.end_agent_steps(interrupted=False)
        assert [load(kv, k).kind for k in keys(kv, "s-wrap", "agent-wrap")] == ["step", "final"]
    finally:
        CheckpointerFactory.set_default_checkpointer(original)

    memory = InMemoryCheckpointer()
    fake = Mock()
    assert memory.begin_agent_steps(fake) is None
    assert memory.peek_agent_step_resume(fake) is None
    await memory.save_agent_step(fake, 0, query="q")
    await memory.save_agent_step(fake, 0, query="q", final=True)
    await memory.end_agent_steps(fake, interrupted=True)
    await memory.end_agent_steps(fake, interrupted=False)


def test_namespace_constant_is_stable():
    assert SESSION_NAMESPACE_AGENT_STEP == "agent-step"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConnection:
    def __init__(self, value, fail=False):
        self._value = value
        self._fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, statement):
        assert isinstance(statement, TextClause)
        return _FakeResult(self._value)


class _FakeEngine:
    def __init__(self, dialect, value=None, fail=False):
        self.dialect = SimpleNamespace(name=dialect)
        self._value = value
        self._fail = fail

    def connect(self):
        if self._fail:
            raise RuntimeError("boom")
        return _FakeConnection(self._value)


@pytest.mark.asyncio
async def test_provider_sqlite_uses_full_synchronous(tmp_path):
    cp = await PersistenceCheckpointerProvider().create(
        {"db_type": "sqlite", "db_path": str(tmp_path / "checkpoint")}
    )
    try:
        async with cp._kv_store.engine.connect() as conn:
            assert (await conn.execute(text("PRAGMA synchronous"))).scalar() == 2
    finally:
        await cp._kv_store.engine.dispose()


@pytest.mark.parametrize("dialect,value,ok", [
    ("mysql", 1, True),
    ("mysql", 2, False),
    ("postgresql", "on", True),
    ("postgresql", "local", True),
    ("postgresql", "off", False),
    ("sqlite", None, True),
])
@pytest.mark.asyncio
async def test_commit_durability_issue(dialect, value, ok):
    assert (await _commit_durability_issue(_FakeEngine(dialect, value)) is None) is ok


@pytest.mark.asyncio
async def test_commit_durability_issue_reports_read_failure():
    issue = await _commit_durability_issue(_FakeEngine("mysql", fail=True))
    assert issue.startswith("cannot read commit durability setting")
