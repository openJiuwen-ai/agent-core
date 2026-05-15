# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for MultiDimUpdater protocol surface."""

import asyncio
from unittest.mock import MagicMock

from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.updater.multi_dim import MultiDimUpdater


class _DummyMultiDimUpdater(MultiDimUpdater):
    def bind(self, operators, targets=None, **config):
        return 0

    async def process(self, trajectories, signals, config):
        self.last_call = (list(trajectories), list(signals), dict(config))
        return {("op1", "prompt"): "new prompt"}

    def get_state(self):
        return {}

    def load_state(self, state):
        return None


class TestMultiDimUpdater:
    @staticmethod
    def test_process_accepts_signals_directly():
        updater = _DummyMultiDimUpdater()
        signal = EvolutionSignal(
            signal_type="low_score",
            section="Troubleshooting",
            excerpt="score=0.00",
        )

        result = asyncio.run(updater.process([], [signal], {}))

        assert result == {("op1", "prompt"): "new prompt"}
        assert updater.last_call[1] == [signal]

    @staticmethod
    def test_update_adapts_evaluated_cases_to_process():
        updater = _DummyMultiDimUpdater()
        case = Case(inputs={"q": "question"}, label={"a": "answer"}, case_id="case-1")
        evaluated_case = EvaluatedCase(case=case, answer={"output": "pred"}, score=0.0, reason="reason")

        result = asyncio.run(updater.update([], [evaluated_case], {}))

        assert result == {("op1", "prompt"): "new prompt"}
        assert len(updater.last_call[1]) == 1
        assert updater.last_call[1][0].skill_name is None

    @staticmethod
    def test_update_respects_score_threshold_from_config():
        updater = _DummyMultiDimUpdater()
        case = Case(inputs={"q": "question"}, label={"a": "answer"}, case_id="case-1")
        high_score = EvaluatedCase(case=case, answer={"output": "good"}, score=1.0, reason="perfect")
        low_score = EvaluatedCase(case=case, answer={"output": "pred"}, score=0.0, reason="reason")

        result = asyncio.run(
            updater.update(
                [],
                [high_score, low_score],
                {"score_threshold": 1.0},
            )
        )

        assert result == {("op1", "prompt"): "new prompt"}
        assert len(updater.last_call[1]) == 1
        assert updater.last_call[1][0].signal_type == "low_score"

    @staticmethod
    def test_update_adapts_multiple_evaluated_cases_to_signals_in_order():
        updater = _DummyMultiDimUpdater()
        case = Case(inputs={"q": "question"}, label={"a": "answer"}, case_id="case-1")
        first_case = EvaluatedCase(case=case, answer={"output": "pred"}, score=1.0, reason="perfect")
        second_case = EvaluatedCase(case=case, answer={"output": "pred"}, score=0.0, reason="reason")

        result = asyncio.run(updater.update([], [first_case, second_case], {}))

        assert result == {("op1", "prompt"): "new prompt"}
        assert len(updater.last_call[1]) == 2
        assert updater.last_call[1][0].context["score"] == 1.0
        assert updater.last_call[1][1].context["score"] == 0.0
        assert updater.last_call[1][0].signal_type == "evaluated"
        assert updater.last_call[1][1].signal_type == "low_score"
