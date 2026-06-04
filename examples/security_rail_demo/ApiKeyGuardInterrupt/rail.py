# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""API Key Guard Rail - INTERRUPT mode.

Requires human approval when API key detected (HITL).

Events:
    BEFORE_TOOL_CALL: Interrupt if tool_args contains secret
    AFTER_TOOL_CALL: Interrupt if tool_result contains secret

Reject behavior:
    BEFORE reject: Skip tool execution, agent continues
    AFTER reject: Skip tool execution, agent continues

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

DETECTION_RULES = [
    {"pattern": r"sk-[a-zA-Z0-9_-]{20,}", "type": "api_key_openai"},
    {"pattern": r"AKIA[0-9A-Z]{16}", "type": "api_key_aws"},
    {"pattern": r"Bearer\s+[a-zA-Z0-9\-_]+", "type": "bearer_token"},
    {
        "pattern": r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN|credential|CREDENTIAL)\s*[=:]\s*[\"']?\S+[\"']?",
        "type": "secret_generic",
    },
]


class ApikeyguardinterruptRail(BaseSecurityRail):
    """Rail that requires human approval for API key operations (INTERRUPT mode).

    BEFORE_TOOL_CALL: Check tool arguments for secrets
        - Interrupt if secret in args
        - Reject: skip tool, agent continues (can try other approach)
        - Approve: execute tool

    AFTER_TOOL_CALL: Check tool result for secrets
        - Interrupt if secret in result
        - Reject: skip tool, agent continues
        - Approve: continue with result
    """

    priority = 89
    supported_events = {
        AgentCallbackEvent.BEFORE_TOOL_CALL,
        AgentCallbackEvent.AFTER_TOOL_CALL,
    }

    def __init__(self) -> None:
        super().__init__(tool_names=FILE_READING_TOOLS)
        self._compiled_rules = [
            {"pattern": re.compile(r["pattern"], re.IGNORECASE), "type": r["type"]} for r in DETECTION_RULES
        ]

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
        """Check tool arguments before execution.

        Multi-type handling: detect all types, separate confirmed/unconfirmed,
        interrupt showing ALL unconfirmed types for batch approval.
        """
        tool_args = inputs.tool_args
        if tool_args is None:
            return self.allow()

        content = self._extract_args_content(tool_args)
        if not content:
            return self.allow()

        detected_types = self._detect_all_types(content)
        if not detected_types:
            return self.allow()

        auto_confirm_config = security_ctx.auto_confirm_config
        unconfirmed_types = []

        for detection_type in detected_types:
            key = f"apikeyguardinterrupt:{detection_type}:before"
            if not self._is_auto_confirmed(auto_confirm_config, key):
                unconfirmed_types.append(detection_type)

        if not unconfirmed_types:
            return self.allow()

        user_input = security_ctx.user_input
        if user_input is not None:
            approved = False
            auto_confirm = False

            if isinstance(user_input, dict):
                approved = user_input.get("approved", False)
                auto_confirm = user_input.get("auto_confirm", False)
            elif hasattr(user_input, "approved"):
                approved = user_input.approved
                auto_confirm = getattr(user_input, "auto_confirm", False)

            if approved:
                if auto_confirm:
                    for detection_type in unconfirmed_types:
                        key = f"apikeyguardinterrupt:{detection_type}:before"
                        self._store_auto_confirm(security_ctx.callback_ctx, key)
                return self.allow()
            else:
                return self.reject(message="用户拒绝")

        first_type_key = f"apikeyguardinterrupt:{unconfirmed_types[0]}:before"

        return self.interrupt(
            InterruptRequest(
                message=f"检测到 {', '.join(unconfirmed_types)} 类敏感信息，是否允许继续？",
                payload_schema={
                    "type": "object",
                    "properties": {
                        "approved": {"type": "boolean"},
                        "feedback": {"type": "string"},
                        "auto_confirm": {"type": "boolean", "description": "Remember approval for ALL detected types"},
                    },
                    "required": ["approved"],
                },
                auto_confirm_key=first_type_key,
            ),
            subject_id=tool_call_id,
        )

    def _check_after(
        self,
        inputs: ToolCallInputs,
        tool_name: str,
        tool_call_id: str,
        security_ctx: SecurityCheckContext,
    ):
        """Check tool result after execution.

        Multi-type handling: detect all types, separate confirmed/unconfirmed,
        interrupt showing ALL unconfirmed types for batch approval.
        """
        tool_result = inputs.tool_result
        if tool_result is None:
            return self.allow()

        content = self._extract_result_content(tool_result)
        if not content:
            return self.allow()

        detected_types = self._detect_all_types(content)
        if not detected_types:
            return self.allow()

        auto_confirm_config = security_ctx.auto_confirm_config
        unconfirmed_types = []

        for detection_type in detected_types:
            key = f"apikeyguardinterrupt:{detection_type}:after"
            if not self._is_auto_confirmed(auto_confirm_config, key):
                unconfirmed_types.append(detection_type)

        if not unconfirmed_types:
            return self.allow()

        user_input = security_ctx.user_input
        if user_input is not None:
            approved = False
            auto_confirm = False

            if isinstance(user_input, dict):
                approved = user_input.get("approved", False)
                auto_confirm = user_input.get("auto_confirm", False)
            elif hasattr(user_input, "approved"):
                approved = user_input.approved
                auto_confirm = getattr(user_input, "auto_confirm", False)

            if approved:
                if auto_confirm:
                    for detection_type in unconfirmed_types:
                        key = f"apikeyguardinterrupt:{detection_type}:after"
                        self._store_auto_confirm(security_ctx.callback_ctx, key)
                return self.allow()
            else:
                return self.reject(message="用户拒绝")

        first_type_key = f"apikeyguardinterrupt:{unconfirmed_types[0]}:after"

        return self.interrupt(
            InterruptRequest(
                message=f"检测到 {', '.join(unconfirmed_types)} 类敏感信息，是否允许继续？",
                payload_schema={
                    "type": "object",
                    "properties": {
                        "approved": {"type": "boolean"},
                        "feedback": {"type": "string"},
                        "auto_confirm": {"type": "boolean", "description": "Remember approval for ALL detected types"},
                    },
                    "required": ["approved"],
                },
                auto_confirm_key=first_type_key,
            ),
            subject_id=tool_call_id,
        )

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

    def _detect_all_types(self, content: str) -> list[str]:
        """Detect content and return ALL matching detection_types.

        Security requirement: detect all types, not just the first one.
        Prevents missing other types when one type is auto-confirmed.
        """
        detected_types = []
        for rule in self._compiled_rules:
            if rule["pattern"].search(content):
                detected_types.append(rule["type"])
        return detected_types


__all__ = ["ApikeyguardinterruptRail"]
