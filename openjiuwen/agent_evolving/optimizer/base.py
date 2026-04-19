# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Base classes for dimension-specific optimizers.

BaseOptimizer: Filters optimizable Operators, caches Trajectory, generates Updates.
TextualParameter: Gradient container for operator_id.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.agent_evolving.trajectory.types import Trajectory, Updates

if TYPE_CHECKING:
    from openjiuwen.core.operator.base import Operator
    from openjiuwen.agent_evolving.signal.base import EvolutionSignal


class BaseOptimizer:
    """
    Common skeleton for dimension-specific optimizers.

    bind(): Filters optimizable Operators, returns count (0 triggers soft-exit).
    add_trajectory / get_trajectories: Caches Trajectory for backward.
    step(): Returns Updates, applied by Trainer.apply_updates.
    """
    domain: str = ""

    def __init__(self, **kwargs):
        self._operators: Dict[str, "Operator"] = {}
        self._parameters: Dict[str, TextualParameter] = {}
        self._targets: List[str] = []
        self._trajectories: List[Trajectory] = []
        self._bad_signals: List["EvolutionSignal"] = []

    @staticmethod
    def requires_forward_data() -> bool:
        """Whether this optimizer needs framework to execute forward on train_cases.

        Returns:
            True (default): optimizer uses trajectories/evaluated_cases from forward.
            False: black-box optimizer (e.g., tool_optimizer, data-self-generation)
                   generates/executes/evaluates internally without framework data.

        Subclass override to False skips forward in Trainer, reducing overhead for
        optimizers that don't consume train_cases.
        """
        return True

    @staticmethod
    def __exit__(self, exc_type, exc_val, exc_tb):
        return None

    def __enter__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    @staticmethod
    def default_targets() -> List[str]:
        """Subclass can override to provide default target list for this dimension."""
        return []

    @staticmethod
    def filter_operators(operators: Dict[str, "Operator"], targets: List[str]) -> Dict[str, "Operator"]:
        """
        Filter Operators that expose any of the targets. Records warning for non-matching, does not interrupt.
        """
        out: Dict[str, "Operator"] = {}
        for op_id, op in (operators or {}).items():
            tunables = op.get_tunables()
            matched = [t for t in targets if t in tunables]
            if not matched:
                logger.warning("[optimizer] operator %s has no tunables in targets=%s", op_id, targets)
                continue
            out[op_id] = op
        return out

    def bind(
        self, operators: Optional[Dict[str, "Operator"]] = None, targets: Optional[List[str]] = None, **config
    ) -> int:
        """
        Filter and bind optimizable Operators. Returns count; 0 triggers soft-exit at upper level.
        """
        if operators is None:
            operators = {}
        self._targets = list(targets or self.default_targets())
        self._operators = self.filter_operators(operators, self._targets)
        self._parameters = {op_id: TextualParameter(operator_id=op_id) for op_id in self._operators}
        self._trajectories = []
        self._bad_signals = []
        if not self._operators:
            logger.error(
                "[optimizer] no operator matches targets=%s; will soft-exit",
                self._targets,
            )
        return len(self._operators)

    def add_trajectory(self, trajectory: Trajectory) -> None:
        """
        Cache Trajectory for backward phase query. Trajectory is the single source of truth.
        """
        self._trajectories.append(trajectory)

    def get_trajectories(self) -> List[Trajectory]:
        """Returns currently cached trajectory list for subclass queries
        (e.g., ADAPT filtering by case_id/operator_id).
        """
        return list(self._trajectories)

    def clear_trajectories(self) -> None:
        """Clear cache after update."""
        self._trajectories.clear()

    async def backward(self, signals: List["EvolutionSignal"]) -> None:
        self._validate_parameters()
        self._get_bad_signals(signals)
        try:
            await self._backward(signals)
        except Exception as e:
            raise build_error(
                StatusCode.TOOLCHAIN_OPTIMIZER_BACKWARD_EXECUTION_ERROR, error_msg=f"{str(e)}", cause=e
            ) from e

    def step(self) -> Updates:
        """Execute _step() and return Updates; applied uniformly by Trainer.apply_updates."""
        self._validate_parameters()
        try:
            updates = self._step()
            self.clear_trajectories()
            return updates or {}
        except Exception as e:
            self.clear_trajectories()
            raise build_error(
                StatusCode.TOOLCHAIN_OPTIMIZER_UPDATE_EXECUTION_ERROR, error_msg=f"{str(e)}", cause=e
            ) from e

    @abstractmethod
    async def _backward(self, signals: List["EvolutionSignal"]) -> None:
        pass

    @abstractmethod
    def _step(self) -> Updates:
        """Subclass implements: generates Updates based on gradients written during backward."""
        pass

    def parameters(self) -> Dict[str, "TextualParameter"]:
        return self._parameters.copy()

    def _get_bad_signals(self, signals: List["EvolutionSignal"]) -> List["EvolutionSignal"]:
        """Filter signals for backward.

        - context is None → online signal, keep all
        - context has value → offline signal, filter by score == 0
        """
        bad = [s for s in signals if s.context is None or s.context.get("score", 1) == 0]
        self._bad_signals = bad
        return bad

    def _validate_parameters(self):
        if not self._parameters:
            raise build_error(StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR, error_msg="cannot optimize empty parameters")


class TextualParameter:
    """Gradient container for operator_id, stores target -> gradient value and
    optional description. No longer holds Operator reference.
    """

    def __init__(self, operator_id: str):
        self.operator_id = operator_id
        self.gradients: Dict[str, Any] = {}  # target -> gradient value (str or list)
        self.description: str = ""

    def set_gradient(self, name: str, gradient: Any) -> None:
        self.gradients[name] = gradient

    def get_gradient(self, name: str) -> Optional[Any]:
        return self.gradients.get(name)

    def set_description(self, description: str) -> None:
        self.description = description

    def get_description(self) -> str:
        return self.description
