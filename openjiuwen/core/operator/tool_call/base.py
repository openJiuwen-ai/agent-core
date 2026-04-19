# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Tool description parameter handle for self-evolution.

ToolCallOperator manages tool description parameters for the evolution framework.
It does NOT execute tool calls.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from openjiuwen.core.operator.base import Operator, TunableSpec


class ToolCallOperator(Operator):
    """Tool description parameter handle for self-evolution.

    Manages tool_description parameter (Dict[tool_name, description_str]).
    Parameter changes are pushed to the consumer via on_parameter_updated callback.

    Single entry points for parameter updates:
    - set_parameter(): evolution updates
    - load_state(): checkpoint recovery
    """

    def __init__(
        self,
        operator_id: str,
        descriptions: Optional[Dict[str, str]] = None,
        on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
    ):
        """Initialize tool description parameter handle.

        Args:
            operator_id: Unique operator identifier
            descriptions: Optional dict mapping tool_name to description.
                When set, exposes tool_description tunable for self-evolution.
            on_parameter_updated: Callback when parameters change
        """
        self._operator_id = operator_id
        self._on_parameter_updated = on_parameter_updated
        # Cache tool descriptions for state management
        self._descriptions: Dict[str, str] = descriptions.copy() if descriptions else {}

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
            Dict with 'tool_description' key when descriptions exist; empty otherwise.
        """
        if not self._descriptions:
            return {}

        return {
            "tool_description": TunableSpec(
                name="tool_description",
                kind="text",
                path="tool_description",
                constraint={"type": "dict"},
            )
        }

    def set_parameter(self, target: str, value: Any) -> None:
        """Set tunable parameter value (tool descriptions).

        Updates internal cache and triggers on_parameter_updated callback.

        Args:
            target: Must be 'tool_description'
            value: Dict[tool_name, description_str] mapping tool names to descriptions
        """
        if target != "tool_description":
            return
        if not isinstance(value, dict):
            return

        self._descriptions = value.copy()

        # Trigger callback
        if self._on_parameter_updated is not None:
            self._on_parameter_updated(target, self._descriptions.copy())

    def get_state(self) -> Dict[str, Any]:
        """Get current state for checkpoint.

        Returns:
            Dict with tool_description
        """
        return {
            "tool_description": self._descriptions.copy(),
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore state from checkpoint.

        Triggers callback.

        Args:
            state: State dict with tool_description
        """
        if "tool_description" in state and isinstance(state["tool_description"], dict):
            self._descriptions = state["tool_description"].copy()

            # Trigger callback
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("tool_description", self._descriptions.copy())

