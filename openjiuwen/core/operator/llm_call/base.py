# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
LLM prompt parameter handle for self-evolution.

LLMCallOperator manages prompt parameters (system_prompt, user_prompt)
for the evolution framework. It does NOT execute LLM calls.
"""

from __future__ import annotations

from typing import Dict, Any, Optional, List, Callable

from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.operator.base import Operator, TunableSpec

DEFAULT_USER_PROMPT: str = "{{query}}"


class LLMCallOperator(Operator):
    """LLM prompt parameter handle for self-evolution.

    Manages system_prompt and user_prompt parameters.
    Parameter changes are pushed to the consumer via on_parameter_updated callback.

    Single entry points for parameter updates:
    - set_parameter(): evolution updates (checks freeze markers)
    - load_state(): checkpoint recovery (no freeze check)
    """

    def __init__(
        self,
        system_prompt: str | List[Dict],
        user_prompt: str | List[Dict],
        freeze_system_prompt: bool = False,
        freeze_user_prompt: bool = True,
        operator_id: str = "llm_call",
        on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        """Initialize LLM parameter handle.

        Args:
            system_prompt: System message(s) for the LLM
            user_prompt: User prompt template (supports {{query}} substitution)
            freeze_system_prompt: If True, system_prompt is not tunable
            freeze_user_prompt: If True, user_prompt is not tunable
            operator_id: Unique operator identifier
            on_parameter_updated: Callback when parameters change
        """
        self._system_prompt = PromptTemplate(content=system_prompt)
        self._user_prompt = PromptTemplate(content=user_prompt or DEFAULT_USER_PROMPT)
        self._freeze_system_prompt = freeze_system_prompt
        self._freeze_user_prompt = freeze_user_prompt
        self._operator_id = operator_id
        self._on_parameter_updated = on_parameter_updated

    @property
    def operator_id(self) -> str:
        """Operator identifier.

        Returns:
            Operator ID string
        """
        return self._operator_id

    def get_tunables(self) -> Dict[str, TunableSpec]:
        """Get tunable parameters.

        Returns:
            Dict of tunable names to TunableSpec (system_prompt, user_prompt if not frozen)
        """
        tunables: Dict[str, TunableSpec] = {}
        if not self._freeze_system_prompt:
            tunables["system_prompt"] = TunableSpec(
                name="system_prompt",
                kind="prompt",
                path="system_prompt",
            )
        if not self._freeze_user_prompt:
            tunables["user_prompt"] = TunableSpec(
                name="user_prompt",
                kind="prompt",
                path="user_prompt",
            )
        return tunables

    def set_parameter(self, target: str, value: Any) -> None:
        """Set tunable parameter value (evolution update).

        Only updates parameters that are not frozen.
        Triggers on_parameter_updated callback if set.

        Args:
            target: Parameter name (system_prompt or user_prompt)
            value: New prompt content (str or list)
        """
        content = value if isinstance(value, (str, list)) else str(value)
        if target == "system_prompt" and not self._freeze_system_prompt:
            self._system_prompt = PromptTemplate(content=content)
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("system_prompt", content)
        elif target == "user_prompt" and not self._freeze_user_prompt:
            self._user_prompt = PromptTemplate(content=content)
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("user_prompt", content)

    def get_state(self) -> Dict[str, Any]:
        """Get current prompt state for checkpoint.

        Returns:
            Dict with system_prompt and user_prompt content
        """
        return {
            "system_prompt": self._system_prompt.content,
            "user_prompt": self._user_prompt.content,
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore prompt state from checkpoint.

        Does NOT check freeze markers (checkpoint recovery must restore full state).
        Triggers on_parameter_updated callback for each field if set.

        Args:
            state: State dict with system_prompt and/or user_prompt
        """
        if "system_prompt" in state:
            content = (
                state["system_prompt"]
                if isinstance(state["system_prompt"], (str, list))
                else str(state["system_prompt"])
            )
            self._system_prompt = PromptTemplate(content=content)
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("system_prompt", content)
        if "user_prompt" in state:
            content = (
                state["user_prompt"] if isinstance(state["user_prompt"], (str, list)) else str(state["user_prompt"])
            )
            self._user_prompt = PromptTemplate(content=content)
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("user_prompt", content)

    def set_freeze_system_prompt(self, switch: bool) -> None:
        """Set system prompt freeze state.

        Args:
            switch: True to freeze, False to enable tuning
        """
        self._freeze_system_prompt = switch

    def set_freeze_user_prompt(self, switch: bool) -> None:
        """Set user prompt freeze state.

        Args:
            switch: True to freeze, False to enable tuning
        """
        self._freeze_user_prompt = switch

    def get_freeze_system_prompt(self) -> bool:
        """Get system prompt freeze state.

        Returns:
            True if frozen (not tunable)
        """
        return self._freeze_system_prompt

    def get_freeze_user_prompt(self) -> bool:
        """Get user prompt freeze state.

        Returns:
            True if frozen (not tunable)
        """
        return self._freeze_user_prompt


# Backward compatible alias
LLMCall = LLMCallOperator
