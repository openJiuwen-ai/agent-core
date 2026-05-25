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
from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.agent_teams.external.cli_agent.spawn import build_cli_runtime
from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import TeamRuntimeContext


async def external_cli_spawn(
    team_agent: "TeamAgent",
    ctx: "TeamRuntimeContext",
    *,
    initial_message: Optional[str] = None,
    session_id: Optional[str] = None,
) -> InProcessSpawnHandle:
    """Launch the CLI for ``ctx.cli_agent`` and run it as a team member.

    Args:
        team_agent: The leader TeamAgent that owns the team spec.
        ctx: Runtime context for the external CLI member.
        initial_message: First prompt delivered to the CLI.
        session_id: Session id propagated via contextvars.

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
        description=f"External CLI member: {ctx.persona}" if ctx.persona else "External CLI member",
    )

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
        runtime = await build_cli_runtime(
            ctx,
            cwd=cli_cfg.cwd,
            command_override=tuple(cli_cfg.command) if cli_cfg.command else None,
            inject_mcp=cli_cfg.inject_mcp,
            mcp_server_command=tuple(cli_cfg.mcp_server_command),
            extra_env=cli_cfg.env or None,
        )
    else:
        runtime = await build_cli_runtime(ctx)

    teammate = _TeamAgent(card)
    teammate.configure(spec, ctx, member_runtime=runtime)

    query = initial_message or "Join the team and wait for your first assignment."
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
            await runtime.aclose()

    task = run_ctx.run(asyncio.get_running_loop().create_task, _run())
    handle = InProcessSpawnHandle(
        process_id=f"extcli-{member_name}",
        _task=task,
        agent_ref=teammate,
    )
    team_logger.info("[external-cli] spawned member {} as {}", member_name, handle.process_id)
    return handle


__all__ = ["external_cli_spawn"]
