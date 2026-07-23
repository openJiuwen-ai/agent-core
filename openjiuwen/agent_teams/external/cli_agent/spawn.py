# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Launch a third-party CLI as a subprocess and wrap it in a runtime.

Builds the team-join descriptor from the member's runtime context, launches
the CLI with that descriptor in its environment (so a team-member MCP server
the CLI spawns inherits it), and returns a CLI member runtime.

An external CLI member runs in a separate process. Its MCP server writes the
shared file-backed sqlite database directly and publishes runtime events via
either the configured team messenger or a Gateway WebSocket relay.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import AsyncIterator

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.external.cli_agent.adapters import CliAgentAdapter, build_adapter
from openjiuwen.agent_teams.external.cli_agent.claude import build_claude_runtime
from openjiuwen.agent_teams.external.cli_agent.claude.options import strip_parent_claude_env
from openjiuwen.agent_teams.external.cli_agent.injector import StdinPipeInjector
from openjiuwen.agent_teams.external.cli_agent.transport.base import StreamReaderLike
from openjiuwen.agent_teams.external.cli_agent.transport.local import LocalTransport
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.agent_teams.external.runtime import CliRuntimeBase, ExternalCliRuntime, ReinvokeCliRuntime
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
from openjiuwen.agent_teams.team_workspace.models import TeamWorkspaceConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger


def descriptor_from_context(ctx: TeamRuntimeContext) -> TeamJoinDescriptor:
    """Build a join descriptor an external CLI member uses to reach the team."""
    member_name = ctx.member_name
    if not member_name:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="external CLI member requires a member_name in its runtime context",
        )
    team_spec = ctx.team_spec
    if team_spec is None or not team_spec.team_name:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="external CLI member requires a team spec with team_name",
        )
    team_name = team_spec.team_name
    language = team_spec.language or "cn"
    dispatch_mode = team_spec.dispatch_mode or "autonomous"
    teammate_mode = team_spec.teammate_mode or "build_mode"
    session_id = get_session_id()
    if not session_id:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="external CLI member requires an active team session_id",
        )
    transport = team_spec.external_messager_config or ctx.messager_config or MessagerTransportConfig()
    transport_updates = {"team_name": team_name, "node_id": member_name}
    if transport.backend == "pyzmq" and transport.direct_addr:
        transport_updates["direct_addr"] = "tcp://127.0.0.1:*"
    transport = transport.model_copy(update=transport_updates)

    workspace_config = None
    if team_spec.workspace:
        candidate_workspace = TeamWorkspaceConfig.model_validate(team_spec.workspace)
        if candidate_workspace.enabled:
            workspace_config = candidate_workspace
    workspace_path = None
    if workspace_config is not None:
        workspace_path = workspace_config.root_path or str(team_home(team_name) / "team-workspace")

    return TeamJoinDescriptor(
        session_id=session_id,
        team_name=team_name,
        member_name=member_name,
        role=ctx.role.value,
        # A spawned third-party CLI is a first-class team member, not an
        # external operator: it gets the native teammate tool set and its
        # team system prompt is injected here at spawn time.
        scope="member",
        language=language,
        # The CLI's tools (MCP server -> ExternalTeamClient -> create_team_tools)
        # and its system prompt (rendered at spawn) are separate chains; both
        # must resolve against the same mode axes or the member gets a prompt
        # describing tools it does not have.
        dispatch_mode=dispatch_mode,
        teammate_mode=teammate_mode,
        db_config=ctx.db_config,
        transport_config=transport,
        workspace_config=workspace_config,
        workspace_path=workspace_path,
    )


async def _aiter_stdout(stream: StreamReaderLike) -> AsyncIterator[str]:
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
    add_dirs: tuple[str, ...] = (),
    command_override: tuple[str, ...] | None = None,
    inject_mcp: bool = True,
    mcp_server_name: str = "openjiuwen-team",
    mcp_server_command: tuple[str, ...] = ("openjiuwen-team-mcp",),
    system_prompt: str | None = None,
    extra_env: dict[str, str] | None = None,
    ssh_transport: SshTransportConfig | None = None,
    resume_external_backend: bool = False,
) -> CliRuntimeBase:
    """Build the member runtime for ``ctx.cli_agent``.

    Claude is handled by its dedicated SDK backend. Other CLI agents are
    picked by the adapter's ``supports_stdin_injection``: streaming CLIs launch
    one long-lived subprocess and return an :class:`ExternalCliRuntime`;
    one-shot CLIs (openclaw / hermes) return a :class:`ReinvokeCliRuntime` that
    launches a fresh subprocess per turn. The returned runtime owns its
    subprocess(es); ``aclose`` tears down.

    Args:
        ctx: Member runtime context; ``ctx.cli_agent`` names the backend.
        cwd: Working directory for the subprocess(es).
        add_dirs: Extra directories exposed to SDK backends that support them.
        command_override: Optional full launch argv (e.g. an absolute path).
        inject_mcp: When True (default), configure the backend to register the
            team MCP server so the CLI gets the team collaboration tools.
            Adapter-backed CLIs without an injection strategy ignore this.
            Only the streaming adapter path injects at launch; one-shot CLIs
            register their MCP server out of band.
        mcp_server_name: Logical name the CLI registers the MCP server under.
        mcp_server_command: Launch argv for the team MCP stdio server.
        system_prompt: The member's team-rail system prompt. Claude receives it
            through SDK options; other CLIs may receive it as a launch arg.
            CLIs without a flag get it prepended to their first user message by
            the caller, so it is ignored here for them.
        extra_env: Extra environment merged over the inherited env + the
            team-join descriptor (descriptor wins is not desired, so this is
            applied last only for non-descriptor keys).
        ssh_transport: Optional ssh endpoint config. Currently supported only
            by the Claude SDK backend.
        resume_external_backend: When True, resume the derived backend session
            instead of starting it as a fresh session.
    """
    if not ctx.cli_agent:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="build_cli_runtime called without ctx.cli_agent set",
        )
    descriptor = descriptor_from_context(ctx)
    if not descriptor.session_id:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="external CLI runtime requires session_id in context",
        )
    if ctx.cli_agent == "claude":
        if command_override is not None:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="Claude SDK members do not support command_override; configure Claude on the local/remote PATH",
            )
        if ssh_transport is None:
            base_env = strip_parent_claude_env(dict(os.environ))
        else:
            base_env = {}
        env = {**base_env, **(extra_env or {}), **descriptor.to_env()}
        return build_claude_runtime(
            member_name=ctx.member_name or "",
            cwd=cwd,
            add_dirs=add_dirs,
            env=env,
            inject_mcp=inject_mcp,
            mcp_server_name=mcp_server_name,
            mcp_server_command=mcp_server_command,
            system_prompt=system_prompt,
            ssh_transport=ssh_transport,
            team_session_id=descriptor.session_id,
            resume_external_backend=resume_external_backend,
        )
    if ssh_transport is not None:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="ssh transport is only supported for claude SDK external CLI members",
        )

    adapter: CliAgentAdapter = build_adapter(ctx.cli_agent, command_override=command_override)
    # Start from the inherited environment minus any parent agent-session
    # markers declared by the adapter. The descriptor env is authoritative for
    # team identity, so it is applied last — a misconfigured extra_env cannot
    # shadow the join.
    base_env = {
        key: value
        for key, value in os.environ.items()
        if not any(key.startswith(prefix) for prefix in adapter.env_strip_prefixes)
    }
    env = {**base_env, **(extra_env or {}), **descriptor.to_env()}

    # System prompt as a launch arg. CLIs without a flag return [] here and get
    # the prompt prepended to their first user message by the caller instead.
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
    transport = LocalTransport()
    process = await transport.run(tuple(command), env=env, cwd=cwd)
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
        transport=transport,
    )


__all__ = ["build_cli_runtime", "descriptor_from_context"]
