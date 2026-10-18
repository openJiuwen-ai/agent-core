# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""VCS record ids must not escape their session directory."""

import pytest

from openjiuwen.core.session.vcs.jsonl_backend import JsonlBackend
from openjiuwen.core.session.vcs.models import Commit, Snapshot


@pytest.mark.asyncio
async def test_commit_id_with_parent_segments_is_not_read(tmp_path) -> None:
    backend = JsonlBackend("s1", tmp_path)
    outside = tmp_path / "s1" / "commit_out.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "s1" / "commits").mkdir(parents=True, exist_ok=True)
    outside.write_text(
        Commit(commit_id="x", event_id_high=0).model_dump_json(),
        encoding="utf-8",
    )

    assert await backend.get_commit("../commit_out") is None


@pytest.mark.asyncio
async def test_snapshot_id_with_separator_is_not_read(tmp_path) -> None:
    backend = JsonlBackend("s1", tmp_path)
    outside = tmp_path / "s1" / "snap_out.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "s1" / "snapshots").mkdir(parents=True, exist_ok=True)
    outside.write_text(
        Snapshot(snapshot_id="x", event_id_high=0).model_dump_json(),
        encoding="utf-8",
    )

    assert await backend.get_snapshot("../snap_out") is None
