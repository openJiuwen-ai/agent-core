# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""VersioningManager logic: WAL replay, snapshot, commit chain, history."""
import pytest


@pytest.mark.asyncio
async def test_append_replay_roundtrip(make_manager):
    manager, live = make_manager()
    live["context"]["c1"] = {"messages": [], "offload_messages": {}}
    for i in range(3):
        live["context"]["c1"]["messages"].append({"role": "user", "content": str(i)})
        await manager.append()
    live["state"]["counter"] = 3
    await manager.append()
    restored = await manager.restore(f"e{manager.current_head().event_id}")
    assert [m["content"] for m in restored["context"]["c1"]["messages"]] == ["0", "1", "2"]
    assert restored["state"]["counter"] == 3


@pytest.mark.asyncio
async def test_empty_append_is_noop(make_manager):
    manager, _ = make_manager()
    ref = await manager.append()
    assert ref == "e0"
    assert manager.current_head().event_id == 0


@pytest.mark.asyncio
async def test_snapshot_does_not_change_replay(make_manager):
    manager, live = make_manager(ids=["snap1"])
    live["context"]["c1"] = {"messages": [], "offload_messages": {}}
    for i in range(3):
        live["context"]["c1"]["messages"].append({"i": i})
        await manager.append()
    await manager.snapshot()
    for i in range(3, 5):
        live["context"]["c1"]["messages"].append({"i": i})
        await manager.append()
    restored = await manager.restore(f"e{manager.current_head().event_id}")
    assert [m["i"] for m in restored["context"]["c1"]["messages"]] == [0, 1, 2, 3, 4]
    mid = await manager.restore("e3")
    assert [m["i"] for m in mid["context"]["c1"]["messages"]] == [0, 1, 2]


@pytest.mark.asyncio
async def test_commit_chain(make_manager):
    manager, live = make_manager(ids=["A", "B", "C"])
    live["state"]["x"] = 1
    await manager.commit("first")
    live["state"]["x"] = 2
    await manager.commit("second")
    live["state"]["x"] = 3
    await manager.commit("third")
    history = await manager.list_history()
    assert [c.commit_id for c in history] == ["C", "B", "A"]
    assert history[0].parent_id == "B"
    assert [c.event_id_high for c in history] == [3, 2, 1]


@pytest.mark.asyncio
async def test_restore_at_commit_ref(make_manager):
    manager, live = make_manager(ids=["A", "B"])
    live["state"]["x"] = 1
    commit_a = await manager.commit("a")
    live["state"]["x"] = 2
    await manager.commit("b")
    restored = await manager.restore(commit_a)
    assert restored["state"]["x"] == 1


@pytest.mark.asyncio
async def test_unknown_ref_raises(make_manager):
    manager, _ = make_manager()
    with pytest.raises(Exception):
        await manager.restore("nope")
