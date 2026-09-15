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
import json
import os
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.external.cli_agent.adapters import CliAgentAdapter, build_adapter
from openjiuwen.agent_teams.external.cli_agent.injector import StdinPipeInjector
from openjiuwen.agent_teams.external.cli_agent.transport.base import StreamReaderLike
from openjiuwen.agent_teams.external.cli_agent.transport.local import LocalTransport
from openjiuwen.agent_teams.external.descriptor import MCP_SERVER_ENV_VARS, OPENJIUWEN_HOME_ENV, TeamJoinDescriptor
from openjiuwen.agent_teams.external.member_runtime import ExternalHarnessMemberRuntime
from openjiuwen.agent_teams.external.runtime import CliRuntimeBase, ExternalCliRuntime, ReinvokeCliRuntime
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.paths import get_openjiuwen_home, team_workspace_dir
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.agent_teams.schema.team import ExternalCliModelConfig, TeamRuntimeContext
from openjiuwen.agent_teams.team_workspace.models import TeamWorkspaceConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness_protocol import HarnessContext, McpServerConfig, McpTransport
from openjiuwen.harness_providers.skills import SkillSource
from openjiuwen.harness_providers.claudecode import ClaudeCodeHarness, ClaudeCodeHarnessConfig, ClaudeModelConfig
from openjiuwen.harness_providers.claudecode.options import strip_parent_claude_env
from openjiuwen.harness_providers.codex import CodexHarness, CodexHarnessConfig, CodexModelConfig

MemberRuntimeLike = CliRuntimeBase | ExternalHarnessMemberRuntime


def _with_home_env(env: dict[str, str]) -> dict[str, str]:
    """Ensure the runtime home travels into the CLI subprocess.

    The host platform configures the home via ``configure_openjiuwen_home``
    (a process-global module variable), which does not cross process
    boundaries. A spawned CLI (and the MCP server it in turn spawns) would
    otherwise resolve the default ``~/.openjiuwen`` and miss session spill
    files written under the configured root. Propagate the resolved home as
    ``OPENJIUWEN_HOME`` so :func:`get_openjiuwen_home` (env fallback) and
    Codex's ``mcp_servers.<key>.env_vars`` allow-list (which carries it one
    hop further into the MCP server) keep the paths aligned.
    """
    env.setdefault(OPENJIUWEN_HOME_ENV, str(get_openjiuwen_home()))
    return env


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
        workspace_path = workspace_config.root_path or str(team_workspace_dir(team_name))

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
    cli_path: str | None = None,
    codex_bin: str | None = None,
    inject_mcp: bool = True,
    mcp_server_name: str = "openjiuwen-team",
    mcp_server_command: tuple[str, ...] = ("openjiuwen-team-mcp",),
    mcp_default_tools_approval_mode: str | None = None,
    codex_bypass_approvals_and_sandbox: bool = True,
    codex_turn_idle_timeout_s: float | None = None,
    codex_turn_idle_retries: int | None = None,
    claude_turn_idle_timeout_s: float | None = None,
    external_model_config: ExternalCliModelConfig | None = None,
    fallback_external_model_config: ExternalCliModelConfig | None = None,
    promote_fallback_model: Callable[[], Awaitable[bool]] | None = None,
    system_prompt: str | None = None,
    system_prompt_mode: str | None = None,
    skills: tuple[SkillSource, ...] = (),
    skill_conflict: str = "skip",
    extra_env: dict[str, str] | None = None,
    ssh_transport: SshTransportConfig | None = None,
    resume_external_backend: bool = False,
    member_agent_id: str | None = None,
    team_context_tracker: Any = None,
) -> MemberRuntimeLike:
    """Build the member runtime for ``ctx.cli_agent``.

    Claude and Codex are handled by their dedicated SDK backends. Other CLI agents are
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
            Adapter backends only; Codex accepts ``codex_bin`` instead.
        cli_path: Optional executable path for SDK-backed CLIs. Claude passes
            this to ``ClaudeAgentOptions.cli_path``; Codex maps it to
            ``CodexConfig.codex_bin``.
        codex_bin: Optional Codex executable path. The SDK constructs all
            app-server arguments around this binary.
        inject_mcp: When True (default), configure the backend to register the
            team MCP server so the CLI gets the team collaboration tools.
            Adapter-backed CLIs without an injection strategy ignore this.
            Only the streaming adapter path injects at launch; one-shot CLIs
            register their MCP server out of band.
        mcp_server_name: Logical name the CLI registers the MCP server under.
        mcp_server_command: Launch argv for the team MCP stdio server.
        mcp_default_tools_approval_mode: Optional Codex-only approval policy
            scoped to tools from the injected team MCP server.
        codex_bypass_approvals_and_sandbox: Codex-only switch that disables
            approval prompts and the SDK sandbox by default. Set to ``False``
            to restore Codex approval and sandbox handling.
        codex_turn_idle_timeout_s: Optional Codex-only inactivity ceiling for
            one SDK turn. Every received SDK notification refreshes it.
        codex_turn_idle_retries: Optional number of same-thread retries when a
            stalled turn emitted no SDK notifications and was interrupted.
        claude_turn_idle_timeout_s: Optional Claude-only inactivity ceiling for
            one SDK turn. Every received SDK message refreshes it.
        external_model_config: Optional model endpoint config translated into
            backend-specific SDK options.
        fallback_external_model_config: Optional endpoint used only after an
            explicit native authentication failure.
        promote_fallback_model: Callback persisting the fallback as active.
        skills: Skill bundles copied into the local CLI project before startup.
        skill_conflict: Skip or replace an existing project skill with the same name.
        system_prompt_mode: Claude/Codex append or replace policy; None uses the provider default.
        system_prompt: The member's team-rail system prompt. Claude receives it
            through SDK options, Codex through SDK thread options, and other CLIs
            may receive it as a launch arg.
            CLIs without a flag get it prepended to their first user message by
            the caller, so it is ignored here for them.
        extra_env: Extra environment merged over the inherited env + the
            team-join descriptor (descriptor wins is not desired, so this is
            applied last only for non-descriptor keys).
        ssh_transport: Optional ssh endpoint config. Currently supported only
            by the Claude SDK backend.
        resume_external_backend: When True, resume the derived backend session
            instead of starting it as a fresh session.
        member_agent_id: Stable TeamAgent card id used to address this
            external member's own AgentSession checkpoint.
        team_context_tracker: Tracker deciding which team state this member has
            not been told about yet; the runtime folds its output into the next
            message it sends to the CLI. ``None`` disables team-state delivery.
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
        if codex_bin is not None:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="codex_bin is only supported for Codex SDK members",
            )
        if command_override is not None:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="Claude SDK members do not support command_override; configure cli_path instead",
            )
        if codex_turn_idle_timeout_s is not None:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="codex_turn_idle_timeout_s is only supported for Codex SDK members",
            )
        if codex_turn_idle_retries is not None:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="codex_turn_idle_retries is only supported for Codex SDK members",
            )
        return await _build_claude_member_runtime(
            ctx,
            descriptor,
            cwd=cwd,
            add_dirs=add_dirs,
            cli_path=cli_path,
            inject_mcp=inject_mcp,
            mcp_server_name=mcp_server_name,
            external_model_config=external_model_config,
            fallback_external_model_config=fallback_external_model_config,
            promote_fallback_model=promote_fallback_model,
            system_prompt=system_prompt,
            system_prompt_mode=system_prompt_mode,
            skills=skills,
            skill_conflict=skill_conflict,
            extra_env=extra_env,
            ssh_transport=ssh_transport,
            resume_external_backend=resume_external_backend,
            member_agent_id=member_agent_id,
            team_context_tracker=team_context_tracker,
        )
    if ctx.cli_agent == "codex":
        if command_override is not None:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="Codex SDK members do not support command_override; configure cli_path instead",
            )
        if claude_turn_idle_timeout_s is not None:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="claude_turn_idle_timeout_s is only supported for Claude SDK members",
            )
        if ssh_transport is not None:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="ssh transport is not yet supported for Codex SDK members",
            )
        if not member_agent_id:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason=f"Codex SDK member '{ctx.member_name}' requires a stable member_agent_id",
            )
        if add_dirs:
            team_logger.debug(
                "[external-cli] codex member {} uses cwd {}; extra add_dirs are not supported by the Codex SDK",
                ctx.member_name,
                cwd,
            )
        return await _build_codex_member_runtime(
            ctx,
            descriptor,
            cwd=cwd,
            codex_bin=cli_path or codex_bin,
            inject_mcp=inject_mcp,
            mcp_server_name=mcp_server_name,
            mcp_server_command=mcp_server_command,
            mcp_default_tools_approval_mode=mcp_default_tools_approval_mode,
            bypass_approvals_and_sandbox=codex_bypass_approvals_and_sandbox,
            turn_idle_timeout_s=codex_turn_idle_timeout_s,
            turn_idle_retries=codex_turn_idle_retries,
            external_model_config=external_model_config,
            fallback_external_model_config=fallback_external_model_config,
            promote_fallback_model=promote_fallback_model,
            system_prompt=system_prompt,
            system_prompt_mode=system_prompt_mode,
            skills=skills,
            skill_conflict=skill_conflict,
            extra_env=extra_env,
            resume_external_backend=resume_external_backend,
            member_agent_id=member_agent_id,
            team_context_tracker=team_context_tracker,
        )
    if ssh_transport is not None:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="ssh transport is only supported for claude SDK external CLI members",
        )
    if codex_bin is not None:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="codex_bin is only supported for Codex SDK members",
        )
    if cli_path is not None:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="cli_path is only supported for Claude and Codex SDK members",
        )
    if claude_turn_idle_timeout_s is not None:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="claude_turn_idle_timeout_s is only supported for Claude SDK members",
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
    env = _with_home_env({**base_env, **(extra_env or {}), **descriptor.to_env()})

    # System prompt as a launch arg. CLIs without a flag return [] here and get
    # the prompt prepended to their first user message by the caller instead.
    sp_args = tuple(adapter.system_prompt_args(system_prompt or ""))

    mcp_args: tuple[str, ...] = ()
    if inject_mcp:
        mcp_args = tuple(adapter.mcp_launch_args(server_name=mcp_server_name, server_command=mcp_server_command))
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
            member_agent_id=member_agent_id,
            team_context_tracker=team_context_tracker,
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
        member_agent_id=member_agent_id,
        team_context_tracker=team_context_tracker,
    )




def _member_context(
    ctx: TeamRuntimeContext,
    descriptor: TeamJoinDescriptor,
    *,
    member_agent_id: str | None,
    system_prompt: str | None,
    cwd: str | None,
) -> HarnessContext:
    """Build the provider-neutral start context for an external SDK member."""
    member_name = ctx.member_name or ""
    return HarnessContext(
        agent_name=member_name,
        agent_id=member_agent_id or f"{descriptor.team_name}_{member_name}",
        host_session_id=descriptor.session_id,
        system_prompt=system_prompt or "",
        cwd=cwd,
        metadata={"team_name": descriptor.team_name, "role": ctx.role.value},
    )


def _claude_model(config: ExternalCliModelConfig | None) -> ClaudeModelConfig | None:
    if config is None:
        return None
    return ClaudeModelConfig(model=config.model, api_base=config.api_base, api_key=config.api_key)


def _codex_model(config: ExternalCliModelConfig | None) -> CodexModelConfig | None:
    if config is None:
        return None
    return CodexModelConfig(
        model=config.model,
        provider=config.provider,
        api_base=config.api_base,
        api_key=config.api_key,
    )


async def _build_claude_member_runtime(
    ctx: TeamRuntimeContext,
    descriptor: TeamJoinDescriptor,
    *,
    cwd: str | None,
    add_dirs: tuple[str, ...],
    cli_path: str | None,
    inject_mcp: bool,
    mcp_server_name: str,
    external_model_config: ExternalCliModelConfig | None,
    fallback_external_model_config: ExternalCliModelConfig | None,
    promote_fallback_model: Callable[[], Awaitable[bool]] | None,
    system_prompt: str | None,
    system_prompt_mode: str | None,
    skills: tuple[SkillSource, ...],
    skill_conflict: str,
    extra_env: dict[str, str] | None,
    ssh_transport: SshTransportConfig | None,
    resume_external_backend: bool,
    member_agent_id: str | None,
    team_context_tracker: Any,
) -> ExternalHarnessMemberRuntime:
    """Build a Claude Code member runtime on the protocol harness."""
    if ssh_transport is None:
        base_env = strip_parent_claude_env(dict(os.environ))
    else:
        base_env = {}
    env = _with_home_env({**base_env, **(extra_env or {}), **descriptor.to_env()})
    team_logger.info(
        "[external-cli] preparing claude member {} cwd={} cli_path_configured={} inject_mcp={} "
        "mcp_server_name={} team_join_env_present={} ssh_transport_configured={}",
        ctx.member_name,
        cwd,
        cli_path is not None,
        inject_mcp,
        mcp_server_name,
        "OPENJIUWEN_TEAM_JOIN" in env,
        ssh_transport is not None,
    )
    span_bridge = _build_claude_span_bridge(
        member_name=ctx.member_name or "",
        member_agent_id=member_agent_id,
        team_name=descriptor.team_name,
        session_id=descriptor.session_id,
        role=ctx.role.value,
    )
    settings_env = await _attach_claude_native_otel(
        span_bridge,
        env,
        member_name=ctx.member_name or "",
        ssh_transport=ssh_transport,
    )
    fallback_model = None
    if external_model_config is None and fallback_external_model_config is not None:
        fallback_model = _claude_model(fallback_external_model_config)
    config = ClaudeCodeHarnessConfig(
        skills=skills,
        skill_conflict=skill_conflict,
        system_prompt_mode=system_prompt_mode or "append",
        cwd=cwd,
        add_dirs=add_dirs,
        env=env,
        settings_env=settings_env,
        inherit_process_env=False,
        cli_path=cli_path,
        model=_claude_model(external_model_config),
        fallback_model=fallback_model,
    )
    transport_factory = None
    if ssh_transport is not None:
        from openjiuwen.agent_teams.external.cli_agent.claude.ssh_transport import build_claude_sdk_ssh_transport

        team_logger.info("[external-cli] using claude sdk ssh transport for member {}", ctx.member_name)

        def transport_factory(options: Any) -> Any:
            return build_claude_sdk_ssh_transport(prompt=_empty_prompt(), options=options, config=ssh_transport)

    harness = ClaudeCodeHarness(config, transport_factory=transport_factory)
    runtime = ExternalHarnessMemberRuntime(
        harness=harness,
        context=_member_context(ctx, descriptor, member_agent_id=member_agent_id, system_prompt=system_prompt, cwd=cwd),
        team_context_tracker=team_context_tracker,
        resume_external_backend=resume_external_backend,
        agent_kind="claude",
        cli_path=cli_path,
        inject_mcp=inject_mcp,
        mcp_server_name=mcp_server_name,
    )
    runtime.bind_span_bridge(span_bridge)
    runtime.bind_fallback_promotion(promote_fallback_model)
    return runtime


async def _empty_prompt() -> AsyncIterator[dict[str, Any]]:
    """Provide an empty streaming prompt for SDK transport construction."""
    return
    yield {}  # type: ignore[unreachable]


_OTEL_RESOURCE_ATTRIBUTES_ENV = "OTEL_RESOURCE_ATTRIBUTES"


async def _attach_claude_native_otel(
    span_bridge: Any,
    env: dict[str, str],
    *,
    member_name: str,
    ssh_transport: SshTransportConfig | None,
) -> dict[str, str]:
    """Point Claude Code's own OTel export at the bridge's loopback receiver.

    Native spans (``claude_code.llm_request`` and the raw API body log events)
    are what the bridge turns into ``llm.call`` spans. The augmentation is
    best-effort: a failure to attach only disables it.

    Args:
        span_bridge: The Claude span bridge, or ``None`` when observability is
            not initialized.
        env: Process env for the CLI subprocess, updated in place.
        member_name: Member the runtime belongs to, for diagnostics.
        ssh_transport: Set when the CLI runs on a remote host, where a
            loopback receiver is unreachable.

    Returns:
        Env that must also win over the CLI's user settings, empty when the
        native export is not enabled.
    """
    if span_bridge is None:
        return {}
    if ssh_transport is not None:
        team_logger.info(
            "[external-cli] claude native otel disabled for ssh member {}; loopback receiver is local-only",
            member_name,
        )
        return {}
    try:
        endpoint = await span_bridge.attach_native_trace()
    except Exception as exc:  # noqa: BLE001 - observability is optional
        team_logger.warning("[external-cli] claude native otel disabled for member {}: {}", member_name, exc)
        return {}
    if not endpoint:
        return {}
    from openjiuwen.agent_teams.observability.shared_otlp import OTEL_RESOURCE_SOURCE_ID
    from openjiuwen.harness_providers.claudecode.options import claude_otel_env

    team_logger.info(
        "[external-cli] claude native otel enabled for member {} endpoint={}",
        member_name,
        endpoint,
    )
    env.update(claude_otel_env(endpoint))
    # Pin the trace parent explicitly. The SDK injects the ambient OTel context
    # at connect() time, but member turns run in bare background tasks with no
    # active span — the CLI would then start its own root trace and the
    # bridge's trace-id filter would drop every native span.
    traceparent = span_bridge.native_traceparent()
    if traceparent:
        env.setdefault("TRACEPARENT", traceparent)
    source_id = span_bridge.native_source_id()
    if not source_id:
        return {}
    existing = [
        item
        for item in str(env.get(_OTEL_RESOURCE_ATTRIBUTES_ENV) or "").split(",")
        if item and not item.startswith(f"{OTEL_RESOURCE_SOURCE_ID}=")
    ]
    existing.append(f"{OTEL_RESOURCE_SOURCE_ID}={source_id}")
    resource_attributes = ",".join(existing)
    env[_OTEL_RESOURCE_ATTRIBUTES_ENV] = resource_attributes
    # The CLI applies user settings after the process env, so the identity the
    # receiver filters on has to be injected through --settings as well.
    return {_OTEL_RESOURCE_ATTRIBUTES_ENV: resource_attributes}


def _build_claude_span_bridge(
    *,
    member_name: str,
    member_agent_id: str | None,
    team_name: str | None,
    session_id: str | None,
    role: str | None,
) -> Any:
    """Build the optional Claude OTel bridge without a hard OTel dependency."""
    try:
        from openjiuwen.agent_teams.observability.setup import is_initialized
    except ImportError:
        return None
    if not is_initialized():
        return None
    try:
        from openjiuwen.agent_teams.observability.claude import ClaudeSpanBridge
    except ImportError as exc:
        team_logger.warning("[{}] Claude observability bridge unavailable: {}", member_name, exc)
        return None
    return ClaudeSpanBridge.build(
        member_name=member_name,
        member_agent_id=member_agent_id,
        team_name=team_name,
        session_id=session_id,
        role=role,
    )


async def _build_codex_member_runtime(
    ctx: TeamRuntimeContext,
    descriptor: TeamJoinDescriptor,
    *,
    cwd: str | None,
    codex_bin: str | None,
    inject_mcp: bool,
    mcp_server_name: str,
    mcp_server_command: tuple[str, ...],
    mcp_default_tools_approval_mode: str | None,
    bypass_approvals_and_sandbox: bool,
    turn_idle_timeout_s: float | None,
    turn_idle_retries: int | None,
    external_model_config: ExternalCliModelConfig | None,
    fallback_external_model_config: ExternalCliModelConfig | None,
    promote_fallback_model: Callable[[], Awaitable[bool]] | None,
    system_prompt: str | None,
    system_prompt_mode: str | None,
    skills: tuple[SkillSource, ...],
    skill_conflict: str,
    extra_env: dict[str, str] | None,
    resume_external_backend: bool,
    member_agent_id: str,
    team_context_tracker: Any,
) -> ExternalHarnessMemberRuntime:
    """Build a Codex member runtime on the protocol harness."""
    member_name = ctx.member_name or ""
    env = _with_home_env({**dict(os.environ), **(extra_env or {}), **descriptor.to_env()})
    team_logger.info(
        "[external-cli] preparing codex member {} cwd={} codex_bin_configured={} inject_mcp={} "
        "mcp_server_name={} mcp_server_command={} team_join_env_present={}",
        member_name,
        cwd,
        codex_bin is not None,
        inject_mcp,
        mcp_server_name,
        mcp_server_command,
        "OPENJIUWEN_TEAM_JOIN" in env,
    )
    if inject_mcp and not mcp_server_command:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="Codex SDK MCP injection requires a non-empty mcp_server_command",
        )
    observability = await _start_codex_observability(
        member_name=member_name,
        member_agent_id=member_agent_id,
        team_name=descriptor.team_name,
        session_id=descriptor.session_id,
        role=ctx.role.value,
    )
    traceparent = observability.traceparent
    if traceparent:
        # Codex reads TRACEPARENT when its App Server subprocess starts.
        env.setdefault("TRACEPARENT", traceparent)
    for key, value in observability.env.items():
        env.setdefault(key, value)
    fallback_model = None
    if external_model_config is None and fallback_external_model_config is not None:
        fallback_model = _codex_model(fallback_external_model_config)
    config_kwargs: dict[str, Any] = {
        "skills": skills,
        "skill_conflict": skill_conflict,
        "system_prompt_mode": system_prompt_mode or "replace",
        "cwd": cwd,
        "env": env,
        "inherit_process_env": False,
        "codex_bin": codex_bin,
        "model": _codex_model(external_model_config),
        "fallback_model": fallback_model,
        "config_overrides": observability.config_overrides,
        "bypass_approvals_and_sandbox": bypass_approvals_and_sandbox,
        "mcp_env_passthrough": tuple(MCP_SERVER_ENV_VARS),
        "mcp_default_tools_approval_mode": mcp_default_tools_approval_mode,
        "client_name": "openjiuwen_agent_team",
        "client_title": f"OpenJiuwen Team Member {member_name}",
    }
    if turn_idle_timeout_s is not None:
        config_kwargs["turn_idle_timeout_s"] = turn_idle_timeout_s
    if turn_idle_retries is not None:
        config_kwargs["turn_idle_retries"] = turn_idle_retries
    try:
        config = CodexHarnessConfig(**config_kwargs)
    except BaseException:
        await observability.aclose()
        raise
    harness = CodexHarness(config, notification_observer=observability.observer)
    runtime = ExternalHarnessMemberRuntime(
        harness=harness,
        context=_member_context(ctx, descriptor, member_agent_id=member_agent_id, system_prompt=system_prompt, cwd=cwd),
        team_context_tracker=team_context_tracker,
        resume_external_backend=resume_external_backend,
        agent_kind="codex",
        cli_path=codex_bin,
        inject_mcp=inject_mcp,
        mcp_server_name=mcp_server_name,
    )
    if inject_mcp:
        runtime.bind_mcp_servers(
            [McpServerConfig(name=mcp_server_name, transport=McpTransport.STDIO, command=mcp_server_command)]
        )
    runtime.bind_span_bridge(observability.span_bridge)
    runtime.bind_fallback_promotion(promote_fallback_model)
    runtime.add_teardown_hook(observability.aclose)
    return runtime


class _CodexObservability:
    """Optional OTel augmentation attached to one Codex member."""

    def __init__(self, span_bridge: Any) -> None:
        self.span_bridge = span_bridge
        self.observer: Callable[[Any], None] | None = None
        self.config_overrides: tuple[str, ...] = ()
        self.env: dict[str, str] = {}
        self.traceparent: str | None = None
        self.receiver: Any = None
        self.rollout_reader: Any = None

    async def aclose(self) -> None:
        receiver, self.receiver = self.receiver, None
        reader, self.rollout_reader = self.rollout_reader, None
        for closer in (receiver, reader):
            if closer is None:
                continue
            try:
                await closer.aclose()
            except Exception:
                team_logger.debug("[external-cli] codex observability teardown failed", exc_info=True)


async def _start_codex_observability(
    *,
    member_name: str,
    member_agent_id: str,
    team_name: str,
    session_id: str,
    role: str | None,
) -> _CodexObservability:
    """Start the Codex OTel bridge, receiver and rollout reader when enabled.

    Observability augmentation is best-effort: a failure only logs a warning
    and disables that augmentation; it never blocks member construction.
    """
    try:
        from openjiuwen.agent_teams.observability.setup import is_initialized
    except ImportError:
        return _CodexObservability(None)
    if not is_initialized():
        return _CodexObservability(None)
    try:
        from openjiuwen.agent_teams.observability.codex import (
            CodexOtelTraceReceiver,
            CodexRolloutTraceReader,
            CodexSpanBridge,
        )
    except ImportError as exc:
        team_logger.warning("[{}] Codex observability bridge unavailable: {}", member_name, exc)
        return _CodexObservability(None)
    from openjiuwen.agent_teams.external.cli_agent.codex.observer import build_codex_notification_observer

    span_bridge = CodexSpanBridge(
        member_name=member_name,
        member_agent_id=member_agent_id,
        team_name=team_name,
        session_id=session_id,
        role=role,
    )
    result = _CodexObservability(span_bridge)
    result.observer = build_codex_notification_observer(span_bridge)
    result.traceparent = span_bridge.native_traceparent()
    overrides: list[str] = []
    try:
        team_logger.info("[external-cli] starting codex rollout trace reader for member {}", member_name)
        result.rollout_reader = await CodexRolloutTraceReader.start(span_bridge.record_rollout_event)
        span_bridge.enable_rollout_trace()
        team_logger.info("[external-cli] starting codex native otel receiver for member {}", member_name)
        result.receiver = await CodexOtelTraceReceiver.start(span_bridge.record_native_model_span)
        if result.receiver is not None:
            span_bridge.enable_native_model_spans()
    except Exception as exc:  # noqa: BLE001 - observability is optional
        team_logger.warning(
            "[external-cli] codex observability augmentation disabled for member {}: {}",
            member_name,
            exc,
        )
        await result.aclose()
        return result
    if result.rollout_reader is not None:
        result.env["CODEX_ROLLOUT_TRACE_ROOT"] = str(result.rollout_reader.root)
    if result.receiver is not None:
        # Codex uses an OTel batch span processor. Keep its delivery interval
        # below the turn-finalization grace period so the native sampling
        # span arrives before the member turn is finalized.
        result.env["OTEL_BSP_SCHEDULE_DELAY"] = "100"
        overrides.extend(
            (
                'otel.environment="openjiuwen"',
                "otel.exporter=none",
                (
                    "otel.trace_exporter={ otlp-http = { "
                    f"endpoint = {json.dumps(result.receiver.endpoint)}, "
                    'protocol = "binary" } }'
                ),
                "otel.metrics_exporter=none",
                "otel.log_user_prompt=false",
            )
        )
    result.config_overrides = tuple(overrides)
    return result


__all__ = ["MemberRuntimeLike", "build_cli_runtime", "descriptor_from_context"]
