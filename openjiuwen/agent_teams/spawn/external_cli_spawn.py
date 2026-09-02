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
from openjiuwen.agent_teams.paths import team_workspace_dir
from openjiuwen.agent_teams.prompts import build_team_member_system_prompt
from openjiuwen.agent_teams.schema.team import ExternalCliModelConfig
from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import TeamAgentSpec, TeamRuntimeContext
    from openjiuwen.agent_teams.team_context import TeamContextTracker
    from openjiuwen.agent_teams.tools.team import TeamBackend


def _team_model_config_to_external(
    member_model: Any,
) -> Optional[ExternalCliModelConfig]:
    """Convert a pool-allocated TeamModelConfig to ExternalCliModelConfig."""
    client_config = getattr(member_model, "model_client_config", None)
    request_config = getattr(member_model, "model_request_config", None)
    if client_config is None:
        return None
    provider = str(getattr(client_config, "client_provider", "") or "")
    model = ""
    if request_config is not None:
        model = str(getattr(request_config, "model_name", "") or getattr(request_config, "model", "") or "")
    return ExternalCliModelConfig(
        provider=provider or None,
        model=model or None,
        api_base=str(getattr(client_config, "api_base", "") or "") or None,
        api_key=str(getattr(client_config, "api_key", "") or "") or None,
    )


async def _build_member_system_prompt(
    spec: "TeamAgentSpec",
    ctx: "TeamRuntimeContext",
    member_name: str | None,
    *,
    hitt_enabled: bool,
    ws_cache: Any = None,
) -> str | None:
    """Build the external CLI member's system prompt from team-rail sections.

    Gives the member the same team sections an in-process DeepAgent member gets
    (role / workflow / lifecycle / private prompt / ...), built the same way, but
    excluding the other DeepAgent rails (safety, workspace, memory, ...) that
    do not apply to a CLI whose brain is not a local DeepAgent.

    Args:
        spec: The team spec carrying lifecycle / teammate_mode / team_mode /
            dispatch_mode.
        ctx: The external CLI member's runtime context (role / desc / language).
        member_name: The member's semantic identifier.
        hitt_enabled: Effective HITT flag for the team instance.
        ws_cache: Optional team workspace cache. When the teammate shares the
            leader's cache (``share_workspace_cache_with``), pass it here so
            ``build_team_member_system_prompt`` reads evolved A-class templates
            (teammate_policy, etc.) through the cache-bound loader instead of
            the framework default. ``None`` keeps the framework read-only path.

    Returns:
        The rendered system prompt, or ``None`` when no section had content.
    """
    from openjiuwen.agent_teams.agent.agent_configurator import _resolve_team_mode
    from openjiuwen.agent_teams.prompts.loader import make_template_loader

    language = (ctx.team_spec.language if ctx.team_spec else None) or "cn"
    prompt = build_team_member_system_prompt(
        role=ctx.role,
        member_prompt=ctx.prompt,
        member_name=member_name,
        display_name=ctx.display_name or "",
        lifecycle=spec.lifecycle,
        teammate_mode=spec.teammate_mode,
        team_mode=_resolve_team_mode(spec),
        dispatch_mode=spec.dispatch_mode,
        language=language,
        hitt_enabled=hitt_enabled,
        expose_human_agents_to_teammates=spec.expose_human_agents_to_teammates,
        workspace_prompt_variant="external",
        loader=make_template_loader(ws_cache),
    )
    return prompt or None


def _build_team_context_tracker(
    team_backend: "TeamBackend | None",
    spec: "TeamAgentSpec",
    ctx: "TeamRuntimeContext",
    member_name: str | None,
    team_name: str,
) -> "TeamContextTracker":
    """Build the tracker feeding team state into this CLI member's messages.

    An external CLI has no rail, so the runtime folds the tracker's output into
    the next message it sends. Unlike an in-process member, an external CLI
    never has the team workspace mounted into its cwd (``setup_agent``
    short-circuits before ``mount_into_workspace`` is ever called), so any
    agent-relative mount string would be a path the member cannot reach. We
    therefore expose only the shared workspace's absolute path — the member
    writes there directly.

    Args:
        team_backend: The external member's own TeamBackend.
        spec: The team spec carrying workspace + HITT exposure config.
        ctx: The member's runtime context (role / private prompt / language).
        member_name: The member's semantic identifier.
        team_name: The team this member belongs to.

    Returns:
        A tracker scoped to this member.
    """
    from openjiuwen.agent_teams.team_context import TeamContextTracker

    language = (ctx.team_spec.language if ctx.team_spec else None) or "cn"
    workspace = spec.workspace
    workspace_enabled = workspace is not None and workspace.enabled
    return TeamContextTracker(
        team_backend=team_backend,
        member_name=member_name,
        role=ctx.role,
        display_name=ctx.display_name or "",
        member_prompt=ctx.prompt or "",
        team_workspace_path=_team_workspace_path(spec, team_name) if workspace_enabled else None,
        team_outputs_dir=_build_context_team_outputs_dir(spec),
        expose_human_agents_to_teammates=spec.expose_human_agents_to_teammates,
        language=language,
    )


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
    return str(team_workspace_dir(team_name))


def _build_context_project_dir(spec: "TeamAgentSpec") -> str | None:
    build_context = spec.build_context
    if build_context is None:
        return None
    return _path_value(getattr(build_context, "project_dir", None))


def _build_context_team_outputs_dir(spec: "TeamAgentSpec") -> str | None:
    build_context = spec.build_context
    if build_context is None:
        return None
    return _path_value(getattr(build_context, "team_outputs_dir", None))


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
    *,
    team_agent: "TeamAgent",
    spec: "TeamAgentSpec",
    ctx: "TeamRuntimeContext",
    hitt_enabled: bool,
    initial_message: Optional[str] = None,
    session_id: Optional[str] = None,
    resume_external_backend: bool = False,
) -> InProcessSpawnHandle:
    """Launch the CLI for ``ctx.cli_agent`` and run it as a team member.

    Args:
        team_agent: The leader TeamAgent that owns the team spec.
        spec: Team spec used to configure this external member shell.
        ctx: Runtime context for the external CLI member.
        hitt_enabled: Effective HITT flag from the caller's team instance.
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

    team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
    member_name = ctx.member_name
    card_id = f"{team_name}_{member_name}" if member_name else "unknown"
    card = AgentCard(
        id=card_id,
        name=member_name or "unknown",
        description=f"External CLI member: {ctx.desc}" if ctx.desc else "External CLI member",
    )

    backend = backend_for(ctx.cli_agent) if ctx.cli_agent else None
    # Build the member's system prompt from the team-rail sections (the same
    # sections an in-process member gets), excluding the other DeepAgent rails.
    # The leader's workspace cache is already built at this point (leader has
    # configured), so we can bind it into the loader — evolved A-class
    # templates (teammate_policy, ...) reach the CLI member's system prompt.
    leader_workspace_cache = team_agent.team_backend.workspace_cache if team_agent.team_backend is not None else None
    system_prompt = await _build_member_system_prompt(
        spec,
        ctx,
        member_name,
        hitt_enabled=hitt_enabled,
        ws_cache=leader_workspace_cache,
    )

    # Resolve the static launch config declared on the spec for this CLI kind.
    cli_cfg = None
    for entry in spec.external_cli_agents:
        if entry.cli_agent == ctx.cli_agent:
            cli_cfg = entry
            break

    # When the pool allocator assigned a model to this member, convert it to
    # an ExternalCliModelConfig, filtering by provider compatibility: Claude
    # needs Anthropic, Codex needs OpenAI-compatible. Falls back to the static
    # spec config when no pool allocation or no provider match exists.
    external_model_config = cli_cfg.external_model_config if cli_cfg is not None else None
    fallback_external_model_config = None
    if ctx.member_model is not None:
        pool_model_config = _team_model_config_to_external(ctx.member_model)
        if pool_model_config is not None:
            team_logger.info(
                "[external-cli] member {} using pool-allocated model: provider={} model={} api_base={}",
                ctx.member_name,
                pool_model_config.provider,
                pool_model_config.model,
                pool_model_config.api_base,
            )
            external_model_config = pool_model_config
        else:
            team_logger.info(
                "[external-cli] member {} pool model conversion returned None; falling back to static config",
                ctx.member_name,
            )
    else:
        team_logger.info(
            "[external-cli] member {} no pool model assigned; using static config",
            ctx.member_name,
        )
    if ctx.fallback_member_model is not None:
        fallback_external_model_config = _team_model_config_to_external(ctx.fallback_member_model)

    async def promote_fallback_model() -> bool:
        """Persist the fallback model as this member's active model."""
        if teammate_backend is None:
            return False
        return await teammate_backend.db.member.promote_member_fallback_model(
            ctx.member_name or "",
            team_name,
        )

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
            cli_path=cli_cfg.cli_path,
            codex_bin=cli_cfg.codex_bin,
            inject_mcp=cli_cfg.inject_mcp,
            mcp_default_tools_approval_mode=cli_cfg.mcp_default_tools_approval_mode,
            codex_bypass_approvals_and_sandbox=cli_cfg.codex_bypass_approvals_and_sandbox,
            codex_turn_idle_timeout_s=cli_cfg.codex_turn_idle_timeout_s,
            codex_turn_idle_retries=cli_cfg.codex_turn_idle_retries,
            external_model_config=external_model_config,
            fallback_external_model_config=fallback_external_model_config,
            promote_fallback_model=promote_fallback_model,
            mcp_server_command=tuple(cli_cfg.mcp_server_command),
            system_prompt=system_prompt,
            extra_env=cli_cfg.env or None,
            ssh_transport=cli_cfg.ssh_transport,
            resume_external_backend=resume_external_backend,
            member_agent_id=card.id,
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
            external_model_config=external_model_config,
            fallback_external_model_config=fallback_external_model_config,
            promote_fallback_model=promote_fallback_model,
            system_prompt=system_prompt,
            resume_external_backend=resume_external_backend,
            member_agent_id=card.id,
        )

    teammate = _TeamAgent(card)
    # Same team-level cache sharing as inprocess_spawn: the CLI
    # member's TeamAgent runs in-process, so it must reuse the leader's built
    # workspace cache instead of re-scanning the team-workspace md files.
    # Injection must precede configure (see inprocess_spawn for the ordering
    # rationale).
    team_agent.share_workspace_cache_with(teammate)
    team_logger.info(
        "[external-cli] about to configure teammate {} member_runtime={} type={}",
        member_name,
        runtime is not None,
        type(runtime).__name__ if runtime is not None else "None",
    )
    teammate.configure(spec, ctx, member_runtime=runtime)
    teammate_backend = teammate.team_backend
    team_logger.info(
        "[external-cli] after configure teammate {} teammate_backend={}",
        member_name,
        teammate_backend is not None,
    )
    runtime.bind_team_context_tracker(
        _build_team_context_tracker(
            teammate_backend,
            spec,
            ctx,
            member_name,
            team_name,
        ),
    )
    from openjiuwen.agent_teams.external.cli_agent.claude import ClaudeSdkRuntime
    from openjiuwen.agent_teams.external.cli_agent.codex import CodexSdkRuntime

    if isinstance(runtime, ClaudeSdkRuntime) and teammate_backend is not None:
        runtime.bind_team_tools(
            team_backend=teammate_backend,
            role=ctx.role.value,
            teammate_mode=spec.teammate_mode,
            dispatch_mode=spec.dispatch_mode,
            lifecycle=spec.lifecycle,
            language=(ctx.team_spec.language if ctx.team_spec else None) or "cn",
            workspace_manager=teammate.infra.workspace_manager,
            messager=teammate.infra.messager,
            team_name=team_name,
            team_permissions_enabled=spec.enable_permissions,
        )

    # Inject the reliability delivery surface (failed message to the
    # leader mailbox + member ERROR status) for Claude/Codex SDK runtimes only.
    if isinstance(runtime, (ClaudeSdkRuntime, CodexSdkRuntime)) and teammate_backend is not None:
        leader_name = await teammate_backend.resolve_leader_member_name()
        runtime.bind_reliability_context(
            session_id=session_id or "",
            team_backend=teammate_backend,
            leader_name=leader_name,
            update_status_cb=teammate.update_status,
            messager=teammate.infra.messager,
        )

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
            team_logger.exception("[external-cli] member {} crashed", member_name)
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
