# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Workspace cloner — creates N isolated copies of a workspace for parallel attempts."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class ClonedWorkspace:
    """A cloned workspace with its original base path."""

    path: Path
    original: Path
    index: int


class WorkspaceCloner:
    """Clone a workspace N times using shutil.copytree.

    Works with any directory tree (git repo or plain files).
    Each clone gets a serial suffix so it is independently mutable.
    """

    def __init__(
        self,
        suffix_template: str = "attempt-{index:03d}",
        ignore_patterns: list[str] | None = None,
    ) -> None:
        self._suffix = suffix_template
        self._ignore = shutil.ignore_patterns(
            *(ignore_patterns or [".git", "__pycache__", "*.pyc", ".pytest_cache"]),
        )

    def clone_n(
        self,
        workspace: Path | str,
        n: int,
        parent_dir: Path | str | None = None,
    ) -> list[ClonedWorkspace]:
        """Create *n* copies of *workspace*.

        Args:
            workspace: Original workspace path.
            n: Number of clones to create.
            parent_dir: Directory to place clones in.  Defaults to
                ``workspace.parent``.

        Returns:
            List of ``ClonedWorkspace`` ordered by index.
        """
        original = Path(workspace).resolve()
        parent = Path(parent_dir) if parent_dir else original.parent
        parent.mkdir(parents=True, exist_ok=True)

        clones: list[ClonedWorkspace] = []
        for i in range(n):
            suffix = self._suffix.format(index=i)
            clone_path = parent / f"{original.name}-{suffix}"
            if clone_path.exists():
                shutil.rmtree(clone_path)
            shutil.copytree(
                original,
                clone_path,
                ignore=self._ignore,
            )
            clones.append(
                ClonedWorkspace(
                    path=clone_path,
                    original=original,
                    index=i,
                ),
            )
            logger.debug(
                "[WorkspaceCloner] Cloned %s → %s", original, clone_path,
            )
        return clones

    async def clone_n_async(
        self,
        workspace: Path | str,
        n: int,
        parent_dir: Path | str | None = None,
        executor: Callable | None = None,
    ) -> list[ClonedWorkspace]:
        """Async wrapper around :meth:`clone_n`.

        Runs the blocking ``shutil.copytree`` calls in a thread-pool
        so the event loop is not blocked.
        """
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            executor,
            self.clone_n,
            workspace,
            n,
            parent_dir,
        )

    def remove(self, cloned: ClonedWorkspace) -> None:
        """Delete a cloned workspace.  Safe to call on originals (no-op)."""
        if cloned.path == cloned.original:
            return
        if cloned.path.exists():
            shutil.rmtree(cloned.path)
            logger.debug(
                "[WorkspaceCloner] Removed %s", cloned.path,
            )

    def promote(
        self,
        cloned: ClonedWorkspace,
    ) -> None:
        """Replace the original workspace with the best clone.

        The original directory is backed up as
        ``<original>.backup-<timestamp>`` before being overwritten.
        """
        import time
        original = cloned.original
        backup = original.parent / f"{original.name}.backup-{int(time.time())}"
        if original.exists():
            shutil.move(str(original), str(backup))
            shutil.copytree(
                cloned.path,
                original,
                ignore=self._ignore,
            )
            # Remove the cloned source after successful promotion
            if cloned.path.exists() and cloned.path != cloned.original:
                try:
                    shutil.rmtree(cloned.path)
                except PermissionError:
                    logger.warning(
                        "[WorkspaceCloner] Could not remove clone "
                        "%s (in use); will be cleaned up later",
                        cloned.path,
                    )
            logger.info(
            "[WorkspaceCloner] Promoted %s → %s (backup=%s)",
            cloned.path,
            original,
            backup,
        )
