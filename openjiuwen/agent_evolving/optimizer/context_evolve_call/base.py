# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Base optimizer for the context-evolve dimension.

Common behavior (each covered by direct unit tests):
- ``algorithm_id`` is a mandatory subclass class attribute (record provenance);
- ``_backward`` filters signals by ``supported_signal_types`` (mechanism is
  common, the type set is subclass-declared; dimension default is
  ``task_completed``) and delegates to ``_evolve``;
- ``bind`` reads the dimension-level ``scope_states`` config key.

The base does NOT constrain update mode/effect: those belong to each
algorithm's ``_step``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer
from openjiuwen.agent_evolving.optimizer.context_evolve_call.contracts import SCOPE_STATES_CONFIG_KEY
from openjiuwen.agent_evolving.protocols import TASK_COMPLETED_SIGNAL
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger


class ContextEvolveOptimizerBase(BaseOptimizer):
    """Dimension skeleton; algorithms implement ``_evolve`` and ``_step``."""

    domain = "context"
    algorithm_id: str = ""
    supported_signal_types: tuple[str, ...] = (TASK_COMPLETED_SIGNAL,)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Require each concrete optimizer to declare its algorithm identity."""
        super().__init_subclass__(**kwargs)
        if not cls.algorithm_id:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg=f"{cls.__name__} must declare a non-empty algorithm_id class attribute",
            )

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the optimizer with no bound scope-state snapshots."""
        super().__init__(**kwargs)
        self._scope_states: Dict[str, Any] = {}

    @property
    def scope_states(self) -> Dict[str, Any]:
        """Per-scope state snapshots passed via ``bind(scope_states=...)``."""
        return self._scope_states

    def bind(
        self,
        operators: Optional[Dict[str, Any]] = None,
        targets: Optional[List[str]] = None,
        **config: Any,
    ) -> int:
        """Bind operators and capture the per-scope state configuration."""
        self._scope_states = dict(config.get(SCOPE_STATES_CONFIG_KEY) or {})
        return super().bind(operators=operators, targets=targets, **config)

    async def _backward(self, signals: List[EvolutionSignal]) -> None:
        selected_signals = [signal for signal in signals if signal.signal_type in self.supported_signal_types]
        if not selected_signals:
            logger.info(
                "[%s] no signal matched supported_signal_types=%s; skipping",
                type(self).__name__,
                self.supported_signal_types,
            )
            return
        await self._evolve(self.get_trajectories(), selected_signals)

    @abstractmethod
    async def _evolve(self, trajectories: List[Trajectory], signals: List[EvolutionSignal]) -> None:
        """Run one evolve pass; write gradients into ``self._parameters``."""
        raise NotImplementedError


__all__ = ["ContextEvolveOptimizerBase"]
