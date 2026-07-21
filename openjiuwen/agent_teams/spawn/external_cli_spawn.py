# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Spawn an external CLI agent as an in-process team member.

Mirrors :func:`inprocess_spawn`, but the member's brain is a third-party CLI
subprocess driven by an ``ExternalCliRuntime`` instead of a local DeepAgent.
The TeamAgent shell (coordination, mailbox, member handle) runs in the
current process; the CLI binary is the only separate process. The runtime is
built before ``configure`` (the subprocess launch is async) and injected so
the configurator skips DeepAgent / rail / memory setup.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.agent_teams.external.cli_agent.backends import backend_for
from openjiuwen.agent_teams.external.cli_agent.spawn import build_cli_runtime
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.prompts import build_team_member_system_prompt
from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import TeamAgentSpec, TeamRuntimeContext


async def _build_member_system_prompt(
    team_agent: "TeamAgent",
    spec: "TeamAgentSpec",
    ctx: "TeamRuntimeContext",
    member_name: str | None,
) -> str | None:
    """Build the external CLI member's system prompt from team-rail sections.

    Gives the member the same team sections an in-process DeepAgent member gets
    (role / workflow / lifecycle / private prompt / ...), built the same way, but
    excluding the other DeepAgent rails (safety, workspace, memory, ...) that
    do not apply to a CLI whose brain is not a local DeepAgent.

    Args:
        team_agent: The leader TeamAgent (source of the team backend roster).
        spec: The team spec carrying lifecycle / teammate_mode / team_mode /
            dispatch_mode.
        ctx: The external CLI member's runtime context (role / desc / language).
        member_name: The member's semantic identifier.

    Returns:
        The rendered system prompt, or ``None`` when no section had content.
    """
    from openjiuwen.agent_teams.agent.agent_configurator import _resolve_team_mode

    language = (ctx.team_spec.language if ctx.team_spec else None) or "cn"
    backend = team_agent.team_backend
    hitt_enabled = backend.hitt_enabled() if backend is not None else False
    prompt = build_team_member_system_prompt(
        role=ctx.role,
        member_prompt=ctx.prompt,
        member_name=member_name,
        lifecycle=spec.lifecycle,
        teammate_mode=spec.teammate_mode,
        team_mode=_resolve_team_mode(spec),
        dispatch_mode=spec.dispatch_mode,
        language=language,
        hitt_enabled=hitt_enabled,
        expose_human_agents_to_teammates=spec.expose_human_agents_to_teammates,
        workspace_prompt_variant="external",
    )
    return prompt or None


def _path_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def _append_extra_dir(result: list[str], path: str | None, *, cwd: str | None) -> None:
    if path is None:
        return
    if cwd is not None and _same_path(path, cwd):
        return
    if any(_same_path(path, existing) for existing in result):
        return
    result.append(path)


def _team_workspace_path(spec: "TeamAgentSpec", team_name: str) -> str:
    workspace = spec.workspace
    if workspace is not None and workspace.root_path:
        return workspace.root_path
    return str(team_home(team_name) / "team-workspace")


def _build_context_project_dir(spec: "TeamAgentSpec") -> str | None:
    build_context = spec.build_context
    if build_context is None:
        return None
    return _path_value(getattr(build_context, "project_dir", None))


def _resolve_external_paths(
    spec: "TeamAgentSpec",
    ctx: "TeamRuntimeContext",
    *,
    configured_cwd: str | None,
    team_name: str,
) -> tuple[str, tuple[str, ...]]:
    """Resolve cwd and extra Claude-accessible directories for an external member."""
    explicit_cwd = _path_value(configured_cwd)
    worktree_path = _path_value(ctx.worktree_path)
    project_dir = _build_context_project_dir(spec)
    team_workspace = _team_workspace_path(spec, team_name)
    cwd = explicit_cwd or worktree_path or project_dir or team_workspace

    extra_dirs: list[str] = []
    for path in (explicit_cwd, worktree_path, project_dir, team_workspace):
        _append_extra_dir(extra_dirs, path, cwd=cwd)
    return cwd, tuple(extra_dirs)


async def external_cli_spawn(
    team_agent: "TeamAgent",
    ctx: "TeamRuntimeContext",
    *,
    initial_message: Optional[str] = None,
    session_id: Optional[str] = None,
    resume_external_backend: bool = False,
) -> InProcessSpawnHandle:
    """Launch the CLI for ``ctx.cli_agent`` and run it as a team member.

    Args:
        team_agent: The leader TeamAgent that owns the team spec.
        ctx: Runtime context for the external CLI member.
        initial_message: First prompt delivered to the CLI.
        session_id: Session id propagated via contextvars.
        resume_external_backend: Whether the backend should resume its derived
            native session instead of starting a fresh one.

    Returns:
        An :class:`InProcessSpawnHandle` wrapping the member task.
    """
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent as _TeamAgent
    from openjiuwen.agent_teams.context import set_session_id
    from openjiuwen.core.runner.runner import Runner
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    spec = team_agent.spec
    team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
    member_name = ctx.member_name
    card_id = f"{team_name}_{member_name}" if member_name else "unknown"
    card = AgentCard(
        id=card_id,
        name=member_name or "unknown",
        description=f"External CLI member: {ctx.desc}" if ctx.desc else "External CLI member",
    )

    # Build the member's system prompt from the team-rail sections (the same
    # sections an in-process member gets), excluding the other DeepAgent rails.
    system_prompt = await _build_member_system_prompt(team_agent, spec, ctx, member_name)
    backend = backend_for(ctx.cli_agent) if ctx.cli_agent else None

    # Resolve the static launch config declared on the spec for this CLI kind.
    # The member was registered through ``spawn_external_cli_agent`` which
    # already validated a matching entry exists; fall back to defaults if it
    # is somehow absent so the launch still produces a usable runtime.
    cli_cfg = None
    for entry in spec.external_cli_agents:
        if entry.cli_agent == ctx.cli_agent:
            cli_cfg = entry
            break

    if cli_cfg is not None:
        cwd, add_dirs = _resolve_external_paths(
            spec,
            ctx,
            configured_cwd=cli_cfg.cwd,
            team_name=team_name,
        )
        runtime = await build_cli_runtime(
            ctx,
            cwd=cwd,
            add_dirs=add_dirs,
            command_override=tuple(cli_cfg.command) if cli_cfg.command else None,
            inject_mcp=cli_cfg.inject_mcp,
            mcp_server_command=tuple(cli_cfg.mcp_server_command),
            system_prompt=system_prompt,
            extra_env=cli_cfg.env or None,
            ssh_transport=cli_cfg.ssh_transport,
            resume_external_backend=resume_external_backend,
        )
    else:
        cwd, add_dirs = _resolve_external_paths(
            spec,
            ctx,
            configured_cwd=None,
            team_name=team_name,
        )
        runtime = await build_cli_runtime(
            ctx,
            cwd=cwd,
            add_dirs=add_dirs,
            system_prompt=system_prompt,
            resume_external_backend=resume_external_backend,
        )

    teammate = _TeamAgent(card)
    teammate.configure(spec, ctx, member_runtime=runtime)

    base_query = initial_message or ""
    # Backends that accept the system prompt as a launch arg already carry it;
    # others get it prepended to their first user message.
    has_launch_prompt = bool(base_query and system_prompt)
    needs_prompt_prepend = backend is not None and not backend.injects_system_prompt_via_arg
    if has_launch_prompt and needs_prompt_prepend:
        query = f"{system_prompt}\n\n---\n\n{base_query}"
    else:
        query = base_query
    inputs: dict[str, Any] = {"query": query}
    run_ctx = contextvars.copy_context()

    async def _run() -> Any:
        if session_id:
            set_session_id(session_id)
        team_logger.info("[external-cli] member {} started", member_name)
        try:
            return await Runner.run_agent_team(teammate, inputs, member=True, session=session_id)
        except asyncio.CancelledError:
            team_logger.info("[external-cli] member {} cancelled", member_name)
            raise
        except Exception:
            team_logger.error("[external-cli] member {} crashed", member_name, exc_info=True)
            raise
        finally:
            await runtime.stop()

    task = run_ctx.run(asyncio.get_running_loop().create_task, _run())
    handle = InProcessSpawnHandle(
        process_id=f"extcli-{member_name}",
        _task=task,
        agent_ref=teammate,
    )
    team_logger.info("[external-cli] spawned member {} as {}", member_name, handle.process_id)
    return handle


__all__ = ["external_cli_spawn"]
