# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""API Key Guard Rail - INTERRUPT mode.

Requires human approval when API key detected (HITL).

Events:
    BEFORE_TOOL_CALL: Interrupt if tool_args contains secret
    AFTER_TOOL_CALL: Interrupt if tool_result contains secret

Reject behavior:
    BEFORE reject: Skip tool execution, agent continues
    AFTER reject: Force finish agent, return error

Copy this folder to: ~/guardrail/extensions/ApiKeyGuardInterrupt/
"""

from __future__ import annotations

import json
import re
from typing import Set

from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
    ToolCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail  # noqa: F401
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityCheckContext,
)

FILE_READING_TOOLS: Set[str] = {
    "read_file",
    "bash",
    "grep",
    "glob",
}

API_KEY_PATTERNS = [
    r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN|credential|CREDENTIAL)\s*[=:]\s*[\"']?\S+[\"']?",
    r"sk-[a-zA-Z0-9_-]{20,}",
    r"Bearer\s+[a-zA-Z0-9\-_]+",
    r"AKIA[0-9A-Z]{16}",
]


class ApikeyguardinterruptRail(BaseSecurityRail):
    """Rail that requires human approval for API key operations (INTERRUPT mode).

    BEFORE_TOOL_CALL: Check tool arguments for secrets
        - Interrupt if secret in args
        - Reject: skip tool, agent continues (can try other approach)
        - Approve: execute tool

    AFTER_TOOL_CALL: Check tool result for secrets
        - Interrupt if secret in result
        - Reject: force finish agent (data leaked)
        - Approve: continue with result
    """

    priority = 89
    supported_events = {
        AgentCallbackEvent.BEFORE_TOOL_CALL,
        AgentCallbackEvent.AFTER_TOOL_CALL,
    }

    def __init__(self) -> None:
        super().__init__(tool_names=FILE_READING_TOOLS)
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in API_KEY_PATTERNS]

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        inputs = ctx.inputs
        event = security_ctx.event

        if not isinstance(inputs, ToolCallInputs):
            return self.allow()

        tool_name = inputs.tool_name or ""
        tool_call = inputs.tool_call
        tool_call_id = tool_call.id if tool_call else "unknown"

        if tool_name not in FILE_READING_TOOLS:
            return self.allow()

        if event == AgentCallbackEvent.BEFORE_TOOL_CALL:
            return self._check_before(inputs, tool_name, tool_call_id, security_ctx)

        if event == AgentCallbackEvent.AFTER_TOOL_CALL:
            return self._check_after(inputs, tool_name, tool_call_id, security_ctx)

        return self.allow()

    def _check_before(
        self,
        inputs: ToolCallInputs,
        tool_name: str,
        tool_call_id: str,
        security_ctx: SecurityCheckContext,
    ):
        """Check tool arguments before execution."""
        auto_confirm_key = f"api_key_guard:{tool_name}:before"

        resume_decision = self._handle_interrupt_resume(security_ctx, auto_confirm_key)
        if resume_decision is not None:
            return resume_decision

        tool_args = inputs.tool_args
        if tool_args is None:
            return self.allow()

        content = self._extract_args_content(tool_args)
        if not content:
            return self.allow()

        if self._contains_api_key(content):
            return self.interrupt(
                InterruptRequest(
                    message="API key/secret detected in tool arguments. Approve execution?",
                    payload_schema={
                        "type": "object",
                        "properties": {
                            "approved": {"type": "boolean"},
                            "feedback": {"type": "string"},
                            "auto_confirm": {"type": "boolean", "description": "Remember approval for this tool"},
                        },
                        "required": ["approved"],
                    },
                    auto_confirm_key=auto_confirm_key,
                    ui_options=[
                        {"label": "Approve", "description": "Execute this tool call", "value": "approve"},
                        {"label": "Always Allow", "description": "Remember approval for this tool", "value": "always_allow"},
                        {"label": "Reject", "description": "Skip tool execution", "value": "reject"},
                    ],
                ),
                subject_id=tool_call_id,
            )

        return self.allow()

    def _check_after(
        self,
        inputs: ToolCallInputs,
        tool_name: str,
        tool_call_id: str,
        security_ctx: SecurityCheckContext,
    ):
        """Check tool result after execution."""
        auto_confirm_key = f"api_key_guard:{tool_name}:after"

        resume_decision = self._handle_interrupt_resume(security_ctx, auto_confirm_key)
        if resume_decision is not None:
            return resume_decision

        tool_result = inputs.tool_result
        if tool_result is None:
            return self.allow()

        content = self._extract_result_content(tool_result)
        if not content:
            return self.allow()

        if self._contains_api_key(content):
            return self.interrupt(
                InterruptRequest(
                    message="API key/secret detected in tool result. Approve to continue?",
                    payload_schema={
                        "type": "object",
                        "properties": {
                            "approved": {"type": "boolean"},
                            "feedback": {"type": "string"},
                            "auto_confirm": {"type": "boolean", "description": "Remember approval for this tool"},
                        },
                        "required": ["approved"],
                    },
                    auto_confirm_key=auto_confirm_key,
                    ui_options=[
                        {"label": "Approve", "description": "Continue with this result", "value": "approve"},
                        {"label": "Always Allow", "description": "Remember approval for this tool", "value": "always_allow"},
                        {"label": "Reject", "description": "Stop execution and return error", "value": "reject"},
                    ],
                ),
                subject_id=tool_call_id,
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

    def _contains_api_key(self, content: str) -> bool:
        """Check if content contains API key patterns."""
        for pattern in self._compiled_patterns:
            if pattern.search(content):
                return True
        return False


__all__ = ["ApikeyguardinterruptRail"]