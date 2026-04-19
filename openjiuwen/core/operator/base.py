# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unified Operator abstraction for agent self-evolution.

Operators are parameter handles for self-evolution:
- Trajectory attribution via operator_id
- Optimization via get_tunables, set_parameter, get_state/load_state

Note: Operator is NOT an executable unit. Execution is handled by
the consumer (Agent) using the parameters managed by Operator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


# TunableSpec kind: "prompt" | "continuous" | "discrete" | "tool_selector" | "memory_selector"
TunableKind = str


class TunableSpec:
    """Describes a single tunable parameter of an operator.

    Attributes:
        name: Parameter name
        kind: Tunable type (prompt, continuous, discrete, etc.)
        path: Path to the parameter in the operator
        constraint: Optional constraints for the parameter
    """

    __slots__ = ("name", "kind", "path", "constraint")

    def __init__(
        self,
        name: str,
        kind: TunableKind,
        path: str,
        constraint: Optional[Any] = None,
    ):
        self.name = name
        self.kind = kind
        self.path = path
        self.constraint = constraint


class Operator(ABC):
    """Base class for self-evolution parameter handles.

    Operator provides a unified interface for the evolution framework to:
    - Identify parameters via operator_id (for trajectory attribution)
    - Describe tunable parameters via get_tunables
    - Read current values via get_state
    - Update parameters via set_parameter (checks freeze markers)
    - Restore from checkpoint via load_state (no freeze check)

    Core constraint: Parameter changes are pushed to the consumer
    (Agent/Rail) via the on_parameter_updated callback, ensuring
    immediate synchronization.
    """

    @property
    @abstractmethod
    def operator_id(self) -> str:
        """Unique identifier for trajectory attribution and checkpointing.

        Returns:
            Operator identifier string, format: {agent_id}/{kind}_{name}
        """
        ...

    @abstractmethod
    def get_tunables(self) -> Dict[str, TunableSpec]:
        """Describe tunable parameters and their constraints.

        Frozen parameters should NOT be included in the result.

        Returns:
            Dict mapping tunable names to TunableSpec
        """
        ...

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Get current parameter values for checkpoint/rollback.

        Returns:
            State dict for serialization
        """
        ...

    @abstractmethod
    def set_parameter(self, target: str, value: Any) -> None:
        """Set a parameter value (evolution update).

        Constraints:
        1. Check if target parameter is frozen (skip if frozen)
        2. Update internal state
        3. Trigger on_parameter_updated callback to sync consumer

        This is the ONLY entry point for evolution updates.

        Args:
            target: Parameter name to set
            value: New value to apply
        """
        ...

    @abstractmethod
    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore state from checkpoint (checkpoint recovery).

        Constraints:
        1. Do NOT check freeze markers (must restore full state)
        2. Update internal state field by field
        3. Trigger on_parameter_updated callback for each field

        This is the ONLY entry point for checkpoint recovery.

        Args:
            state: State dict to restore from
        """
        ...
