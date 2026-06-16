# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Backend CRUD + truncate tests, parametrized across jsonl and kv backends."""
import pytest

from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.session.vcs.jsonl_backend import JsonlBackend
from openjiuwen.core.session.vcs.kv_backend import KvBackend
from openjiuwen.core.session.vcs.models import (
    Commit,
    Head,
    LogEntry,
    MessageDelta,
    Snapshot,
    StateDelta,
)


def _backend(kind, tmp_path):
    if kind == "jsonl":
        return JsonlBackend("sess-1", tmp_path)
    return KvBackend("sess-1", InMemoryKVStore())


def _entry(event_id, text="x"):
    return LogEntry(
        event_id=event_id,
        context=[MessageDelta(context_id="c1", kind="append", messages=[{"role": "user", "content": text}])],
        state=StateDelta(set={"k": event_id}),
        ts=1.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["jsonl", "kv"])
async def test_append_read_roundtrip(kind, tmp_path):
    backend = _backend(kind, tmp_path)
    await backend.append_log(_entry(1, "a"))
    await backend.append_log(_entry(2, "b"))
    entries = await backend.read_log()
    assert [e.event_id for e in entries] == [1, 2]
    assert entries[0].context[0].messages[0]["content"] == "a"
    assert entries[1].state.set == {"k": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["jsonl", "kv"])
async def test_read_since(kind, tmp_path):
    backend = _backend(kind, tmp_path)
    for i in range(1, 6):
        await backend.append_log(_entry(i))
    entries = await backend.read_log(since_event_id=3)
    assert [e.event_id for e in entries] == [4, 5]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["jsonl", "kv"])
async def test_snapshot_put_get_latest(kind, tmp_path):
    backend = _backend(kind, tmp_path)
    await backend.put_snapshot(Snapshot(snapshot_id="s1", event_id_high=2, state={"v": 1}))
    await backend.put_snapshot(Snapshot(snapshot_id="s2", event_id_high=5, state={"v": 2}))
    assert (await backend.get_snapshot("s1")).event_id_high == 2
    assert (await backend.latest_snapshot()).snapshot_id == "s2"
    assert (await backend.latest_snapshot(at_event_id=3)).snapshot_id == "s1"
    assert await backend.get_snapshot("nope") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["jsonl", "kv"])
async def test_commit_put_get_list(kind, tmp_path):
    backend = _backend(kind, tmp_path)
    await backend.put_commit(Commit(commit_id="c1", event_id_high=1))
    await backend.put_commit(Commit(commit_id="c2", parent_id="c1", event_id_high=3))
    assert (await backend.get_commit("c2")).parent_id == "c1"
    assert {c.commit_id for c in await backend.list_commits()} == {"c1", "c2"}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["jsonl", "kv"])
async def test_head_put_get_overwrite(kind, tmp_path):
    backend = _backend(kind, tmp_path)
    assert await backend.get_head() is None
    await backend.put_head(Head(event_id=7, commit_id="c1"))
    assert (await backend.get_head()).event_id == 7
    await backend.put_head(Head(event_id=9, commit_id="c2", forked_from=("src", "c0")))
    head = await backend.get_head()
    assert head.event_id == 9
    assert head.forked_from == ("src", "c0")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["jsonl", "kv"])
async def test_truncate_drops_after(kind, tmp_path):
    backend = _backend(kind, tmp_path)
    for i in range(1, 6):
        await backend.append_log(_entry(i))
    await backend.put_snapshot(Snapshot(snapshot_id="s_low", event_id_high=2))
    await backend.put_snapshot(Snapshot(snapshot_id="s_high", event_id_high=4))
    await backend.put_commit(Commit(commit_id="c_low", event_id_high=2))
    await backend.put_commit(Commit(commit_id="c_high", event_id_high=4))
    await backend.truncate(after_event_id=2)
    assert [e.event_id for e in await backend.read_log()] == [1, 2]
    assert await backend.get_snapshot("s_low") is not None
    assert await backend.get_snapshot("s_high") is None
    assert {c.commit_id for c in await backend.list_commits()} == {"c_low"}
