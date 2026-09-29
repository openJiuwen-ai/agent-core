# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tool execution tracking rail.

Emits ``tool_call`` and ``tool_result`` chunks into the session
stream so the CLI renderer can display tool activity in Claude Code
style (``● ToolName(args)`` / ``⎿ result summary``).
"""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent.ability_manager import resolve_tool_result_text
from openjiuwen.core.single_agent.rail.base import AgentRail, ToolCallInputs


class ToolTrackingRail(AgentRail):
    """Emit tool call/result chunks for UI rendering.

    This rail fires at low priority so that other rails (e.g.
    ``SecurityRail``) can modify or reject tool calls before
    the UI is notified.
    """

    priority = 5  # very low — run after everything else

    @staticmethod
    def _build_tool_result_payload(
        tool_name: str,
        tool_result: Any,
    ) -> dict[str, Any]:
        """Build the UI payload for a completed tool call.

        ``tool_result`` is a compatibility field: ``str()`` of the structured
        result, which existing consumers still parse. Displays read
        ``rendered_result`` (see ``after_tool_call``) instead. A structured
        ``structured_result`` field is intentionally not emitted yet; once UI
        consumers migrate to structured data, this string field and the
        parsing built on it are removed end to end.
        """
        payload: dict[str, Any] = {
            "tool_result": str(tool_result)
            if tool_result is not None
            else "",
        }
        if tool_name != "read_file" or tool_result is None:
            return payload

        data = getattr(tool_result, "data", None)
        if not isinstance(data, dict):
            return payload

        content = data.get("content")
        if content is not None:
            if isinstance(content, bytes):
                payload["tool_result"] = content.decode(
                    "utf-8", errors="replace"
                )
            else:
                payload["tool_result"] = str(content)

        line_count = data.get("line_count")
        if line_count is not None:
            try:
                payload["line_count"] = int(line_count)
            except (TypeError, ValueError):
                pass

        return payload

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
        """Write a ``tool_result`` chunk when a tool finishes.

        Besides the compatibility fields, the chunk carries ``rendered_result``:
        the exact text the model reads for this call. The rail's priority (5)
        is below every rail that rewrites the tool message, so the text is final.
        """
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

        payload: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_args": tool_args,
            **self._build_tool_result_payload(
                tool_name, tool_result
            ),
        }
        if isinstance(inputs, ToolCallInputs):
            rendered_result = resolve_tool_result_text(inputs, ctx.exception)
            if rendered_result is not None:
                payload["rendered_result"] = rendered_result

        await session.write_stream(
            OutputSchema(
                type="tool_result",
                index=0,
                payload=payload,
            )
        )
