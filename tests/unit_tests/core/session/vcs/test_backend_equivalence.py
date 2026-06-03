# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""The jsonl and kv backends are equivalent under the same operation sequence."""
from copy import deepcopy

import pytest

from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.session.vcs.backend import encode_log_entry
from openjiuwen.core.session.vcs.jsonl_backend import JsonlBackend
from openjiuwen.core.session.vcs.kv_backend import KvBackend
from openjiuwen.core.session.vcs.manager import VersioningManager


def _manager(backend, live, ids):
    seq = iter(ids)

    async def provider():
        return deepcopy(live)

    async def applier(snap):
        live["context"] = deepcopy(snap["context"])
        live["state"] = deepcopy(snap["state"])

    return VersioningManager(
        "s",
        backend,
        snapshot_provider=provider,
        applier=applier,
        clock=lambda: 1.0,
        id_factory=lambda: next(seq),
    )


async def _run_sequence(manager, live):
    live["context"]["c1"] = {"messages": [], "offload_messages": {}}
    for i in range(4):
        live["context"]["c1"]["messages"].append({"i": i})
        await manager.append()
    live["state"]["x"] = 1
    await manager.commit("m")
    await manager.rewind("e3")
    live["context"]["c1"]["messages"].append({"i": 99})
    await manager.append()


@pytest.mark.asyncio
async def test_jsonl_and_kv_equivalent(tmp_path):
    live_j = {"context": {}, "state": {}}
    mgr_j = _manager(JsonlBackend("s", tmp_path), live_j, ids=["cid"])
    await _run_sequence(mgr_j, live_j)

    live_k = {"context": {}, "state": {}}
    mgr_k = _manager(KvBackend("s", InMemoryKVStore()), live_k, ids=["cid"])
    await _run_sequence(mgr_k, live_k)

    assert mgr_j.current_head().event_id == mgr_k.current_head().event_id
    head = mgr_j.current_head().event_id

    restored_j = await mgr_j.restore(f"e{head}")
    restored_k = await mgr_k.restore(f"e{head}")
    assert restored_j == restored_k

    entries_j = await mgr_j._backend.read_log()
    entries_k = await mgr_k._backend.read_log()
    assert [encode_log_entry(e) for e in entries_j] == [encode_log_entry(e) for e in entries_k]

    history_j = await mgr_j.list_history()
    history_k = await mgr_k.list_history()
    assert [c.model_dump() for c in history_j] == [c.model_dump() for c in history_k]
