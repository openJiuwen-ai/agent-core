# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run the team leader on a third-party harness.

A spawned teammate gets its external runtime from :func:`external_cli_spawn`,
which runs once the team session is live. The leader is different in one
respect only: it is configured by ``TeamAgentSpec.build`` (or recovered from a
checkpoint) before any session is bound, and its runtime then serves every run
cycle of the team. So its runtime is built here without touching the session,
and everything that depends on the session -- the start context, the team
tools, the trajectory recorder -- is bound each time the runtime starts.

A team with swarmflow enabled gives the leader the same ``swarmflow`` tool an
external CLI member gets, hosted the same way: the leader has no local harness
to run background work on, so an ``ExternalSwarmflowHost`` stands in for it.

The leader's team tools are built over its own ``TeamBackend`` and mounted in
process (Claude's SDK MCP server, Codex's dynamic tools). Spawning members,
allocating models and building the team all go through callbacks that only
that backend holds, which is why only the SDK backends can drive a leader.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.external.cli_agent import TEAM_MCP_SERVER_NAME
from openjiuwen.agent_teams.external.cli_agent.spawn import build_sdk_member_runtime
from openjiuwen.agent_teams.external.member_runtime import ExternalHarnessMemberRuntime
from openjiuwen.agent_teams.inbound_render import render_team_policy, render_team_policy_inline
from openjiuwen.agent_teams.prompts import build_leader_policy_disclosure, build_leader_system_prompt
from openjiuwen.agent_teams.prompts.loader import make_template_loader
from openjiuwen.agent_teams.spawn.external_cli_spawn import (
    _bind_protocol_member_team_tools,
    _bind_trajectory_recorder,
    _build_team_context_tracker,
    _external_swarmflow_host,
    _external_cli_config_for_member,
    _resolve_external_paths,
    _resolve_member_models,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness_protocol import HarnessContext
from openjiuwen.harness_providers.skills import normalize_skills

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
    from openjiuwen.agent_teams.tools.team import TeamBackend

# The checkpoint tools snapshot the conversation of the local harness the
# leader no longer has.
_CHECKPOINT_TOOLS = frozenset({"checkpoint", "list_checkpoints"})
# The async-task tools query an async tool runtime. A leader has one only
# while a swarmflow host stands in for the local harness.
_ASYNC_TASK_TOOLS = frozenset({"async_tasks_list", "async_task_output", "async_task_cancel"})
# Predefined teams pin their roster, so the leader gets no spawn tool.
_SPAWN_TOOLS = frozenset({"spawn_teammate", "spawn_human_agent", "spawn_bridge_agent"})


def _language(ctx: "TeamRuntimeContext") -> str:
    return (ctx.team_spec.language if ctx.team_spec else None) or "cn"


def _team_name(spec: "TeamAgentSpec", ctx: "TeamRuntimeContext") -> str:
    return (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name


def _tool_namespace(ctx: "TeamRuntimeContext") -> str | None:
    """The MCP server the leader's team tools come from, or None.

    Codex registers them as top-level dynamic tools under their bare names,
    so it has no server to declare.
    """
    return None if ctx.cli_agent == "codex" else TEAM_MCP_SERVER_NAME


def _expert_handles(spec: "TeamAgentSpec") -> tuple[Any, Any]:
    """The host's expert catalog and the team's binding to it, or ``(None, None)``."""
    from openjiuwen.agent_teams.agent.agent_configurator import _derive_expert_binding
    from openjiuwen.agent_teams.experts.catalog import get_process_expert_catalog

    catalog = get_process_expert_catalog()
    if catalog is None:
        return None, None
    return catalog, _derive_expert_binding(spec)


def _bound_group_name(spec: "TeamAgentSpec") -> str | None:
    """The bound AgentGroup the leader is pointed at, under the rail's own gate."""
    if spec.dispatch_mode != "scheduled":
        return None
    catalog, binding = _expert_handles(spec)
    bound = binding.bound_group if catalog is not None and binding is not None else None
    return bound.name if bound is not None else None


def build_external_leader_runtime(
    spec: "TeamAgentSpec",
    ctx: "TeamRuntimeContext",
    *,
    agent_id: str,
    team_backend: "TeamBackend",
) -> ExternalHarnessMemberRuntime:
    """Build the leader's runtime on the harness named by ``ctx.cli_agent``.

    Synchronous and session-free: the provider start context is a factory the
    runtime resolves on ``start``, and the CLI process inherits no team-join
    environment because the leader's tools never leave this process.

    Args:
        spec: The team spec; ``external_cli_agents`` holds the launch config.
        ctx: The leader's runtime context.
        agent_id: The leader's agent card id; addresses its own agent session,
            where the provider checkpoint is kept.
        team_backend: The leader's backend, read when the context is resolved.

    Returns:
        The runtime, not yet started and with nothing session-bound.
    """
    cli_cfg = _external_cli_config_for_member(spec, ctx.member_name, ctx.cli_agent)
    if cli_cfg is None:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=f"leader cli_agent '{ctx.cli_agent}' is not declared in external_cli_agents",
        )
    team_name = _team_name(spec, ctx)
    member_name = ctx.member_name or ""
    language = _language(ctx)
    cwd, add_dirs = _resolve_external_paths(spec, ctx, configured_cwd=cli_cfg.cwd, team_name=team_name)
    external_model_config, fallback_external_model_config = _resolve_member_models(ctx, cli_cfg)
    leader_agent = spec.agents.get("leader")
    base_prompt = leader_agent.system_prompt if leader_agent is not None else None

    async def start_context(team_session: Any | None) -> HarnessContext:
        if team_session is None:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="an external leader harness starts only on a bound team session",
            )
        # Decided per run: a conversation that already holds the policy must
        # not be routed back to build_team, which would refuse.
        recovered = team_backend.history_restored() and await team_backend.rejects_rebuild()
        system_prompt = build_leader_system_prompt(
            bootstrap_mode=spec.bootstrap_mode,
            # Same signal the tool factory gates the ``swarmflow`` tool on.
            swarmflow_enabled=spec.enable_swarmflow,
            recovered=recovered,
            bound_group_name=_bound_group_name(spec),
            base_prompt=base_prompt,
            language=language,
            mcp_server_name=_tool_namespace(ctx),
            loader=make_template_loader(team_backend.workspace_cache),
        )
        return HarnessContext(
            agent_name=member_name,
            agent_id=agent_id,
            host_session_id=team_session.get_session_id(),
            system_prompt=system_prompt,
            cwd=cwd,
            metadata={"team_name": team_name, "role": ctx.role.value},
        )

    team_logger.info("[external-leader] building {} harness for leader {} cwd={}", ctx.cli_agent, member_name, cwd)
    return build_sdk_member_runtime(
        ctx,
        context=start_context,
        join_env={},
        cwd=cwd,
        add_dirs=add_dirs,
        cli_path=cli_cfg.cli_path,
        codex_bin=cli_cfg.codex_bin,
        inject_mcp=True,
        mcp_default_tools_approval_mode=cli_cfg.mcp_default_tools_approval_mode,
        codex_bypass_approvals_and_sandbox=cli_cfg.codex_bypass_approvals_and_sandbox,
        codex_turn_idle_timeout_s=cli_cfg.codex_turn_idle_timeout_s,
        codex_turn_idle_retries=cli_cfg.codex_turn_idle_retries,
        claude_turn_idle_timeout_s=cli_cfg.claude_turn_idle_timeout_s,
        claude_max_buffer_size=cli_cfg.claude_max_buffer_size,
        external_model_config=external_model_config,
        fallback_external_model_config=fallback_external_model_config,
        system_prompt_mode=cli_cfg.system_prompt_mode,
        skills=normalize_skills(cli_cfg.skills, cli_cfg.skill_conflict),
        skill_conflict=cli_cfg.skill_conflict,
        extra_env=cli_cfg.env or None,
        ssh_transport=cli_cfg.ssh_transport,
        member_agent_id=agent_id,
        # Nobody is above the leader to be told: its failures go to the user.
        surface_turn_failures=True,
    )


def _leader_policy_text(spec: "TeamAgentSpec", ctx: "TeamRuntimeContext", team_backend: "TeamBackend") -> str | None:
    """The collaboration policy a framework-built team's leader is told up front.

    Such a team has no ``build_team`` result to carry it, so it rides the team
    context tracker instead -- the same text and the same channel an
    in-process leader uses, declared under the tool namespace when the
    leader's tools come from an MCP server.
    """
    if spec.bootstrap_mode != "framework":
        return None
    from openjiuwen.agent_teams.agent.agent_configurator import _resolve_team_mode

    language = _language(ctx)
    loader = make_template_loader(team_backend.workspace_cache)
    body = build_leader_policy_disclosure(
        lifecycle=spec.lifecycle,
        teammate_mode=spec.teammate_mode,
        team_mode=_resolve_team_mode(spec),
        dispatch_mode=spec.dispatch_mode,
        member_messaging=team_backend.member_messaging_enabled() is True,
        member_subtasks=team_backend.member_subtasks_enabled() is True,
        language=language,
        hitt_enabled=team_backend.hitt_enabled(),
        loader=loader,
    )
    server = _tool_namespace(ctx)
    if server is None:
        return render_team_policy_inline(body=body)
    note = loader("tool_namespace", language).format({"server": server}).content.strip()
    return render_team_policy(body=body, tools=server, note=note)


def _leader_tool_options(
    spec: "TeamAgentSpec",
    team_backend: "TeamBackend",
    *,
    hosts_async_tools: bool,
) -> dict[str, Any]:
    """What a leader's tool set needs on top of what every member's does.

    The same mode axes the leader's prompt is rendered against, so the tools
    it is told about are the tools it holds.
    """
    from openjiuwen.agent_teams.agent.agent_configurator import _resolve_team_mode

    team_mode = _resolve_team_mode(spec)
    exclude = _CHECKPOINT_TOOLS
    if not hosts_async_tools:
        exclude = exclude | _ASYNC_TASK_TOOLS
    if team_mode == "predefined":
        exclude = exclude | _SPAWN_TOOLS
    expert_catalog, expert_binding = _expert_handles(spec)
    return {
        "model_config_allocator": team_backend.allocate_model,
        "team_mode": team_mode,
        "bootstrap_mode": spec.bootstrap_mode,
        "exclude_tools": exclude,
        "expert_catalog": expert_catalog,
        "expert_binding": expert_binding,
    }


def bind_external_leader(
    agent: "TeamAgent",
    spec: "TeamAgentSpec",
    ctx: "TeamRuntimeContext",
) -> None:
    """Wire the configured leader's external runtime to its team.

    Binds the team context tracker once, and registers the startup hook that
    rebuilds the session-bound pieces on every start: the runtime is stopped
    at the end of each run cycle and may restart under another session.

    Args:
        agent: The configured leader; its ``resources.harness`` is the runtime.
        spec: The team spec.
        ctx: The leader's runtime context.
    """
    runtime = agent.resources.harness
    team_backend = agent.team_backend
    if not isinstance(runtime, ExternalHarnessMemberRuntime) or team_backend is None:
        return
    team_name = _team_name(spec, ctx)
    runtime.bind_team_context_tracker(
        _build_team_context_tracker(
            team_backend,
            spec,
            ctx,
            ctx.member_name,
            team_name,
            policy_text=_leader_policy_text(spec, ctx, team_backend),
        ),
    )

    # One host for the runtime's whole life: a background run outlives the
    # run cycle that launched it, and must still be listed and delivered after.
    swarmflow_host = _external_swarmflow_host(agent, spec, ctx, team_name, ctx.member_name, "", runtime)

    async def bind_session_services(team_session: Any | None) -> None:
        if team_session is None:
            return
        session_id = team_session.get_session_id()
        # A restored conversation is only as good as the provider session it
        # resumes. Without a stored checkpoint the provider starts afresh, so
        # the policy is gone and build_team must stay callable.
        if team_backend.history_restored() and not runtime.has_stored_checkpoint():
            team_backend.clear_history_restored()
        if swarmflow_host is not None:
            # The embedder attaches its controller after configure, per run.
            swarmflow_host.background_task_controller = agent.background_task_controller
        runtime.reset_tool_bindings()
        _bind_protocol_member_team_tools(
            runtime,
            leader=agent,
            teammate=agent,
            teammate_backend=team_backend,
            spec=spec,
            ctx=ctx,
            team_name=team_name,
            session_id=session_id,
            role_tool_options=_leader_tool_options(
                spec,
                team_backend,
                hosts_async_tools=swarmflow_host is not None,
            ),
            swarmflow_host=swarmflow_host,
        )
        _bind_trajectory_recorder(runtime, teammate=agent, session_id=session_id, team_name=team_name)

    runtime.add_startup_hook(bind_session_services)


__all__ = ["bind_external_leader", "build_external_leader_runtime"]
