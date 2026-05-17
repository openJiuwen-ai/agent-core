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
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.types import ApplyResult, UpdateValue


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

    def apply_update(self, target: str, update: "UpdateValue") -> "ApplyResult":
        """Apply a structured evolution update.

        Default compatibility behavior preserves replace-only operators by
        delegating ``replace/state`` updates to ``set_parameter``.
        """
        from openjiuwen.agent_evolving.types import ApplyResult

        if update.mode != "replace" or update.effect != "state":
            return ApplyResult(
                operator_id=self.operator_id,
                target=target,
                applied=False,
                mode=update.mode,
                effect=update.effect,
                value=update.payload,
                errors=[
                    f"unsupported update mode/effect for compatibility operator: {update.mode}/{update.effect}"
                ],
                metadata=dict(update.metadata),
            )

        before_state = self.get_state()
        self.set_parameter(target, update.payload)
        after_state = self.get_state()
        applied = before_state != after_state
        return ApplyResult(
            operator_id=self.operator_id,
            target=target,
            applied=applied,
            mode=update.mode,
            effect=update.effect,
            value=update.payload,
            metadata=dict(update.metadata),
        )

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


class PreviewableOperator(Operator):
    """Optional extension for operators that support local preview updates.

    Preview updates produce local apply results only. Approval and persistence
    remain owned by the caller's lifecycle manager, not by the operator.
    """

    @abstractmethod
    def preview_update(self, target: str, update: "UpdateValue") -> "ApplyResult":
        """Apply a local preview update without entering pending or persistence."""
        ...

    def apply_update(self, target: str, update: "UpdateValue") -> "ApplyResult":
        """Route standard update execution to preview semantics."""
        return self.preview_update(target, update)
