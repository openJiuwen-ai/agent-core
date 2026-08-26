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
    from openjiuwen.agent_teams.fork import ForkContext
    from openjiuwen.agent_teams.schema.team import TeamRuntimeContext


async def inprocess_spawn(
    team_agent: "TeamAgent",
    ctx: "TeamRuntimeContext",
    *,
    initial_message: Optional[str] = None,
    session_id: Optional[str] = None,
    fork_from: "ForkContext | None" = None,
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
    # Share the leader's team-level workspace manager BEFORE configure: the
    # teammate's setup_infra must not create its own manager, and its
    # _assemble_member_workspace reuse check then hits the leader's already
    # built cache — no re-scan of the team-workspace md files.
    # Injected at TeamAgent construction time so it cannot race configure.
    team_agent.share_workspace_cache_with(teammate)
    teammate.configure(spec, ctx)
    kv_cache_hooks.share_registry_with_teammate(team_agent, teammate)

    # Share the leader's checkpoint dict so this teammate's
    # ``checkpoint()`` tool writes into the leader-visible namespace.
    # The store callback routes through the *leader's* ``set_checkpoint``:
    # the dict is shared by reference, so the teammate sees the write
    # immediately, while the leader is the single writer that mirrors it
    # into the session per-team namespace (avoiding independent State
    # objects clobbering each other at ``post_run``).
    team_agent.share_checkpoints_with(teammate)
    if teammate.team_backend is not None:
        teammate.team_backend.set_store_checkpoint_fn(
            team_agent.set_checkpoint
        )

    # Fork context injection: seed the teammate's context engine with the
    # fork source's conversation history so it inherits prior understanding.
    if fork_from and not fork_from.is_empty():
        native = teammate.resources.harness.get_deep_agent()
        await native.create_new_context_engine(
            session_id=session_id,
            messages=fork_from.to_messages(),
        )
        team_logger.debug(
            "[fork] inprocess_spawn: injected %d messages into %s compact_split=%s",
            len(fork_from.messages), ctx.member_name, fork_from.compact_split,
        )
        team_logger.info(
            "[fork] %d messages injected into %s",
            len(fork_from.messages), ctx.member_name,
        )
        # Compaction: compress the side opposite ``compact_direction`` at the
        # split point.
        if fork_from.compact_split is not None:
            from openjiuwen.agent_teams.fork_compact import compact_context

            await compact_context(
                native, split_at=fork_from.compact_split,
                session_id=session_id,
                direction=fork_from.compact_direction,
            )
    else:
        team_logger.debug(
            "[fork] inprocess_spawn: NO fork injection for %s (fork_from=%s)",
            ctx.member_name, "present" if fork_from else "None",
        )

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
