# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_workspace_cloner — WorkspaceCloner 单元测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from openjiuwen.auto_harness.infra.workspace_cloner import (
    ClonedWorkspace,
    WorkspaceCloner,
)


class TestWorkspaceCloner(IsolatedAsyncioTestCase):
    async def test_clone_n_creates_isolated_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "workspace"
            original.mkdir()
            (original / "file.txt").write_text("hello")
            sub = original / "subdir"
            sub.mkdir()
            (sub / "nested.txt").write_text("world")

            cloner = WorkspaceCloner()
            clones = cloner.clone_n(original, n=3)

            assert len(clones) == 3
            for i, cloned in enumerate(clones):
                assert cloned.index == i
                assert cloned.original == original.resolve()
                assert cloned.path.exists()
                assert (cloned.path / "file.txt").read_text() == "hello"
                assert (cloned.path / "subdir" / "nested.txt").read_text() == "world"
                # Verify isolation
                (cloned.path / "file.txt").write_text(f"modified-{i}")

            # Original unchanged
            assert (original / "file.txt").read_text() == "hello"

            # Cleanup
            for cloned in clones:
                cloner.remove(cloned)
                assert not cloned.path.exists()

    async def test_clone_n_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "workspace"
            original.mkdir()
            (original / "file.txt").write_text("v2")

            # Pre-create clone
            pre_existing = Path(tmp) / "workspace-attempt-000"
            pre_existing.mkdir()
            (pre_existing / "stale.txt").write_text("old")

            cloner = WorkspaceCloner()
            clones = cloner.clone_n(original, n=1)

            assert len(clones) == 1
            assert (clones[0].path / "file.txt").read_text() == "v2"
            assert not (clones[0].path / "stale.txt").exists()

            cloner.remove(clones[0])

    async def test_clone_n_async(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "workspace"
            original.mkdir()
            (original / "a.txt").write_text("a")

            cloner = WorkspaceCloner()
            clones = await cloner.clone_n_async(original, n=2)

            assert len(clones) == 2
            for cloned in clones:
                assert (cloned.path / "a.txt").read_text() == "a"
                cloner.remove(cloned)

    async def test_promote_replaces_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "workspace"
            original.mkdir()
            (original / "file.txt").write_text("original")

            cloner = WorkspaceCloner()
            clones = cloner.clone_n(original, n=1)
            clone = clones[0]
            (clone.path / "file.txt").write_text("best")
            (clone.path / "new.txt").write_text("extra")

            cloner.promote(clone)

            assert (original / "file.txt").read_text() == "best"
            assert (original / "new.txt").read_text() == "extra"
            assert not clone.path.exists()  # cloned dir removed after promotion

    async def test_remove_original_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "workspace"
            original.mkdir()
            (original / "file.txt").write_text("preserve")

            cloner = WorkspaceCloner()
            cloned = ClonedWorkspace(
                path=original.resolve(),
                original=original.resolve(),
                index=0,
            )
            cloner.remove(cloned)
            assert original.exists()
            assert (original / "file.txt").read_text() == "preserve"
