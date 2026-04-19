# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Revert-on-failure rail — tracks base commit for revert."""
from __future__ import annotations

import asyncio
import logging

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)


class RevertOnFailureRail(DeepAgentRail):
    """Track the base commit SHA for revert capability.

    Captures HEAD before each task iteration so the
    Orchestrator can revert on failure.
    """

    def __init__(self) -> None:
        super().__init__()
        self._base_sha: str = ""

    def set_base_commit(self, sha: str) -> None:
        """Record the commit to revert to.

        Args:
            sha: Git commit SHA.
        """
        self._base_sha = sha
        logger.debug("Base commit set to %s", sha)

    @property
    def base_commit(self) -> str:
        """Return the current base commit SHA."""
        return self._base_sha

    async def before_task_iteration(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
    ) -> None:
        """Capture current HEAD as base commit."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0 and stdout:
                sha = stdout.decode().strip()
                self.set_base_commit(sha)
        except FileNotFoundError:
            logger.debug("git not found, skip capture")

    async def revert(self, workspace: str) -> bool:
        """Revert to the base commit.

        Args:
            workspace: Working directory for git.

        Returns:
            True if revert succeeded.
        """
        if not self._base_sha:
            logger.warning("No base commit to revert to")
            return False

        logger.info(
            "Reverting to base commit %s", self._base_sha,
        )
        proc = await asyncio.create_subprocess_exec(
            "git", "reset", "--hard", self._base_sha,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(
                "git reset failed: %s",
                stderr.decode(errors="replace"),
            )
            return False
        return True
