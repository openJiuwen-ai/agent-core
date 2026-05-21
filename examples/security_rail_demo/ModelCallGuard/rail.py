# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model Call Guard Rail - Block secrets in LLM input/output.

Rejects when API keys/secrets detected in:
1. BEFORE_MODEL_CALL: Last user message sent to LLM (pop + reject)
2. AFTER_MODEL_CALL: LLM response content and tool_call.arguments (pop history + reject)

Pop behavior:
- BEFORE: Pops last user message from current turn only (amnesty for history)
- AFTER: Pops all messages containing secrets from entire history (thorough cleanup)

Conversation continues after rejection - user sees error message and can retry.

Copy this folder to: ~/guardrail/extensions/ModelCallGuard/
"""

from __future__ import annotations

import json
import re

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.harness.rails.base import DeepAgentRail  # noqa: F401
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
    ModelCallInputs,
)
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityCheckContext,
)

API_KEY_PATTERNS = [
    r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN|credential|CREDENTIAL)\s*[=:]\s*[\"']?\S+[\"']?",
    r"sk-[a-zA-Z0-9_-]{20,}",
    r"Bearer\s+[a-zA-Z0-9\-_]+",
    r"AKIA[0-9A-Z]{16}",
]


class ModelcallguardRail(BaseSecurityRail):
    """Rail that blocks API keys/secrets in model input/output.

    Events:
        BEFORE_MODEL_CALL: Check last user message, pop if secret, reject
        AFTER_MODEL_CALL: Check response, pop all historical secrets, reject
    """

    priority = 90
    supported_events = {
        AgentCallbackEvent.BEFORE_MODEL_CALL,
        AgentCallbackEvent.AFTER_MODEL_CALL,
    }

    def __init__(self) -> None:
        super().__init__()
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in API_KEY_PATTERNS]

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        inputs = ctx.inputs
        event = security_ctx.event

        if not isinstance(inputs, ModelCallInputs):
            return self.allow()

        if event == AgentCallbackEvent.BEFORE_MODEL_CALL:
            return self._check_input(inputs, ctx)

        if event == AgentCallbackEvent.AFTER_MODEL_CALL:
            return self._check_output(inputs, ctx)

        return self.allow()

    def _check_input(self, inputs: ModelCallInputs, ctx):
        if ctx.context is None:
            return self.allow()
        
        messages = ctx.context.get_messages(with_history=True)
        
        for msg in messages:
            content = self._extract_message_content(msg)
            if content and self._contains_secret(content):
                self._pop_matching_messages(ctx, self._compiled_patterns, with_history=True)
                return self.reject(
                    message="API key/secret detected in conversation history. Operation blocked for security.",
                )
        
        return self.allow()

    def _check_output(self, inputs: ModelCallInputs, ctx):
        response = inputs.response
        if response is None:
            return self.allow()

        if isinstance(response, AssistantMessage):
            content = getattr(response, "content", "")
            if isinstance(content, str) and self._contains_secret(content):
                self._pop_matching_messages(ctx, self._compiled_patterns, with_history=True)
                return self.reject(
                    message="API key/secret detected in model response. Operation blocked for security.",
                )

            tool_calls = getattr(response, "tool_calls", None) or []
            for tc in tool_calls:
                args_str = getattr(tc, "arguments", "")
                if isinstance(args_str, str) and args_str:
                    try:
                        args_dict = json.loads(args_str)
                        args_json = json.dumps(args_dict)
                        if self._contains_secret(args_json):
                            self._pop_matching_messages(ctx, self._compiled_patterns, with_history=True)
                            return self.reject(
                                message="API key/secret detected in tool arguments. Operation blocked for security.",
                            )
                    except json.JSONDecodeError:
                        if self._contains_secret(args_str):
                            self._pop_matching_messages(ctx, self._compiled_patterns, with_history=True)
                            return self.reject(
                                message="API key/secret detected in tool arguments. Operation blocked for security.",
                            )

        return self.allow()

    def _contains_secret(self, text: str) -> bool:
        for pattern in self._compiled_patterns:
            if pattern.search(text):
                return True
        return False


__all__ = ["ModelcallguardRail"]