# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Spawn a teammate as an in-process coroutine (asyncio.Task)."""

from __future__ import annotations

import asyncio
import contextvars
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
)

from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
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
    from openjiuwen.agent_teams.context import set_session_id
    from openjiuwen.core.runner.runner import Runner
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    spec = team_agent.spec

    agent_spec = spec.agents.get(ctx.role.value) or spec.agents["leader"]
    team_name = (ctx.team_spec.team_name if ctx.team_spec else None) or spec.team_name
    card_id = f"{team_name}_{ctx.member_name}" if ctx.member_name else "unknown"
    card = agent_spec.card or AgentCard(
        id=card_id,
        name=ctx.member_name or "unknown",
        description=f"Teammate: {ctx.desc}" if ctx.desc else "Teammate",
    )

    teammate = _TeamAgent(card)
    # Share the leader's team-level workspace manager BEFORE configure.
    team_agent.share_workspace_cache_with(teammate)
    # Share team-level swarmflow handles BEFORE configure so the teammate's
    # setup_agent wires the leader's governor / budget / resolver instances
    # (one L1/L3 authority and one token pool per team).
    team_agent.share_swarmflow_with(teammate)
    teammate.configure(spec, ctx)
    kv_cache_hooks.share_registry_with_teammate(team_agent, teammate)
    # Attach the leader's session-level background task controller AFTER
    # configure (before it the teammate has no harness). A leader without a
    # controller attaches nothing.
    controller = team_agent.background_task_controller
    if controller is not None:
        teammate.set_background_task_controller(controller)
    # Share the leader's checkpoint namespace after configure.
    team_agent.share_checkpoints_with(teammate)
    backend = teammate.team_backend
    set_store = getattr(backend, "set_store_checkpoint_fn", None) if backend is not None else None
    set_checkpoint = getattr(team_agent, "set_checkpoint", None)
    if callable(set_store) and callable(set_checkpoint):
        set_store(set_checkpoint)

    # Empty query means "no first round": the teammate comes up, subscribes,
    # and idles until a real mailbox message arrives. Only a genuine
    # first-start instruction drives an initial harness.send (gated in
    # ``TeamAgent.invoke`` / ``stream``).
    inputs: dict[str, Any] = {"query": initial_message or ""}

    member_name = ctx.member_name

    # Copy current context so session_id propagates into the new task.
    run_ctx = contextvars.copy_context()

    async def _run() -> Any:
        if session_id:
            set_session_id(session_id)

        team_logger.info("[inprocess] teammate {} started", member_name)
        try:
            # Spawned teammates are not leaders and never enter the pool —
            # ``member=True`` skips activate/dispatch (leader-only pool invariant).
            return await Runner.run_agent_team(teammate, inputs, member=True, session=session_id)
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
        agent_ref=teammate,
    )
    team_logger.info(
        "[inprocess] spawned teammate {} as task {}",
        member_name,
        handle.process_id,
    )
    return handle
