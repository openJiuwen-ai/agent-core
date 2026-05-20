# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""API Key Guard Rail - REJECT mode.

Blocks execution completely when API key detected.
Copy this folder to: ~/guardrail/extensions/ApiKeyGuardReject/
"""

from __future__ import annotations

import re
from typing import Set

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityAllow,
    SecurityCheckContext,
    SecurityReject,
)

FILE_READING_TOOLS: Set[str] = {
    "read_file",
    "bash",
    "grep",
    "glob",
}

API_KEY_PATTERNS = [
    r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN|credential|CREDENTIAL)\s*[=:]\s*[\"']?\S+[\"']?",
    r"\.env\b",
    r"sk-[a-zA-Z0-9]{20,}",
    r"Bearer\s+[a-zA-Z0-9\-_]+",
    r"AKIA[0-9A-Z]{16}",
]


class ApikeyguardrejectRail(BaseSecurityRail):
    """Rail that blocks API keys/secrets in tool results (REJECT mode)."""

    priority = 89
    supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}

    def __init__(self) -> None:
        super().__init__(tool_names=FILE_READING_TOOLS)
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in API_KEY_PATTERNS]

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        inputs = ctx.inputs

        tool_name = getattr(inputs, "tool_name", "") or ""
        if tool_name not in FILE_READING_TOOLS:
            return self.allow()

        tool_result = getattr(inputs, "tool_result", None)
        if tool_result is None:
            return self.allow()

        content = self._extract_content(tool_result)
        if not content:
            return self.allow()

        if self._contains_api_key(content):
            return self.reject(
                message="API key/secret detected in tool result. Operation blocked for security.",
            )

        return self.allow()

    def _extract_content(self, tool_result) -> str:
        if isinstance(tool_result, str):
            return tool_result
        if isinstance(tool_result, dict):
            return tool_result.get("output", "") or tool_result.get("content", "") or ""
        if hasattr(tool_result, "data"):
            data = tool_result.data
            if isinstance(data, str):
                return data
            if isinstance(data, dict):
                return data.get("output", "") or data.get("content", "") or ""
        return str(tool_result) if tool_result else ""

    def _contains_api_key(self, content: str) -> bool:
        for pattern in self._compiled_patterns:
            if pattern.search(content):
                return True
        return False

    async def apply_security_decision(self, security_ctx: SecurityCheckContext, decision) -> None:
        if isinstance(decision, SecurityAllow):
            return

        if isinstance(decision, SecurityReject):
            ctx = security_ctx.callback_ctx
            inputs = ctx.inputs

            error_msg = decision.message or "Blocked by API key guard rail"
            tool_call = getattr(inputs, "tool_call", None)
            tool_call_id = tool_call.id if tool_call else ""

            inputs.tool_result = error_msg
            inputs.tool_msg = ToolMessage(
                content=error_msg,
                tool_call_id=tool_call_id,
            )
            return

        await super().apply_security_decision(security_ctx, decision)


__all__ = ["ApikeyguardrejectRail"]