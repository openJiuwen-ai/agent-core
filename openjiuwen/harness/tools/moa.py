# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""On-demand reference consultation using the trusted MoA model window."""

from __future__ import annotations

from typing import Any, AsyncIterator

from openjiuwen.core.foundation.tool import Tool, ToolExposure
from openjiuwen.harness.moa.config import MoAConfig
from openjiuwen.harness.moa.consultation import prepare_reference
from openjiuwen.harness.moa.state import active_moa
from openjiuwen.harness.prompts.tools import ToolCardBuildOptions, build_tool_card
from openjiuwen.harness.prompts.tools.moa import CONSULT_TOOL
from openjiuwen.harness.tools.base_tool import ToolOutput


class ConsultReferenceModels(Tool):
    """Zero-argument tool; the rail supplies the trusted model window."""

    def __init__(self, config: MoAConfig, language: str = "cn") -> None:
        card = build_tool_card(
            CONSULT_TOOL,
            CONSULT_TOOL,
            language=language,
            options=ToolCardBuildOptions(parallel_safe=False, idempotent=False),
        )
        card.exposure = ToolExposure.DIRECT
        super().__init__(card)
        self._config = config

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        state = active_moa.get()
        unavailable = not isinstance(inputs, dict) or bool(inputs)
        if state is not None:
            unavailable = unavailable or state.closed or not state.baseline
        if state is None or unavailable:
            return ToolOutput(success=False, error="Consultation is unavailable for this call.")
        if state.completed_turns < 1:
            return ToolOutput(success=False, error="Complete one primary-model turn before consulting.")
        if state.consultation_rejection:
            return ToolOutput(success=False, error=state.consultation_rejection)
        target = (state.active_key[0], state.active_key[1] + 1) if state.active_key is not None else None
        success_count = await prepare_reference(self._config, state, state.baseline, target)
        if success_count is None:
            return ToolOutput(success=False, error="Reference-call budget exhausted.")
        return ToolOutput(
            success=bool(success_count),
            data={"reference_count": len(self._config.reference_models), "success_count": success_count},
            error=None if success_count else "All reference models failed.",
        )

    async def stream(  # type: ignore[override]
        self, inputs: dict[str, Any], **kwargs: Any
    ) -> AsyncIterator[ToolOutput]:
        yield await self.invoke(inputs, **kwargs)
