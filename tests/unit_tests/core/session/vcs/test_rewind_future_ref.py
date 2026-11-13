# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rewind must reject event refs that are not in this session's history."""

import pytest

from openjiuwen.core.session.vcs.jsonl_backend import JsonlBackend
from openjiuwen.core.session.vcs.manager import VersioningManager


@pytest.mark.asyncio
async def test_rewind_future_event_ref_is_rejected(tmp_path) -> None:
    async def provider() -> dict:
        return {"context": {}, "state": {}}

    async def applier(_snapshot: dict) -> None:
        return None

    backend = JsonlBackend("s1", tmp_path)
    manager = VersioningManager(
        "s1",
        backend,
        snapshot_provider=provider,
        applier=applier,
    )

    with pytest.raises(Exception, match="unknown vcs ref"):
        await manager.rewind("e5")

    assert manager.current_head().event_id == 0
