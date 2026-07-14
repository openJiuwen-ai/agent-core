# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local transport: launch the CLI as a local subprocess.

:class:`LocalTransport` wraps :func:`asyncio.create_subprocess_exec` — the
same local-fork path the spawn module used inline before the transport
abstraction existed. It returns a native :class:`asyncio.subprocess.Process`
which already satisfies :class:`ProcessLike` with zero adaptation, so the
runtime drives it exactly as before. ``aclose`` is a no-op: the process's own
lifetime is owned by the runtime (``ExternalCliRuntime.aclose`` terminates
it); a local fork has no connection to release.
"""

from __future__ import annotations

import asyncio
from typing import cast

from openjiuwen.agent_teams.external.cli_agent.transport.base import ProcessLike
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error


class LocalTransport:
    """Launch a CLI argv as a local subprocess; return a native Process."""

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ProcessLike:
        """Fork ``argv`` with pipes; return the native asyncio Process.

        Args:
            argv: Launch argv (binary + flags).
            env: Full environment for the subprocess.
            cwd: Working directory.

        Returns:
            The :class:`asyncio.subprocess.Process` (satisfies
            :class:`ProcessLike`).

        Raises:
            BaseError: ``AGENT_TEAM_EXECUTION_ERROR`` when the subprocess did
                not expose stdin/stdout pipes.
        """
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        if process.stdin is None or process.stdout is None:
            raise_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg=f"local CLI '{argv[0] if argv else ''}' did not expose stdin/stdout pipes",
            )
            raise AssertionError  # pragma: no cover - raise_error always raises
        return cast(ProcessLike, process)

    async def aclose(self) -> None:
        """No-op: a local subprocess's lifetime is owned by the runtime."""
        return None


__all__ = ["LocalTransport"]
