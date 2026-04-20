"""Tool execution tracking rail.

Emits ``tool_call`` and ``tool_result`` chunks into the session
stream so the CLI renderer can display tool activity in Claude Code
style (``● ToolName(args)`` / ``⎿ result summary``).
"""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentRail


class ToolTrackingRail(AgentRail):
    """Emit tool call/result chunks for UI rendering.

    This rail fires at low priority so that other rails (e.g.
    ``SecurityRail``) can modify or reject tool calls before
    the UI is notified.
    """

    priority = 5  # very low — run after everything else

    async def before_tool_call(self, ctx: Any) -> None:
        """Write a ``tool_call`` chunk when a tool starts."""
        session = ctx.session
        if session is None:
            return

        inputs = ctx.inputs
        tool_name = getattr(inputs, "tool_name", "")
        tool_args = getattr(inputs, "tool_args", "")

        # Normalize args to a dict if it's a JSON string
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except (json.JSONDecodeError, TypeError):
                pass

        await session.write_stream(
            OutputSchema(
                type="tool_call",
                index=0,
                payload={
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                },
            )
        )

    async def after_tool_call(self, ctx: Any) -> None:
        """Write a ``tool_result`` chunk when a tool finishes."""
        session = ctx.session
        if session is None:
            return

        inputs = ctx.inputs
        tool_name = getattr(inputs, "tool_name", "")
        tool_result = getattr(inputs, "tool_result", None)
        tool_args = getattr(inputs, "tool_args", "")

        # Normalize args
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except (json.JSONDecodeError, TypeError):
                pass

        await session.write_stream(
            OutputSchema(
                type="tool_result",
                index=0,
                payload={
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "tool_result": str(tool_result)
                    if tool_result is not None
                    else "",
                },
            )
        )
