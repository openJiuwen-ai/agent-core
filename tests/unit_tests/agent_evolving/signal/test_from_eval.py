# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for signal.from_eval module."""

import unittest
from typing import List

from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase
from openjiuwen.agent_evolving.signal.base import EvolutionCategory
from openjiuwen.agent_evolving.signal.from_eval import (
    from_evaluated_case,
    from_evaluated_cases,
)


class TestFromEvaluatedCase(unittest.TestCase):
    """Tests for from_evaluated_case function."""

    def _make_case(
        self,
        score: float,
        inputs: dict = None,
        label: dict = None,
        answer: dict = None,
        reason: str = "test_reason",
    ) -> EvaluatedCase:
        """Create a test EvaluatedCase."""
        if inputs is None:
            inputs = {"query": "test_input"}
        if label is None:
            label = {"expected": "test_label"}
        if answer is None:
            answer = {"result": "test_answer"}
        return EvaluatedCase(
            case=Case(inputs=inputs, label=label),
            answer=answer,
            score=score,
            reason=reason,
        )

    def test_low_score_produces_signal(self) -> None:
        """Case with score below threshold should produce a signal."""
        case = self._make_case(score=0.0)
        signal = from_evaluated_case(case, "test_operator", score_threshold=1.0)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal_type, "low_score")
        self.assertEqual(signal.evolution_type, EvolutionCategory.SKILL_EXPERIENCE)
        self.assertEqual(signal.section, "Troubleshooting")
        self.assertEqual(signal.skill_name, "test_operator")
        self.assertIn("score", signal.excerpt)

    def test_high_score_returns_none_with_threshold(self) -> None:
        """Case with score >= threshold should return None when threshold is set."""
        case = self._make_case(score=1.0)
        signal = from_evaluated_case(case, "test_operator", score_threshold=1.0)

        self.assertIsNone(signal)

    def test_score_threshold_default_is_none(self) -> None:
        """Default threshold is None, so all cases produce signals."""
        case0 = self._make_case(score=0.5)
        case1 = self._make_case(score=1.0)

        signal0 = from_evaluated_case(case0, "test_operator")
        signal1 = from_evaluated_case(case1, "test_operator")

        self.assertIsNotNone(signal0)
        self.assertIsNotNone(signal1)  # score=1.0 also produces signal with default threshold=None

    def test_custom_score_threshold(self) -> None:
        """Custom threshold should filter cases correctly."""
        case0 = self._make_case(score=0.5)
        case1 = self._make_case(score=0.7)
        case2 = self._make_case(score=0.9)

        signal0 = from_evaluated_case(case0, "test_operator", score_threshold=0.8)
        signal1 = from_evaluated_case(case1, "test_operator", score_threshold=0.8)
        signal2 = from_evaluated_case(case2, "test_operator", score_threshold=0.8)

        self.assertIsNotNone(signal0)  # 0.5 < 0.8
        self.assertIsNotNone(signal1)  # 0.7 < 0.8
        self.assertIsNone(signal2)  # 0.9 >= 0.8

    def test_context_contains_evaluation_fields(self) -> None:
        """Signal context should contain question, label, answer, reason, score."""
        case = self._make_case(
            score=0.0,
            inputs={"query": "What is the answer?"},
            label={"expected": "42"},
            answer={"result": "40"},
            reason="Wrong answer",
        )
        signal = from_evaluated_case(case, "test_operator", score_threshold=1.0)

        self.assertIsNotNone(signal)
        context = signal.context
        self.assertIsNotNone(context)
        self.assertIn("question", context)
        self.assertIn("label", context)
        self.assertIn("answer", context)
        self.assertIn("reason", context)
        self.assertIn("score", context)
        # question and label are str(dict) in current implementation
        self.assertIn("What is the answer?", context["question"])
        self.assertIn("42", context["label"])
        self.assertIn("40", context["answer"])
        self.assertEqual(context["reason"], "Wrong answer")
        self.assertEqual(context["score"], 0.0)

    def test_operator_id_as_skill_name(self) -> None:
        """operator_id should be set as signal.skill_name."""
        case = self._make_case(score=0.0)
        signal = from_evaluated_case(case, "skill_call_test_skill", score_threshold=1.0)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.skill_name, "skill_call_test_skill")


class TestFromEvaluatedCases(unittest.TestCase):
    """Tests for from_evaluated_cases batch function."""

    def _make_cases(self, scores: List[float]) -> List[EvaluatedCase]:
        """Create test cases from score list."""
        return [
            EvaluatedCase(
                case=Case(inputs={"query": f"input_{i}"}, label={"expected": f"label_{i}"}),
                answer={"result": f"answer_{i}"},
                score=score,
                reason=f"reason_{i}",
            )
            for i, score in enumerate(scores)
        ]

    def test_batch_filters_by_threshold(self) -> None:
        """Batch function should filter high-score cases when threshold is set."""
        cases = self._make_cases([0.0, 0.5, 1.0, 0.8])
        signals = from_evaluated_cases(cases, "test_operator", score_threshold=1.0)

        # Only scores < 1.0: 0.0, 0.5, 0.8
        self.assertEqual(len(signals), 3)
        # score=0.0 -> 'low_score', others -> 'evaluated'
        self.assertEqual(signals[0].signal_type, "low_score")
        self.assertEqual(signals[1].signal_type, "evaluated")
        self.assertEqual(signals[2].signal_type, "evaluated")

    def test_batch_with_all_high_scores(self) -> None:
        """All high-score cases should return empty list when threshold filters."""
        cases = self._make_cases([1.0, 1.0, 1.0])
        signals = from_evaluated_cases(cases, "test_operator", score_threshold=1.0)

        self.assertEqual(signals, [])

    def test_batch_with_all_low_scores(self) -> None:
        """All low-score cases should produce signals when threshold filters."""
        cases = self._make_cases([0.0, 0.0, 0.0])
        signals = from_evaluated_cases(cases, "test_operator", score_threshold=1.0)

        self.assertEqual(len(signals), 3)

    def test_batch_matches_single_case_results(self) -> None:
        """Batch results should match calling single function on each case."""
        scores = [0.0, 0.5, 1.0, 0.8]
        cases = self._make_cases(scores)

        batch_signals = from_evaluated_cases(cases, "test_operator", score_threshold=1.0)

        single_signals = [
            from_evaluated_case(case, "test_operator", score_threshold=1.0)
            for case in cases
        ]
        single_signals = [s for s in single_signals if s is not None]

        self.assertEqual(len(batch_signals), len(single_signals))
        for batch_signal, single_signal in zip(batch_signals, single_signals):
            self.assertEqual(batch_signal.signal_type, single_signal.signal_type)
            self.assertEqual(batch_signal.skill_name, single_signal.skill_name)
            self.assertEqual(batch_signal.excerpt, single_signal.excerpt)

    def test_empty_cases_returns_empty_list(self) -> None:
        """Empty case list should return empty signal list."""
        signals = from_evaluated_cases([], "test_operator")
        self.assertEqual(signals, [])

    def test_custom_threshold_in_batch(self) -> None:
        """Batch function should respect custom threshold."""
        cases = self._make_cases([0.5, 0.7, 0.8, 0.9])
        signals = from_evaluated_cases(cases, "test_operator", score_threshold=0.75)

        # Scores < 0.75: 0.5, 0.7
        self.assertEqual(len(signals), 2)


if __name__ == "__main__":
    unittest.main()