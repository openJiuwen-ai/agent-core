# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework Builtin Implementations

Provides ready-to-use guardrail implementations for common security scenarios.
Users can use these directly with custom backends or extend them for
additional customization.
"""

from typing import Any, Dict, List

from openjiuwen.core.security.guardrail.guardrail import BaseGuardrail
from openjiuwen.core.security.guardrail.models import GuardrailResult


class UserInputGuardrail(BaseGuardrail):
    """Guardrail for user input validation.

    Monitors user input events to detect risks like prompt injection,
    jailbreak attempts, or malicious content.

    Default events: user_input

    Example:
        >>> # Use with default events
        >>> guardrail = UserInputGuardrail()
        >>> guardrail.set_backend(PromptInjectionDetector())
        >>> await guardrail.register(callback_framework)
        >>>
        >>> # Use with custom events
        >>> guardrail = UserInputGuardrail(events=["custom_user_input"])
    """

    DEFAULT_EVENTS = ["user_input"]

    async def detect(self, event_name: str, **event_data) -> GuardrailResult:
        """Detect risks in user input.

        Expected event_data:
            - text: The user input text
            - user_id: Optional user identifier
            - session_id: Optional session identifier
        """
        text = event_data.get("text", "")

        if not text or not isinstance(text, str):
            return GuardrailResult.pass_(details={"empty_input": True})

        if self._backend:
            return await super().detect(event_name, **event_data)

        return GuardrailResult.pass_()


class LLMInputGuardrail(BaseGuardrail):
    """Guardrail for LLM input/prompt validation.

    Monitors LLM input events to detect risks in prompts before they
    are sent to the language model.

    Default events: llm_input
    """

    DEFAULT_EVENTS = ["llm_input"]

    async def detect(self, event_name: str, **event_data) -> GuardrailResult:
        """Detect risks in LLM input.

        Expected event_data:
            - prompt: The formatted prompt string
            - messages: List of message objects (for chat models)
            - model: Model identifier
        """
        prompt = event_data.get("prompt", "")
        messages = event_data.get("messages", [])

        if not prompt and not messages:
            return GuardrailResult.pass_(details={"empty_input": True})

        if self._backend:
            return await super().detect(event_name, **event_data)

        return GuardrailResult.pass_()


class LLMOutputGuardrail(BaseGuardrail):
    """Guardrail for LLM output validation.

    Monitors LLM output events to detect risks in model responses
    such as sensitive data disclosure or harmful content.

    Default events: llm_output
    """

    DEFAULT_EVENTS = ["llm_output"]

    async def detect(self, event_name: str, **event_data) -> GuardrailResult:
        """Detect risks in LLM output.

        Expected event_data:
            - content: The generated text content
            - tool_calls: List of tool calls (if any)
            - model: Model identifier
        """
        content = event_data.get("content", "")
        tool_calls = event_data.get("tool_calls", [])

        if not content and not tool_calls:
            return GuardrailResult.pass_(details={"empty_output": True})

        if self._backend:
            return await super().detect(event_name, **event_data)

        return GuardrailResult.pass_()


class ToolGuardrail(BaseGuardrail):
    """Guardrail for tool invocation validation.

    Monitors tool input and output events to detect risks in tool
    invocations such as unauthorized access attempts or data leakage.

    Default events: tool_input, tool_output

    Example:
        >>> # Use with default events (input and output)
        >>> guardrail = ToolGuardrail()
        >>>
        >>> # Only listen to input events
        >>> guardrail = ToolGuardrail(events=["tool_input"])
        >>>
        >>> # Custom events
        >>> guardrail = ToolGuardrail()
        >>> guardrail.with_events(["custom_tool_input", "custom_tool_output"])
    """

    DEFAULT_EVENTS = ["tool_input", "tool_output"]

    async def detect(self, event_name: str, **event_data) -> GuardrailResult:
        """Detect risks in tool invocation.

        Expected event_data for tool_input:
            - tool_name: Name of the tool being called
            - tool_input: Input parameters

        Expected event_data for tool_output:
            - tool_name: Name of the tool
            - tool_output: Tool execution result
        """
        tool_name = event_data.get("tool_name")

        if not tool_name:
            return GuardrailResult.pass_(details={"no_tool_name": True})

        if event_name == "tool_input":
            return await self._check_tool_input(tool_name, event_data)
        else:
            return await self._check_tool_output(tool_name, event_data)

    async def _check_tool_input(
            self,
            tool_name: str,
            event_data: Dict[str, Any]
    ) -> GuardrailResult:
        """Check tool input phase."""
        if self._backend:
            return await super().detect("tool_input", **event_data)
        return GuardrailResult.pass_()

    async def _check_tool_output(
            self,
            tool_name: str,
            event_data: Dict[str, Any]
    ) -> GuardrailResult:
        """Check tool output phase."""
        if self._backend:
            return await super().detect("tool_output", **event_data)
        return GuardrailResult.pass_()


class PlanningGuardrail(BaseGuardrail):
    """Guardrail for agent planning validation.

    Monitors planning events to detect risks in agent task planning,
    such as plans that deviate from user intent or contain unsafe steps.

    Default events: planning_start, planning_complete
    """

    DEFAULT_EVENTS = ["planning_start", "planning_complete"]

    async def detect(self, event_name: str, **event_data) -> GuardrailResult:
        """Detect risks in agent planning.

        Expected event_data for planning_start:
            - task: The task description

        Expected event_data for planning_complete:
            - plan: The generated plan structure
            - steps: List of planned steps
        """
        if event_name == "planning_start":
            return await self._check_planning_start(event_data)
        else:
            return await self._check_planning_complete(event_data)

    async def _check_planning_start(self, event_data: Dict[str, Any]) -> GuardrailResult:
        """Check the start of planning phase."""
        task = event_data.get("task", "")

        if not task:
            return GuardrailResult.pass_(details={"empty_task": True})

        if self._backend:
            return await super().detect("planning_start", **event_data)
        return GuardrailResult.pass_()

    async def _check_planning_complete(self, event_data: Dict[str, Any]) -> GuardrailResult:
        """Check the completed plan."""
        plan = event_data.get("plan")
        steps = event_data.get("steps", [])

        if not plan and not steps:
            return GuardrailResult.pass_(details={"empty_plan": True})

        if self._backend:
            return await super().detect("planning_complete", **event_data)

        return GuardrailResult.pass_()
