# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool Reject Example - Demonstrates BEFORE/AFTER reject behavior difference.

This rail shows the different reject behaviors for tool events:
- BEFORE_TOOL_CALL: skip_tool, agent continues (can try other approach)
- AFTER_TOOL_CALL: force_finish, agent terminates (data leaked)

NO INTERRUPT - Direct reject when secret detected.

Use this for:
1. Understanding reject behavior difference
2. Testing tool security without HITL
3. Simple security enforcement without user approval

Copy this folder to: ~/guardrail/extensions/ToolRejectExample/
"""

from __future__ import annotations

import json
import re

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
    ToolCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail  # noqa: F401
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityCheckContext,
)

SENSITIVE_PATTERNS = [
    r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN|credential|CREDENTIAL)\s*[=:]\s*[\"']?\S+[\"']?",
    r"sk-[a-zA-Z0-9_-]{20,}",
    r"Bearer\s+[a-zA-Z0-9\-_]+",
    r"AKIA[0-9A-Z]{16}",
]

TOOL_WHITELIST = [
    "read_file",
    "bash",
    "grep",
    "glob",
    "write_file",
]


class ToolrejectexampleRail(BaseSecurityRail):
    """Example rail demonstrating BEFORE/AFTER reject behavior.

    BEFORE_TOOL_CALL reject:
        - Secret detected in tool_args
        - Action: skip_tool (tool not executed)
        - ToolMessage: "Tool execution skipped"
        - Agent continues (can try other tools/approach)

    AFTER_TOOL_CALL reject:
        - Secret detected in tool_result
        - Action: force_finish (agent terminates)
        - ToolMessage: "Blocked by security rail"
        - Agent stops, returns error to user

    No interrupt - direct reject when secret found.
    """

    priority = 88
    supported_events = {
        AgentCallbackEvent.BEFORE_TOOL_CALL,
        AgentCallbackEvent.AFTER_TOOL_CALL,
    }

    def __init__(self) -> None:
        super().__init__()
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in SENSITIVE_PATTERNS]

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        inputs = ctx.inputs
        event = security_ctx.event

        if not isinstance(inputs, ToolCallInputs):
            return self.allow()

        tool_name = inputs.tool_name or ""

        if event == AgentCallbackEvent.BEFORE_TOOL_CALL:
            return self._check_before(inputs, tool_name)

        if event == AgentCallbackEvent.AFTER_TOOL_CALL:
            return self._check_after(inputs, tool_name)

        return self.allow()

    def _check_before(self, inputs: ToolCallInputs, tool_name: str):
        """Check tool arguments before execution.

        If secret found:
        - Return reject (base will call _skip_tool)
        - Agent continues, can try other approach
        """
        if tool_name not in TOOL_WHITELIST:
            return self.allow()

        tool_args = inputs.tool_args
        if tool_args is None:
            return self.allow()

        content = self._extract_args_content(tool_args)
        if not content:
            return self.allow()

        if self._contains_secret(content):
            return self.reject(
                message=f"Secret detected in {tool_name} arguments. Tool skipped.",
            )

        return self.allow()

    def _check_after(self, inputs: ToolCallInputs, tool_name: str):
        """Check tool result after execution.

        If secret found:
        - Return reject (base will call force_finish)
        - Agent terminates, returns error
        """
        if tool_name not in TOOL_WHITELIST:
            return self.allow()

        tool_result = inputs.tool_result
        if tool_result is None:
            return self.allow()

        content = self._extract_result_content(tool_result)
        if not content:
            return self.allow()

        if self._contains_secret(content):
            return self.reject(
                message=f"Secret detected in {tool_name} result. Agent terminated.",
            )

        return self.allow()

    def _extract_args_content(self, tool_args) -> str:
        """Extract content from tool arguments."""
        if isinstance(tool_args, str):
            return tool_args
        if isinstance(tool_args, dict):
            return json.dumps(tool_args)
        return str(tool_args) if tool_args else ""

    def _extract_result_content(self, tool_result) -> str:
        """Extract content from tool result."""
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

    def _contains_secret(self, content: str) -> bool:
        """Check if content contains sensitive patterns."""
        for pattern in self._compiled_patterns:
            if pattern.search(content):
                return True
        return False


__all__ = ["ToolrejectexampleRail"]