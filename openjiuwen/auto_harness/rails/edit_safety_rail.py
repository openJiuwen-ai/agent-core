# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Edit-safety rail — atomic change tracking + ruff check.

Merges the former ``AtomicChangeRail`` and
``EditCheckRail`` into a single rail.
"""
from __future__ import annotations

import asyncio
import logging

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)

_WRITE_TOOLS = frozenset({"write_file", "edit_file"})


class EditSafetyRail(DeepAgentRail):
    """Track edited files + run ruff after Python writes.

    - ``after_tool_call``: records edited files, warns
      if count exceeds limit, runs ``ruff check`` on
      Python files.

    Args:
        max_files: Maximum source files before warning.
    """

    def __init__(self, max_files: int = 3) -> None:
        super().__init__()
        self._max_files = max_files
        self._edited_files: set[str] = set()

    async def after_tool_call(
        self,
        ctx: AgentCallbackContext,
    ) -> None:
        """Record edit, check file count, run ruff."""
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        if inputs.tool_name not in _WRITE_TOOLS:
            return

        args = inputs.tool_args or {}
        file_path: str = args.get("file_path", "")
        if not file_path:
            return

        # -- Atomic change tracking -----------------------
        self._edited_files.add(file_path)
        count = len(self._edited_files)
        if count > self._max_files:
            logger.warning(
                "Atomic change limit exceeded: "
                "%d files (max %d)",
                count,
                self._max_files,
            )
            ctx.push_steering(
                f"You have modified {count} files "
                f"(limit is {self._max_files}). "
                "Keep changes minimal and focused."
            )

        # -- Ruff check -----------------------------------
        if file_path.endswith(".py"):
            await self._run_ruff(ctx, file_path)

    def reset(self) -> None:
        """Clear tracked files between tasks."""
        self._edited_files.clear()

    def edited_files(self) -> list[str]:
        """Return tracked edited files in stable order."""
        return sorted(self._edited_files)

    @staticmethod
    async def _run_ruff(
        ctx: AgentCallbackContext,
        file_path: str,
    ) -> None:
        """Run ruff check on a Python file."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ruff", "check", file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0 and stdout:
                output = stdout.decode(
                    errors="replace",
                )
                logger.info(
                    "ruff check failed for %s",
                    file_path,
                )
                ctx.push_steering(
                    f"ruff check found issues in "
                    f"'{file_path}':\n{output}\n"
                    "Please fix these issues."
                )
        except FileNotFoundError:
            logger.debug(
                "ruff not found, skipping check",
            )
