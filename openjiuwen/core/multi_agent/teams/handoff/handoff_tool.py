# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HandoffTool -- tool injected by HandoffTeam to signal agent-to-agent transfers."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
    HANDOFF_MESSAGE_KEY,
    HANDOFF_REASON_KEY,
    HANDOFF_TARGET_KEY,
)


class HandoffTool(Tool):
    """Tool that signals control transfer to a target agent.

    Injected automatically by :class:`~HandoffTeam` into every agent's AbilityManager.
    The tool name exposed to the LLM is ``transfer_to_{target_id}``.

    Args:
        target_id:          ID of the agent to hand off to.
        target_description: Optional description of the target agent appended to
                            the tool description shown to the LLM.
    """

    def __init__(self, target_id: str, target_description: str = "") -> None:
        tool_name = f"transfer_to_{target_id}"
        description = f"Transfer the current task to {target_id} for processing."
        if target_description:
            description += f" {target_description}"

        card = ToolCard(
            id=tool_name,
            name=tool_name,
            description=description,
            input_params={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Reason for handoff: briefly explain why the task is being transferred.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Context information passed to the next agent (optional).",
                    },
                },
                "required": ["reason"],
            },
        )
        super().__init__(card)
        self._target_id = target_id

    async def invoke(self, inputs: Any, **kwargs: Any) -> dict:
        """Return a handoff signal payload dict consumed by :func:`~handoff_signal.extract_handoff_signal`.

        Args:
            inputs: Tool arguments from the LLM (dict or JSON string with ``reason`` / ``message`` keys).

        Returns:
            Dict with ``__handoff_to__``, ``__handoff_message__``, and ``__handoff_reason__`` keys.
        """
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs)
            except ValueError:
                inputs = {"reason": inputs}
        if not isinstance(inputs, dict):
            inputs = {}
        return {
            HANDOFF_TARGET_KEY: self._target_id,
            HANDOFF_MESSAGE_KEY: inputs.get("message") or "",
            HANDOFF_REASON_KEY: inputs.get("reason") or "",
        }

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[dict]:
        """Streaming variant -- yields the single :meth:`invoke` result."""
        yield await self.invoke(inputs, **kwargs)
