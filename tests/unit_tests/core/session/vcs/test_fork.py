# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Fork delegates to an injected forker with the seed snapshot; source is intact."""
import pytest

from openjiuwen.core.session.vcs.models import ForkResult


@pytest.mark.asyncio
async def test_fork_passes_seed_and_leaves_source_intact(make_manager):
    captured = {}

    async def forker(new_id, seed, forked_from):
        captured.update(new_id=new_id, seed=seed, forked_from=forked_from)
        return ForkResult(session_id=new_id, session=object(), version_control=object())

    manager, live = make_manager(forker=forker, ids=["NEW"])
    live["state"]["x"] = 1
    await manager.append()  # e1
    live["state"]["x"] = 2
    await manager.append()  # e2

    result = await manager.fork(at="e1")
    assert result.session_id == "NEW"
    assert captured["seed"]["state"] == {"x": 1}
    assert captured["forked_from"] == ("sess", "e1")
    # source session untouched by fork
    assert manager.current_head().event_id == 2
    assert manager._session_id == "sess"


@pytest.mark.asyncio
async def test_fork_default_at_head(make_manager):
    captured = {}

    async def forker(new_id, seed, forked_from):
        captured.update(seed=seed, forked_from=forked_from)
        return ForkResult(session_id=new_id, session=None, version_control=None)

    manager, live = make_manager(forker=forker, ids=["NEW"])
    live["state"]["x"] = 5
    await manager.append()  # e1
    await manager.fork()  # at=None -> head
    assert captured["seed"]["state"] == {"x": 5}
    assert captured["forked_from"] == ("sess", "e1")


@pytest.mark.asyncio
async def test_fork_without_forker_raises(make_manager):
    manager, _ = make_manager()
    with pytest.raises(Exception):
        await manager.fork()
