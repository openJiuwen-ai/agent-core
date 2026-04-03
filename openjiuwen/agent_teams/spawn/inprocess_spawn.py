# coding: utf-8
"""Spawn a teammate as an in-process coroutine (asyncio.Task)."""

from __future__ import annotations

import asyncio
import contextvars
from typing import TYPE_CHECKING, Any, Optional

from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.team_agent import TeamAgent
    from openjiuwen.agent_teams.schema.team import TeamMemberSpec


async def inprocess_spawn(
    team_agent: "TeamAgent",
    member_spec: "TeamMemberSpec",
    *,
    initial_message: Optional[str] = None,
    session_id: Optional[str] = None,
) -> InProcessSpawnHandle:
    """Spawn a teammate TeamAgent as a coroutine in the current process.

    Mirrors the subprocess path (Runner.spawn_agent -> child_process) but
    runs everything within the same event loop.

    Args:
        team_agent: The leader TeamAgent that owns the team spec.
        member_spec: Specification for the teammate to create.
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
    context = team_agent._build_member_context(member_spec)

    agent_spec = spec.agents.get(context.role.value) or spec.agents["leader"]
    card = agent_spec.card or AgentCard(
        id=context.member_id or "unknown",
        name=context.member_spec.name if context.member_spec else "unknown",
        description=f"Teammate for domain {context.domain}",
    )

    teammate = _TeamAgent(card)
    await teammate.configure_team(spec, context)

    query = initial_message or "Join the team and wait for your first assignment."
    inputs: dict[str, Any] = {"query": query}

    # Copy current context so session_id propagates into the new task.
    ctx = contextvars.copy_context()

    async def _run() -> Any:
        if session_id:
            set_session_id(session_id)
        team_logger.info(
            "[inprocess] teammate {} started",
            member_spec.member_id,
        )
        try:
            return await Runner.run_agent(agent=teammate, inputs=inputs, session=session_id)
        except asyncio.CancelledError:
            team_logger.info("[inprocess] teammate {} cancelled", member_spec.member_id)
            raise
        except Exception:
            team_logger.error(
                "[inprocess] teammate {} crashed",
                member_spec.member_id,
                exc_info=True,
            )
            raise

    task = ctx.run(asyncio.get_running_loop().create_task, _run())

    handle = InProcessSpawnHandle(
        process_id=f"inproc-{member_spec.member_id}",
        _task=task,
    )
    team_logger.info(
        "[inprocess] spawned teammate {} as task {}",
        member_spec.member_id,
        handle.process_id,
    )
    return handle
