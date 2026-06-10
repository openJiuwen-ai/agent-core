# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from openjiuwen.agent_evolving.optimizer import BaseOptimizer
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.signal.from_eval import from_evaluated_case
from openjiuwen.agent_evolving.trajectory import Trajectory
from openjiuwen.core.operator import Operator


class SingleDimUpdater:
    """
    Single-dimension update updater: Reuses BaseOptimizer (backward/step),
    Update mappings are applied uniformly by Trainer.
    """

    def __init__(self, optimizer: BaseOptimizer):
        self._opt = optimizer

    def bind(self, operators: Dict[str, Operator], targets: Optional[List[str]] = None, **config: Any) -> int:
        """Bind operators, filter optimizable ones; returns count; 0 triggers Trainer soft-exit."""
        effective_targets = targets or config.get("targets")
        bound_n = self._opt.bind(operators=operators, targets=effective_targets, **config)
        return bound_n

    def requires_forward_data(self) -> bool:
        """Delegate to internal optimizer."""
        return self._opt.requires_forward_data()

    async def process(
        self,
        trajectories: List[Trajectory],
        signals: List[EvolutionSignal],
        config: Dict[str, Any],
    ) -> Union[Dict[tuple[str, str], Any], List[Dict[tuple[str, str], Any]]]:
        """Signal-first entry: write trajectories, run backward, and return updates."""
        for traj in trajectories:
            self._opt.add_trajectory(traj)
        await self._opt.backward(signals)
        return self._opt.step()

    async def update(
        self, trajectories: List[Trajectory], evaluated_cases: List[Any], config: Dict[str, Any]
    ) -> Union[Dict[tuple[str, str], Any], List[Dict[tuple[str, str], Any]]]:
        """Offline compatibility adapter that converts evaluated cases to signals."""
        score_threshold = config.get("score_threshold")
        signals = []
        for case in evaluated_cases:
            signal = from_evaluated_case(case, score_threshold=score_threshold)
            if signal is not None:
                signals.append(signal)
        return await self.process(trajectories, signals, config)

    def get_state(self) -> Dict[str, Any]:
        """Delegate to the internal optimizer."""
        return self._opt.get_state()

    def load_state(self, state: Dict[str, Any]) -> None:
        """Delegate to the internal optimizer."""
        self._opt.load_state(state)
