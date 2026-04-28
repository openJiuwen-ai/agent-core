# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Isolation of per-member memory directories and index managers."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.memory.team.member_memory_toolkit import MemberMemoryToolkit


class MockWorkspace:
    def __init__(self, root: str):
        self._root = root

    def get_node_path(self, node_name: str) -> str:
        node_path = os.path.join(self._root, node_name)
        os.makedirs(node_path, exist_ok=True)
        return node_path


@pytest.fixture
def pair_roots():
    base = tempfile.mkdtemp(prefix="iso_mem_")
    root_a = os.path.join(base, "ws_a")
    root_b = os.path.join(base, "ws_b")
    os.makedirs(root_a)
    os.makedirs(root_b)
    yield root_a, root_b
    shutil.rmtree(base, ignore_errors=True)


@pytest.mark.asyncio
@patch("openjiuwen.core.memory.lite.manager.MemoryIndexManager.get", new_callable=AsyncMock)
@patch("openjiuwen.core.memory.lite.config.is_memory_enabled", return_value=True)
async def test_two_members_distinct_managers_and_disk_paths(mock_enabled, mock_get, pair_roots):
    root_a, root_b = pair_roots
    mgr_a = MagicMock()
    mgr_a.closed = False
    mgr_b = MagicMock()
    mgr_b.closed = False
    mock_get.side_effect = [mgr_a, mgr_b]

    wa = MockWorkspace(root_a)
    wb = MockWorkspace(root_b)
    tk_a = MemberMemoryToolkit(
        member_name="m1",
        team_name="same_team",
        workspace=wa,
        scenario="general",
    )
    tk_b = MemberMemoryToolkit(
        member_name="m2",
        team_name="same_team",
        workspace=wb,
        scenario="general",
    )

    assert await tk_a.initialize()
    assert await tk_b.initialize()

    assert tk_a.manager is not None and tk_b.manager is not None
    assert id(tk_a.manager) != id(tk_b.manager)

    marker = Path(root_a) / "memory" / "m1_exclusive.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("only-a", encoding="utf-8")

    other_path = Path(root_b) / "memory" / "m1_exclusive.txt"
    assert not other_path.is_file()

    await tk_a.close()
    await tk_b.close()
