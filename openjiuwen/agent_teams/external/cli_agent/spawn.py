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
import uuid
from typing import AsyncIterator

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.external.cli_agent.adapters import CliAgentAdapter, build_adapter
from openjiuwen.agent_teams.external.cli_agent.injector import StdinPipeInjector
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.agent_teams.external.runtime import ExternalCliRuntime, ReinvokeCliRuntime
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
    transport = ctx.messager_config or MessagerTransportConfig()
    # The in-process member shell already binds this member's ``direct_addr``
    # ROUTER. The external client (the CLI's MCP server, a separate process)
    # only publishes events to the pub/sub bus and reads the shared db — it
    # never receives direct ROUTER messages — so hand it an ephemeral
    # ``direct_addr`` to avoid colliding with the shell's bind on the same
    # port. Pub/sub publish/subscribe still target the leader's broker.
    if transport.direct_addr:
        transport = transport.model_copy(update={"direct_addr": "tcp://127.0.0.1:*"})
    return TeamJoinDescriptor(
        session_id=get_session_id() or "",
        team_name=team_name,
        member_name=ctx.member_name or "",
        role=ctx.role.value,
        language=language,
        db_config=ctx.db_config,
        transport_config=transport,
    )


async def _aiter_stdout(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """Yield decoded, newline-stripped stdout lines until the stream ends."""
    while True:
        raw = await stream.readline()
        if not raw:
            return
        yield raw.decode("utf-8", errors="replace").rstrip("\n")


async def _register_mcp_out_of_band(
    adapter: CliAgentAdapter,
    *,
    server_name: str,
    server_command: tuple[str, ...],
    env: dict[str, str],
    cwd: str | None,
    member_name: str,
) -> None:
    """Register the team MCP server with a CLI that has no launch-inject flag.

    Some CLIs (gemini, hermes) register MCP servers via a subcommand that
    persists to their own config rather than a launch flag. Runs that command
    once (best-effort) so the member still gets team tools. When the adapter
    has no registration mechanism either, logs a loud warning instead of
    silently leaving the member without team tools.
    """
    register_cmd = adapter.mcp_register_command(server_name=server_name, server_command=server_command)
    if register_cmd is None:
        team_logger.warning(
            "[external-cli] {} cannot auto-inject the team MCP server (no launch flag or "
            "registration command); member {} will lack team tools unless registered out of band",
            adapter.name,
            member_name,
        )
        return
    team_logger.info("[external-cli] registering team MCP for member {} via {}", member_name, register_cmd)
    try:
        proc = await asyncio.create_subprocess_exec(
            *register_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        _, stderr = await proc.communicate()
    except (OSError, ValueError) as exc:
        team_logger.warning("[external-cli] team MCP registration for {} failed to launch: {}", adapter.name, exc)
        return
    if proc.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-500:]
        team_logger.warning(
            "[external-cli] team MCP registration for {} exited {}: {}",
            adapter.name,
            proc.returncode,
            tail,
        )


async def build_cli_runtime(
    ctx: TeamRuntimeContext,
    *,
    cwd: str | None = None,
    command_override: tuple[str, ...] | None = None,
    inject_mcp: bool = True,
    mcp_server_name: str = "openjiuwen-team",
    mcp_server_command: tuple[str, ...] = ("openjiuwen-team-mcp",),
    system_prompt: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> ExternalCliRuntime | ReinvokeCliRuntime:
    """Build the member runtime for ``ctx.cli_agent``.

    Picks the runtime by the adapter's ``supports_stdin_injection``:
    streaming CLIs (claude / codex) launch one long-lived subprocess now and
    return an :class:`ExternalCliRuntime`; one-shot CLIs (openclaw / hermes)
    return a :class:`ReinvokeCliRuntime` that launches a fresh subprocess per
    turn. The returned runtime owns its subprocess(es); ``aclose`` tears down.

    Args:
        ctx: Member runtime context; ``ctx.cli_agent`` names the adapter.
        cwd: Working directory for the subprocess(es).
        command_override: Optional full launch argv (e.g. an absolute path).
        inject_mcp: When True (default), append the adapter's MCP-server
            registration args so the CLI starts the team MCP server and gets
            the team collaboration tools. Adapters without an injection
            strategy ignore this. Only the streaming path injects; one-shot
            CLIs register their MCP server out of band.
        mcp_server_name: Logical name the CLI registers the MCP server under.
        mcp_server_command: Launch argv for the team MCP stdio server.
        system_prompt: The member's team-rail system prompt. Passed as a launch
            arg for CLIs that support it (claude ``--append-system-prompt``);
            CLIs without a flag get it prepended to their first user message by
            the caller, so it is ignored here for them.
        extra_env: Extra environment merged over the inherited env + the
            team-join descriptor (descriptor wins is not desired, so this is
            applied last only for non-descriptor keys).
    """
    if not ctx.cli_agent:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="build_cli_runtime called without ctx.cli_agent set",
        )
    adapter: CliAgentAdapter = build_adapter(ctx.cli_agent, command_override=command_override)
    descriptor = descriptor_from_context(ctx)
    # Start from the inherited environment minus any parent agent-session
    # markers (e.g. CLAUDECODE / CLAUDE_CODE_* when the team itself runs inside
    # a Claude Code session) so the spawned CLI is a fresh, independent
    # instance rather than a nested one. The descriptor env is authoritative
    # for team identity, so it is applied last — a misconfigured extra_env
    # cannot shadow the join.
    base_env = {
        key: value
        for key, value in os.environ.items()
        if not any(key.startswith(prefix) for prefix in adapter.env_strip_prefixes)
    }
    env = {**base_env, **(extra_env or {}), **descriptor.to_env()}

    # System prompt as a launch arg (claude --append-system-prompt). CLIs
    # without a flag return [] here and get the prompt prepended to their first
    # user message by the caller instead.
    sp_args = tuple(adapter.system_prompt_args(system_prompt or ""))

    mcp_args: tuple[str, ...] = ()
    if inject_mcp:
        mcp_args = tuple(
            adapter.mcp_launch_args(server_name=mcp_server_name, server_command=mcp_server_command)
        )
        if not mcp_args:
            # No launch-injection flag for this CLI: register the team MCP
            # server out of band (e.g. `gemini mcp add`) so the member still
            # gets team tools, or warn loudly when nothing can register it.
            await _register_mcp_out_of_band(
                adapter,
                server_name=mcp_server_name,
                server_command=mcp_server_command,
                env=env,
                cwd=cwd,
                member_name=ctx.member_name or "",
            )

    launch_extra_args = mcp_args + sp_args

    if not adapter.supports_stdin_injection:
        # One-shot CLI: no eager launch; the runtime spawns per turn and adds
        # the MCP-registration args to each invocation. A canonical UUID is the
        # member's stable session id across turns (CLIs that resume by id, e.g.
        # gemini ``--session-id`` / ``--resume``, require a real UUID).
        return ReinvokeCliRuntime(
            member_name=ctx.member_name or "",
            adapter=adapter,
            env=env,
            cwd=cwd,
            cli_session_id=str(uuid.uuid4()),
            launch_extra_args=launch_extra_args,
        )

    command = adapter.build_command(extra_args=launch_extra_args)
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

    return ExternalCliRuntime(
        member_name=ctx.member_name or "",
        adapter=adapter,
        injector=StdinPipeInjector(process.stdin),
        output_lines=_aiter_stdout(process.stdout),
        process=process,
    )


__all__ = ["build_cli_runtime", "descriptor_from_context"]
