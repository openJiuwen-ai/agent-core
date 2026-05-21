# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Sensitive Data Sanitize Rail - Mask secrets without blocking execution.

Sanitizes API keys/secrets in conversation history by replacing with [REDACTED].
Does NOT reject or block execution - user decides subsequent action.

Copy this folder to: ~/guardrail/extensions/SensitiveDataSanitize/
"""

from __future__ import annotations

import json
import re

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.rails.base import DeepAgentRail  # noqa: F401
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
    ModelCallInputs,
)
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


class SensitivedatasanitizeRail(BaseSecurityRail):
    """Rail that sanitizes sensitive data in conversation history.

    Events:
        BEFORE_MODEL_CALL: Sanitize secrets in history before LLM sees them
        AFTER_MODEL_CALL: Sanitize secrets in response and history after LLM

    Behavior:
        - Replaces matched patterns with [REDACTED]
        - Does NOT reject or block execution
        - Returns SecurityAllow after sanitization
        - User can decide to reject in subsequent rail or handler
    """

    priority = 85
    supported_events = {
        AgentCallbackEvent.BEFORE_MODEL_CALL,
        AgentCallbackEvent.AFTER_MODEL_CALL,
    }

    def __init__(self, replacement: str = "[REDACTED]") -> None:
        super().__init__()
        self._replacement = replacement
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in SENSITIVE_PATTERNS]

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        inputs = ctx.inputs
        event = security_ctx.event

        if not isinstance(inputs, ModelCallInputs):
            return self.allow()

        if event == AgentCallbackEvent.BEFORE_MODEL_CALL:
            return self._sanitize_input(inputs, ctx)

        if event == AgentCallbackEvent.AFTER_MODEL_CALL:
            return self._sanitize_output(inputs, ctx)

        return self.allow()

    def _sanitize_input(self, inputs: ModelCallInputs, ctx):
        """Sanitize secrets in conversation history before model call."""
        if ctx.context is None:
            return self.allow()

        sanitized = self._sanitize_matching_messages(
            ctx,
            self._compiled_patterns,
            replacement=self._replacement,
            with_history=True,
        )

        if sanitized:
            logger.info(
                "[SensitiveDataSanitize] Sanitized %d messages in history",
                len(sanitized),
            )

        return self.allow()

    def _sanitize_output(self, inputs: ModelCallInputs, ctx):
        """Sanitize secrets in response and history after model call."""
        response = inputs.response
        if response is None:
            return self.allow()

        sanitized_response = False
        if hasattr(response, "content"):
            content = getattr(response, "content", "")
            if isinstance(content, str):
                new_content = content
                for pattern in self._compiled_patterns:
                    new_content = pattern.sub(self._replacement, new_content)
                if new_content != content:
                    response.content = new_content
                    sanitized_response = True

        tool_calls = getattr(response, "tool_calls", None) or []
        for tc in tool_calls:
            args_str = getattr(tc, "arguments", "")
            if isinstance(args_str, str) and args_str:
                try:
                    args_dict = json.loads(args_str)
                    args_json = json.dumps(args_dict)
                    new_args = args_json
                    for pattern in self._compiled_patterns:
                        new_args = pattern.sub(self._replacement, new_args)
                    if new_args != args_json:
                        tc.arguments = json.dumps(json.loads(new_args))
                        sanitized_response = True
                except json.JSONDecodeError:
                    new_args = args_str
                    for pattern in self._compiled_patterns:
                        new_args = pattern.sub(self._replacement, new_args)
                    if new_args != args_str:
                        tc.arguments = new_args
                        sanitized_response = True

        sanitized_history = 0
        if ctx.context is not None:
            sanitized_history = len(
                self._sanitize_matching_messages(
                    ctx,
                    self._compiled_patterns,
                    replacement=self._replacement,
                    with_history=True,
                )
            )

        if sanitized_response or sanitized_history:
            logger.info(
                "[SensitiveDataSanitize] Sanitized response=%s history=%d",
                sanitized_response,
                sanitized_history,
            )

        return self.allow()


__all__ = ["SensitivedatasanitizeRail"]