# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression coverage for persisted state and live context restore semantics."""

import json
import zlib

import pytest

from openjiuwen.core.context_engine import ContextEngine, StringLengthCounter
from openjiuwen.core.context_engine.processor.offloader.message_offloader import MessageOffloaderConfig
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.vcs import for_session
from openjiuwen.core.session.vcs.adapter import _apply
from openjiuwen.core.session.vcs.backend import decode_log_entry, encode_log_entry
from openjiuwen.core.session.vcs.delta import apply_state, diff_state
from openjiuwen.core.session.vcs.kv_backend import KvBackend
from openjiuwen.core.session.vcs.models import Head, LogEntry, Snapshot, StateDelta


def _legacy_wal() -> str:
    """Build a historical record independently of the current model serializer."""
    data = {
        "event_id": 1,
        "context": [],
        "state": {"set": {"a~1b": 2}, "removed": ["x~0y"]},
        "ts": 0.0,
    }
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    data["crc"] = zlib.crc32(canonical.encode("utf-8"))
    return json.dumps(data)


def test_legacy_wal_crc_and_literal_paths_are_preserved() -> None:
    entry = decode_log_entry(_legacy_wal())
    assert entry is not None
    assert "path_encoding" not in json.loads(encode_log_entry(entry))["state"]
    assert apply_state({"a~1b": 1, "x~0y": 9}, entry.state) == {"a~1b": 2}


def test_versioned_paths_keep_literal_and_dotted_keys_distinct() -> None:
    old = {"a~1b": 1, "a.b": 3, "gone.x": 8, "nested": {"x~0y": 0}}
    new = {"a~1b": 2, "a.b": None, "nested": {"x~0y": 5}}
    delta = diff_state(old, new)
    restored = decode_log_entry(encode_log_entry(LogEntry(event_id=2, state=delta)))
    assert restored is not None
    assert restored.state.path_encoding == 2
    assert apply_state(old, restored.state) == new
    with pytest.raises(ValueError):
        StateDelta(path_encoding=99)


@pytest.mark.asyncio
async def test_snapshot_and_mixed_wal_restore() -> None:
    store = InMemoryKVStore()
    backend = KvBackend("compat", store)
    await backend.put_snapshot(Snapshot(snapshot_id="base", state={"a~1b": 1, "x~0y": 9}))
    # Write legacy bytes, not a record produced by the new encoder.
    await store.set(backend._log_key(1), _legacy_wal())
    await backend.append_log(LogEntry(event_id=2, state=diff_state({"a~1b": 2}, {"a~1b": 4, "a.b": 7})))
    await backend.put_head(Head(event_id=2))
    session = create_agent_session(session_id="compat")
    vc = for_session(session, ContextEngine(), backend_factory=lambda _: backend)
    assert (await vc.restore("e1"))["state"] == {"a~1b": 2}
    assert (await vc.restore("e2"))["state"] == {"a~1b": 4, "a.b": 7}


@pytest.mark.asyncio
async def test_apply_preserves_context_runtime_and_other_sessions() -> None:
    engine = ContextEngine()
    session = create_agent_session(session_id="keep-runtime")
    counter = StringLengthCounter(model="custom-counter")
    # Use a real registered processor and ContextEngine creation path.
    context = await engine.create_context(
        "kept", session,
        processors=[("MessageOffloader", MessageOffloaderConfig())],
        token_counter=counter,
    )
    processors = list(context._processors)
    assert processors
    await context.add_messages(UserMessage(content="newer"))
    await engine.create_context("stale", session)
    other_session = create_agent_session(session_id="other")
    other_context = await engine.create_context("kept", other_session)
    await _apply(session, engine, {
        "context": {
            "kept": {"messages": [{"role": "user", "content": "earlier"}]},
            "missing": {"messages": []},
        },
        "state": {},
    })
    assert engine.get_context("kept", "keep-runtime") is context
    assert context._token_counter is counter
    assert context._processors == processors
    assert all(actual is expected for actual, expected in zip(context._processors, processors))
    assert [message.content for message in context.get_messages()] == ["earlier"]
    assert set(engine.list_context_ids("keep-runtime")) == {"kept", "missing"}
    assert engine.get_context("kept", "other") is other_context
    await _apply(session, engine, {"context": {}, "state": {}})
    assert engine.list_context_ids("keep-runtime") == []
    assert engine.get_context("kept", "other") is other_context
