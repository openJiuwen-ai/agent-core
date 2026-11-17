# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Extension interfaces for evaluation-result analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
        CaseAnalysisInput,
        DeterministicSignals,
        EvaluationSummaryInput,
    )
    from openjiuwen.rsi.harness_rsi.schema import (
        EvaluationResultAnalysisArtifact,
        EvaluationResultAnalysisInvocation,
    )


class SignalExtractor(Protocol):
    """Pluggable zero-LLM pre-extraction strategy keyed by judger method."""

    @property
    def name(self) -> str:
        """Stable extractor name for logging and metadata."""
        ...

    def extract(
        self,
        summary: EvaluationSummaryInput,
        case_inputs: list[CaseAnalysisInput],
    ) -> DeterministicSignals:
        """Extract deterministic signals from evaluation outputs without LLM calls."""
        ...


class EvaluationResultAnalysisStrategy(Protocol):
    """Pluggable strategy for producing Team issue analysis artifacts.

    Each implementation receives only the raw invocation and is fully
    responsible for its own data-loading, signal-extraction, and inference.
    This keeps the Protocol free of internal pipeline types and makes
    alternative implementations genuinely plug-in-able.
    """

    @property
    def name(self) -> str:
        """Stable strategy name used in analysis metadata."""
        ...

    async def analyze(
        self,
        invocation: EvaluationResultAnalysisInvocation,
    ) -> EvaluationResultAnalysisArtifact:
        """Run full analysis pipeline and return a structured issue artifact."""
        ...


__all__ = [
    "EvaluationResultAnalysisStrategy",
    "SignalExtractor",
]
