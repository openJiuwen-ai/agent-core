# coding: utf-8
"""Spawn a teammate as an in-process coroutine (asyncio.Task)."""

from __future__ import annotations

import asyncio
import contextvars
from typing import (
    Any,
    Optional,
    TYPE_CHECKING,
)

from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import TeamRuntimeContext


async def inprocess_spawn(
    team_agent: "TeamAgent",
    ctx: "TeamRuntimeContext",
    *,
    initial_message: Optional[str] = None,
    session_id: Optional[str] = None,
) -> InProcessSpawnHandle:
    """Spawn a teammate TeamAgent as a coroutine in the current process.

    Mirrors the subprocess path (Runner.spawn_agent -> child_process) but
    runs everything within the same event loop.

    Args:
        team_agent: The leader TeamAgent that owns the team spec.
        ctx: Runtime context for the teammate.
        initial_message: First query to send to the teammate.
        session_id: Session id to propagate via contextvars.

    Returns:
        An InProcessSpawnHandle wrapping the teammate's asyncio.Task.
    """
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent as _TeamAgent
    from openjiuwen.agent_teams.spawn.context import set_session_id
    from openjiuwen.core.runner.runner import Runner
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    spec = team_agent.spec

    agent_spec = spec.agents.get(ctx.role.value) or spec.agents["leader"]
    team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
    card_id = f"{team_name}_{ctx.member_name}" if ctx.member_name else "unknown"
    card = agent_spec.card or AgentCard(
        id=card_id,
        name=ctx.member_name or "unknown",
        description=f"Teammate: {ctx.persona}" if ctx.persona else "Teammate",
    )

    teammate = _TeamAgent(card)
    await teammate.configure_team(spec, ctx)

    query = initial_message or "Join the team and wait for your first assignment."
    inputs: dict[str, Any] = {"query": query}

    member_name = ctx.member_name

    # Copy current context so session_id propagates into the new task.
    run_ctx = contextvars.copy_context()

    async def _run() -> Any:
        if session_id:
            set_session_id(session_id)
        team_logger.info("[inprocess] teammate {} started", member_name)
        try:
            return await Runner.run_agent_team(agent_team=teammate, inputs=inputs, session=session_id)
        except asyncio.CancelledError:
            team_logger.info("[inprocess] teammate {} cancelled", member_name)
            raise
        except Exception:
            team_logger.error(
                "[inprocess] teammate {} crashed",
                member_name,
                exc_info=True,
            )
            raise

    task = run_ctx.run(asyncio.get_running_loop().create_task, _run())

    handle = InProcessSpawnHandle(
        process_id=f"inproc-{member_name}",
        _task=task,
    )
    team_logger.info(
        "[inprocess] spawned teammate {} as task {}",
        member_name,
        handle.process_id,
    )
    return handle
