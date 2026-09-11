# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tool-call lifecycle rail attached to the ReAct agent.

Modeled on ``openjiuwen.harness.cli.rails.tool_tracker.ToolTrackingRail``,
which emits the same ``tool_call``/``tool_result`` OutputSchema chunks but
stringifies ``tool_result`` via ``str(...)`` for CLI text rendering. That
stringification would mangle ``show_card``'s structured return value (a
Python dict embedding A2UI/GenUI messages), so this rail keeps
``tool_result`` as the raw returned object instead -- see ``ws_session.py``,
which reads ``payload["tool_result"]["genui"]`` off of it.
"""

from typing import Any

from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentRail


def _tool_call_id(ctx: Any) -> Any:
    """The originating ToolCall's id, so parallel calls to the same tool

    can be told apart client-side. The agent can run several calls to the
    same tool concurrently (e.g. multiple fetch_webpage calls in one
    iteration) -- without a stable per-call id, the client has no reliable
    way to match a later tool_result/tool_error back to the right
    "started" chip; it has to guess by name+recency, which silently
    mismatches under concurrency.
    """
    return getattr(getattr(ctx.inputs, "tool_call", None), "id", None)


class A2uiToolEventRail(AgentRail):
    """Emits tool_call / tool_result chunks with untouched payloads."""

    priority = 5  # run after other rails, mirroring ToolTrackingRail

    async def before_tool_call(self, ctx: Any) -> None:
        session = ctx.session
        if session is None:
            return
        await session.write_stream(
            OutputSchema(
                type="tool_call",
                index=0,
                payload={
                    "tool_call_id": _tool_call_id(ctx),
                    "tool_name": getattr(ctx.inputs, "tool_name", ""),
                    "tool_args": getattr(ctx.inputs, "tool_args", ""),
                },
            )
        )

    async def after_tool_call(self, ctx: Any) -> None:
        session = ctx.session
        if session is None:
            return
        await session.write_stream(
            OutputSchema(
                type="tool_result",
                index=0,
                payload={
                    "tool_call_id": _tool_call_id(ctx),
                    "tool_name": getattr(ctx.inputs, "tool_name", ""),
                    "tool_args": getattr(ctx.inputs, "tool_args", ""),
                    "tool_result": getattr(ctx.inputs, "tool_result", None),
                },
            )
        )

    async def on_tool_exception(self, ctx: Any) -> None:
        # AgentRail fires this instead of after_tool_call when the tool
        # raises -- without this override a failed tool call produces
        # `tool_call` (started) and then nothing at all, no distinguishable
        # failure signal reaches the client.
        session = ctx.session
        if session is None:
            return
        await session.write_stream(
            OutputSchema(
                type="tool_error",
                index=0,
                payload={
                    "tool_call_id": _tool_call_id(ctx),
                    "tool_name": getattr(ctx.inputs, "tool_name", ""),
                    "tool_args": getattr(ctx.inputs, "tool_args", ""),
                    "message": str(ctx.exception) if ctx.exception else "Tool execution failed.",
                },
            )
        )
