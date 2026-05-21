# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""API Key Guard Rail - ALERT mode.

Allows execution but alerts user when API key detected.
Demonstrates three display_mode options: popup, history, inline.

Copy this folder to: ~/guardrail/extensions/ApiKeyGuardAlert/
"""

from __future__ import annotations

import re
from typing import Set

from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness.rails.base import DeepAgentRail  # noqa: F401
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityAlert,
    SecurityAlertLevel,
    SecurityAllow,
    SecurityCheckContext,
)

FILE_READING_TOOLS: Set[str] = {
    "read_file",
    "bash",
    "grep",
    "glob",
    "read",
}

API_KEY_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN)\s*[=:]\s*[\"']?\S+[\"']?", re.IGNORECASE),
    re.compile(r"Bearer\s+[a-zA-Z0-9\-_]+", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE),
]


class ApikeyguardalertRail(BaseSecurityRail):
    """Rail that alerts on API keys/secrets but allows execution (ALERT mode).

    Demonstrates SecurityAlert with configurable display_mode:
    - popup: Toast/popup notification (default)
    - history: Insert into chat history
    - inline: Stream output in real-time
    """

    priority = 89
    supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}

    def __init__(
        self,
        display_mode: str = "popup",
        alert_level: SecurityAlertLevel = SecurityAlertLevel.WARNING,
    ) -> None:
        super().__init__(tool_names=FILE_READING_TOOLS)
        self._display_mode = display_mode
        self._alert_level = alert_level

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
            return self.alert(
                message=f"API key/secret detected in {tool_name} result. Execution allowed but flagged.",
                level=self._alert_level,
                alert_type="api_key_leakage",
                display_mode=self._display_mode,
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
        for pattern in API_KEY_PATTERNS:
            if pattern.search(content):
                return True
        return False


__all__ = ["ApikeyguardalertRail"]