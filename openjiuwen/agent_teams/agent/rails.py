# coding: utf-8
"""Rails for TeamAgent coordination."""

from __future__ import annotations

import asyncio

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail


class FirstIterationGate(AgentRail):
    """Signals when the agent enters its first task-loop iteration.

    External code can ``await gate.wait()`` to block until
    the agent is actually inside its loop and ready to
    receive steer / follow_up inputs.
    """

    def __init__(self) -> None:
        super().__init__()
        self._event = asyncio.Event()

    async def wait(self) -> None:
        """Block until the first iteration has started."""
        await self._event.wait()

    @property
    def is_ready(self) -> bool:
        return self._event.is_set()

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        if not self._event.is_set():
            self._event.set()

    def reset(self) -> None:
        """Reset the gate for a new round."""
        self._event.clear()
