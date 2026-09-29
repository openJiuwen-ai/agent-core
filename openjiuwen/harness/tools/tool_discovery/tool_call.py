# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Fixed model-visible wrapper for executing discovered deferred tools."""

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from pydantic import BaseModel, Field, PrivateAttr

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput


class ToolCallInput(BaseModel):
    """Arguments accepted by the fixed ``tool_call`` wrapper."""

    name: str = Field(..., description="Exact tool name returned by tool_search")
    args: Dict[str, Any] = Field(
        ...,
        description="Arguments matching the schema returned by tool_search",
    )


class RelayedToolOutput(ToolOutput):
    """Wrapper result that carries the dispatched target's model-facing text.

    The target already went through its own ``render_for_llm`` and any
    AFTER_TOOL_CALL rewrite, so the wrapper relays that text to the model. It
    lives in a private attribute: ``model_dump`` / ``str()`` ignore it, so the
    structured ``tool_result`` streamed to upper layers is exactly the fields.
    """

    _rendered_text: str = PrivateAttr(default="")

    def __init__(
        self,
        *,
        success: bool,
        rendered_text: str,
        data: Any = None,
        error: str | None = None,
    ) -> None:
        super().__init__(success=success, data=data, error=error)
        self._rendered_text = rendered_text

    @property
    def rendered_text(self) -> str:
        """The target tool's rendered model-facing text."""
        return self._rendered_text


class ToolCallTool(Tool):
    """Execute a deferred tool without changing the model-visible tool list.

    The actual execution callback is supplied by ``ProgressiveToolRail``.  It
    receives the callback context from ``AbilityManager`` so the target call can
    reuse the normal tool-rail lifecycle instead of invoking a resource directly.
    """

    TOOL_NAME = "tool_call"
    TOOL_ID = "ToolCallTool"
    accepts_tool_callback_context = True

    def __init__(
        self,
        call_tool: Callable[..., Awaitable[Any]],
        language: str = "cn",
        agent_id: Optional[str] = None,
    ):
        super().__init__(
            build_tool_card(self.TOOL_NAME, self.TOOL_ID, language, agent_id=agent_id)
        )
        self._call_tool = call_tool

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        session = kwargs.get("session")
        callback_context = kwargs.get("_tool_callback_context")
        try:
            parsed = ToolCallInput(**(inputs or {}))
            if callback_context is None:
                raise RuntimeError("tool_call requires an active agent callback context")

            result = await self._call_tool(
                parsed.name,
                parsed.args,
                session,
                callback_context,
            )
            if isinstance(result, ToolOutput):
                return result
            return ToolOutput(
                success=True,
                data={
                    "name": parsed.name,
                    "result": result,
                },
            )
        except ToolInterruptException:
            raise
        except Exception as exc:
            logger.warning(
                "[ProgressiveToolRail] tool_call invoke failed | error=%s",
                str(exc),
            )
            return ToolOutput(success=False, error=str(exc))

    def render_for_llm(self, output: ToolOutput) -> str:
        """Relay the target's rendering once the call reached the target."""
        if isinstance(output, RelayedToolOutput) and output.rendered_text:
            return output.rendered_text
        return super().render_for_llm(output)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None
