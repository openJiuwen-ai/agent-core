# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async-tool host for external CLI members launching swarmflow runs.

``SwarmflowTool`` reads its launching host through duck-typed attributes on
``parent_agent`` (``launch_async_tool`` / ``send(immediate=False)`` injection
via the async-tool runtime / ``build_context.member_name`` / ``model`` /
``background_task_controller``). A NativeHarness provides all five; an external
CLI member has none of them — its brain is a CLI subprocess — yet its TeamAgent
shell, tool gateway and the team's ``SwarmflowInfra`` all live in this process,
so a run can still be hosted here and its completion still delivered to the
member.

This module is that host: a per-member :class:`AsyncToolRuntime` whose
``inject`` callback forwards into the member's
``ExternalHarnessMemberRuntime.send(immediate=False)`` (the same
capability-aware steer / follow-up surface coordination narration uses), plus
the small attribute surface ``SwarmflowTool`` probes on its ``parent_agent``.

The tool itself is the one and only ``SwarmflowTool`` — same name, same schema,
same invoke. Only the host under it differs; control-plane registration goes to
the SAME session controller (so pause / resume / ``stop_by_owner`` reach these
runs exactly like in-process ones) and budget / governor / journal authority
stays in the team process.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.harness.async_tools import AsyncToolRuntime


class ExternalSwarmflowHost:
    """The ``parent_agent`` SwarmflowTool sees for an external CLI member.

    Attributes:
        member_runtime: The member's external runtime; the inject callback
            forwards completion text into it as a non-immediate send (follow-up
            when the member is in a turn, a fresh turn when idle) — the CLI-side
            equivalent of NativeHarness's ``send(immediate=False)`` completion
            injection. Wired after construction (``bind_member_runtime``): the
            spawn path builds the gateway before the CLI runtime exists.
        build_context: A derived per-member context carrying the member's
            name (run attribution / narration routing key) and the team
            extras (worker model resolver pool, expert tables) the tool reads
            at run time. The platform subclass is preserved through
            ``derive``, so worker harness builds see the real platform shape.
        background_task_controller: The session-level controller shared with
            every in-process member; run tickets register here so pause /
            stop / teardown ``stop_by_owner`` work unchanged.
        model: Always ``None`` — the CLI member's own model lives in its
            subprocess and never matches the team pool, so worker model
            resolution falls back to the worker base spec's model instead of
            a wrong default.
    """

    def __init__(
        self,
        *,
        build_context: Any,
        background_task_controller: Any = None,
    ) -> None:
        self._member_runtime: Any = None
        self.build_context = build_context
        self.background_task_controller = background_task_controller
        self.async_tool_runtime = AsyncToolRuntime(inject=self._inject)

    def bind_member_runtime(self, member_runtime: Any) -> None:
        """Attach the member's external runtime (spawn-path second phase)."""
        self._member_runtime = member_runtime

    @property
    def model(self) -> None:
        """No default model: workers resolve from the base spec / pool hints."""
        return None

    async def _inject(self, text: str) -> None:
        """Deliver a background run's completion text to the CLI member."""
        if self._member_runtime is None:
            from openjiuwen.core.common.logging import team_logger

            team_logger.debug("[swarmflow-ext] completion dropped: member runtime not bound")
            return
        await self._member_runtime.send(text, immediate=False)

    def launch_async_tool(
        self,
        task_id: str,
        coro_factory: Any,
        *,
        tool_name: str,
        description: str,
        format_completed: Any = None,
        format_failed: Any = None,
    ) -> None:
        """Schedule a background task on this member's runtime (host seam)."""
        self.async_tool_runtime.launch(
            task_id,
            coro_factory,
            tool_name=tool_name,
            description=description,
            format_completed=format_completed,
            format_failed=format_failed,
        )


__all__ = ["ExternalSwarmflowHost"]
