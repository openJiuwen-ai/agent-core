# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Mode tools for switching CodeAgent runtime mode.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import Input, Output, Tool
from openjiuwen.harness.prompts.sections.tools import build_tool_card
from openjiuwen.harness.schema.agent_mode import AgentMode

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


_SWITCH_MODE_INVALID_MSG = {
    "en": "Invalid mode '{mode}'. Supported modes: auto, plan.",
    "cn": "无效模式 '{mode}'。支持模式：auto、plan。",
}

_SWITCH_MODE_TO_AUTO_MSG = {
    "en": "Switched mode to auto.",
    "cn": "已切换为 auto 模式。",
}

_SWITCH_MODE_TO_PLAN_MSG = {
    "en": (
        "Switched mode to plan.\n"
        "Next step: call enter_plan_mode to continue the plan workflow."
    ),
    "cn": (
        "已切换为 plan 模式。\n"
        "下一步：调用 enter_plan_mode 继续 Plan 工作流。"
    ),
}


class SwitchModeInput(BaseModel):
    """Input schema for ``switch_mode`` tool."""

    mode: str = Field(..., description="Target mode: auto or plan")


class SwitchModeTool(Tool):
    """Switch session runtime mode between auto and plan.

    Behavior:
    - ``plan``: switch to plan mode and ensure a plan file exists.
    - ``auto``: switch back to auto mode.
    """

    def __init__(self, agent_ref: "DeepAgent", language: str = "cn") -> None:
        super().__init__(
            build_tool_card(
                name="switch_mode",
                tool_id="switch_mode",
                language=language,
            )
        )
        self._agent_ref = agent_ref
        self._language = language

    async def invoke(self, inputs: Input, **kwargs: Any) -> str:
        parsed = SwitchModeInput.model_validate(inputs or {})
        raw_mode = (parsed.mode or "").strip().lower()
        lang = "en" if self._language == "en" else "cn"

        if raw_mode not in (AgentMode.AUTO.value, AgentMode.PLAN.value):
            return _SWITCH_MODE_INVALID_MSG[lang].format(mode=raw_mode)

        session = kwargs.get("session")
        agent = self._agent_ref

        if raw_mode == AgentMode.PLAN.value:
            agent.switch_mode(session, AgentMode.PLAN.value)
            return _SWITCH_MODE_TO_PLAN_MSG[lang]

        agent.switch_mode(session, AgentMode.AUTO.value)
        return _SWITCH_MODE_TO_AUTO_MSG[lang]

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass


__all__ = ["SwitchModeTool"]
