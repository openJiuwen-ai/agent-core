# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Memory parameter handle for self-evolution.

MemoryCallOperator manages memory-related parameters for the evolution framework.
It does NOT execute memory operations.

Future extensions:
- get_tunables() for retrieval strategy (top_k, query rewrite), write strategy
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Callable

from openjiuwen.core.operator.base import Operator, TunableSpec


class MemoryCallOperator(Operator):
    """Memory parameter handle for self-evolution.

    Manages enabled and max_retries parameters.

    Single entry points for parameter updates:
    - set_parameter(): evolution updates
    - load_state(): checkpoint recovery
    """

    def __init__(
        self,
        operator_id: str = "memory_call",
        on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
    ):
        """Initialize memory parameter handle.

        Args:
            operator_id: Unique operator identifier
            on_parameter_updated: Callback when parameters change
        """
        self._operator_id = operator_id
        self._on_parameter_updated = on_parameter_updated
        self._enabled: bool = True
        self._max_retries: int = 0

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
            Dict with enabled and max_retries tunables
        """
        return {
            "enabled": TunableSpec(
                name="enabled",
                kind="discrete",
                path="enabled",
                constraint={"type": "bool"},
            ),
            "max_retries": TunableSpec(
                name="max_retries",
                kind="discrete",
                path="max_retries",
                constraint={"type": "int", "min": 0, "max": 5},
            ),
        }

    def set_parameter(self, target: str, value: Any) -> None:
        """Set tunable parameter value.

        Triggers on_parameter_updated callback if set.

        Args:
            target: Parameter name (enabled or max_retries)
            value: New value to set
        """
        if target == "enabled":
            self._enabled = bool(value)
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("enabled", self._enabled)
        elif target == "max_retries":
            v = int(value)
            self._max_retries = max(0, min(5, v))
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("max_retries", self._max_retries)

    def get_state(self) -> Dict[str, Any]:
        """Get current state for checkpoint.

        Returns:
            Dict with enabled and max_retries
        """
        return {"enabled": self._enabled, "max_retries": self._max_retries}

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore state from checkpoint.

        Triggers on_parameter_updated callback if set.

        Args:
            state: State dict with enabled and/or max_retries
        """
        if "enabled" in state:
            self._enabled = bool(state["enabled"])
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("enabled", self._enabled)
        if "max_retries" in state:
            self._max_retries = max(0, min(5, int(state["max_retries"])))
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("max_retries", self._max_retries)
