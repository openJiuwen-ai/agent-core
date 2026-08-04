# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Extension interfaces for optimization experience learning."""

from __future__ import annotations

from typing import Protocol

from openjiuwen.rsi.optimization_experience_learner.schema import (
    OptimizationExperienceArtifact,
    OptimizationExperienceInput,
    OptimizationExperienceRetrievalQuery,
    OptimizationExperienceRetrievalResult,
)


class OptimizationExperienceLearningStrategy(Protocol):
    """Pluggable strategy for learning reusable optimization experience."""

    @property
    def name(self) -> str:
        """Stable strategy name used in experience metadata."""
        ...

    async def learn(
        self,
        experience_input: OptimizationExperienceInput,
    ) -> OptimizationExperienceArtifact:
        """Learn and persist reusable experience for one accepted optimization."""
        ...

    async def retrieve(
        self,
        query: OptimizationExperienceRetrievalQuery,
    ) -> OptimizationExperienceRetrievalResult:
        """Retrieve reusable experience for an optimization stage."""
        ...


__all__ = [
    "OptimizationExperienceLearningStrategy",
]
