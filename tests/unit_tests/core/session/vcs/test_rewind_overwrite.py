# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Rewind is same-session, overwrite: history after the target is truncated."""
import pytest


@pytest.mark.asyncio
async def test_rewind_truncates_and_overwrites(make_manager):
    manager, live = make_manager()
    live["context"]["c1"] = {"messages": [], "offload_messages": {}}
    for i in range(5):
        live["context"]["c1"]["messages"].append({"i": i})
        await manager.append()
    assert manager.current_head().event_id == 5

    restored = await manager.rewind("e2")
    assert [m["i"] for m in restored["context"]["c1"]["messages"]] == [0, 1]
    assert manager.current_head().event_id == 2
    # rewind never changes the session id
    assert manager._session_id == "sess"
    # live state was reloaded to the rewound point
    assert [m["i"] for m in live["context"]["c1"]["messages"]] == [0, 1]
    # e3-e5 are physically gone
    entries = await manager._backend.read_log()
    assert [e.event_id for e in entries] == [1, 2]

    # continuing appends overwrite from e3
    live["context"]["c1"]["messages"].append({"i": 99})
    ref = await manager.append()
    assert ref == "e3"
    assert manager.current_head().event_id == 3
    final = await manager.restore("e3")
    assert [m["i"] for m in final["context"]["c1"]["messages"]] == [0, 1, 99]
