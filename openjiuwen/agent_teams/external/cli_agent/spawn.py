# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Launch a third-party CLI as a subprocess and wrap it in a runtime.

Builds the team-join descriptor from the member's runtime context, launches
the CLI with that descriptor in its environment (so a team-member MCP server
the CLI spawns inherits it), and returns an :class:`ExternalCliRuntime`
bound to the subprocess via stdin (input) and a stdout line iterator.

Note: an external CLI member runs in a separate process, so the team must
use a cross-process transport (``pyzmq`` messager + file-backed sqlite) for
the CLI's tool calls to reach the team. The in-process backends are
single-process only.
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.external.cli_agent.adapters import CliAgentAdapter, build_adapter
from openjiuwen.agent_teams.external.cli_agent.injector import StdinPipeInjector
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.agent_teams.external.runtime import ExternalCliRuntime
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger


def descriptor_from_context(ctx: TeamRuntimeContext) -> TeamJoinDescriptor:
    """Build a join descriptor an external CLI member uses to reach the team."""
    if not ctx.member_name:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="external CLI member requires a member_name in its runtime context",
        )
    team_name = ctx.team_spec.team_name if ctx.team_spec else ""
    language = (ctx.team_spec.language if ctx.team_spec else None) or "cn"
    return TeamJoinDescriptor(
        session_id=get_session_id() or "",
        team_name=team_name,
        member_name=ctx.member_name or "",
        role=ctx.role.value,
        language=language,
        db_config=ctx.db_config,
        transport_config=ctx.messager_config or MessagerTransportConfig(),
    )


async def _aiter_stdout(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """Yield decoded, newline-stripped stdout lines until the stream ends."""
    while True:
        raw = await stream.readline()
        if not raw:
            return
        yield raw.decode("utf-8", errors="replace").rstrip("\n")


async def launch_external_cli(
    ctx: TeamRuntimeContext,
    *,
    cwd: str | None = None,
    command_override: tuple[str, ...] | None = None,
) -> tuple[ExternalCliRuntime, asyncio.subprocess.Process]:
    """Spawn the CLI for ``ctx.cli_agent`` and wrap it in an ExternalCliRuntime.

    Args:
        ctx: Member runtime context; ``ctx.cli_agent`` names the adapter.
        cwd: Working directory for the subprocess.
        command_override: Optional full launch argv (e.g. an absolute path).

    Returns:
        The runtime plus the live subprocess (caller owns its teardown).
    """
    if not ctx.cli_agent:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="launch_external_cli called without ctx.cli_agent set",
        )
    adapter: CliAgentAdapter = build_adapter(ctx.cli_agent, command_override=command_override)
    descriptor = descriptor_from_context(ctx)

    env = {**os.environ, **descriptor.to_env()}
    command = adapter.build_command()
    team_logger.info("[external-cli] launching {} for member {}", command, ctx.member_name)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    if process.stdin is None or process.stdout is None:
        raise_error(
            StatusCode.AGENT_TEAM_EXECUTION_ERROR,
            error_msg=f"external CLI '{ctx.cli_agent}' did not expose stdin/stdout pipes",
        )

    runtime = ExternalCliRuntime(
        member_name=ctx.member_name or "",
        adapter=adapter,
        injector=StdinPipeInjector(process.stdin),
        output_lines=_aiter_stdout(process.stdout),
    )
    return runtime, process


__all__ = ["descriptor_from_context", "launch_external_cli"]
