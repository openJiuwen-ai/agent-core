# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Crash-recovery tests: corrupt log tail must stop the read without raising."""
import pytest

from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.session.vcs.jsonl_backend import JsonlBackend
from openjiuwen.core.session.vcs.kv_backend import KvBackend
from openjiuwen.core.session.vcs.models import LogEntry, StateDelta


def _entry(event_id):
    return LogEntry(event_id=event_id, state=StateDelta(set={"k": event_id}), ts=1.0)


@pytest.mark.asyncio
async def test_jsonl_torn_tail_stops_read(tmp_path):
    backend = JsonlBackend("sess", tmp_path)
    await backend.append_log(_entry(1))
    await backend.append_log(_entry(2))
    log_path = tmp_path / "sess" / "logs" / "log.jsonl"
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write('{"event_id": 3, "ts": 1.0, "crc": 99999, ')  # truncated json, no newline
    assert [e.event_id for e in await backend.read_log()] == [1, 2]


@pytest.mark.asyncio
async def test_jsonl_bad_crc_stops_read(tmp_path):
    backend = JsonlBackend("sess", tmp_path)
    await backend.append_log(_entry(1))
    log_path = tmp_path / "sess" / "logs" / "log.jsonl"
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write('{"event_id":2,"context":[],"state":{"set":{},"removed":[]},"ts":1.0,"crc":123}\n')
    assert [e.event_id for e in await backend.read_log()] == [1]


@pytest.mark.asyncio
async def test_kv_bad_value_stops_read(tmp_path):
    kv = InMemoryKVStore()
    backend = KvBackend("sess", kv)
    await backend.append_log(_entry(1))
    await kv.set("sess:vcs:log:" + "2".zfill(20), "not-json")
    assert [e.event_id for e in await backend.read_log()] == [1]
