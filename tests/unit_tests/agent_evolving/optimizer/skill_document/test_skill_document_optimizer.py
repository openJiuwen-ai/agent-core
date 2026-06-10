# coding: utf-8
"""Tests for SkillDocumentOptimizer constructor and configuration."""

import warnings
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.protocols import SKILL_CONTENT_TARGET


def _make_cases(n: int = 10) -> CaseLoader:
    cases = [
        Case(inputs={"q": f"question {i}"}, label={"a": f"answer {i}"}, case_id=f"c{i}")
        for i in range(n)
    ]
    return CaseLoader(cases=cases)


def _make_optimizer(**overrides) -> SkillDocumentOptimizer:
    defaults = dict(
        agent=MagicMock(),
        evaluator=MagicMock(),
        llm=MagicMock(),
        model="test-model",
        train_cases=_make_cases(10),
    )
    defaults.update(overrides)
    return SkillDocumentOptimizer(**defaults)


class TestConstructor:
    @staticmethod
    def test_basic_construction():
        opt = _make_optimizer()
        assert opt._batch_size == 40
        assert opt._accumulation == 2
        assert opt._minibatch_size == 8
        assert opt._update_mode == "patch"
        assert opt._score_threshold == 0.5

    @staticmethod
    def test_custom_hyperparameters():
        opt = _make_optimizer(
            batch_size=8, accumulation=1, minibatch_size=4,
            edit_budget=5, score_threshold=0.7,
        )
        assert opt._batch_size == 8
        assert opt._accumulation == 1
        assert opt._minibatch_size == 4
        assert opt._score_threshold == 0.7

    @staticmethod
    def test_requires_forward_data():
        assert SkillDocumentOptimizer.requires_forward_data() is False

    @staticmethod
    def test_default_targets():
        targets = SkillDocumentOptimizer.default_targets()
        assert targets == [SKILL_CONTENT_TARGET]

    @staticmethod
    def test_domain():
        opt = _make_optimizer()
        assert opt.domain == "skill_document"


class TestValidation:
    @staticmethod
    def test_non_patch_update_mode_raises():
        with pytest.raises(NotImplementedError, match="rewrite"):
            _make_optimizer(update_mode="rewrite")

    @staticmethod
    def test_autonomous_scheduler_raises():
        with pytest.raises(NotImplementedError, match="autonomous"):
            _make_optimizer(scheduler_mode="autonomous")

    @staticmethod
    def test_batch_overflow_warning():
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _make_optimizer(batch_size=10, accumulation=2, train_cases=_make_cases(5))
            assert len(w) == 1
            assert "batch_size" in str(w[0].message)

    @staticmethod
    def test_no_warning_when_batch_fits():
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _make_optimizer(batch_size=2, accumulation=1, train_cases=_make_cases(10))
            assert len(w) == 0


class TestStepsPerEpoch:
    @staticmethod
    def test_auto_compute():
        opt = _make_optimizer(batch_size=2, accumulation=1, train_cases=_make_cases(10))
        assert opt._steps_per_epoch == 5  # ceil(10 / (2*1))

    @staticmethod
    def test_auto_compute_with_accumulation():
        opt = _make_optimizer(batch_size=2, accumulation=2, train_cases=_make_cases(10))
        assert opt._steps_per_epoch == 3  # ceil(10 / (2*2))

    @staticmethod
    def test_explicit_steps_override():
        opt = _make_optimizer(steps_per_epoch=3, train_cases=_make_cases(10))
        assert opt._steps_per_epoch == 3


class TestSampleCases:
    @staticmethod
    def test_returns_correct_count():
        opt = _make_optimizer(train_cases=_make_cases(10))
        sampled = opt._sample_cases(5, seed=42)
        assert len(sampled) == 5

    @staticmethod
    def test_returns_all_when_n_exceeds_total():
        opt = _make_optimizer(train_cases=_make_cases(5))
        sampled = opt._sample_cases(10, seed=42)
        assert len(sampled) == 5

    @staticmethod
    def test_deterministic_with_seed():
        opt = _make_optimizer(train_cases=_make_cases(10))
        s1 = opt._sample_cases(5, seed=42)
        s2 = opt._sample_cases(5, seed=42)
        assert [c.case_id for c in s1] == [c.case_id for c in s2]

    @staticmethod
    def test_different_seeds_different_order():
        opt = _make_optimizer(train_cases=_make_cases(10))
        s1 = opt._sample_cases(10, seed=1)
        s2 = opt._sample_cases(10, seed=2)
        assert [c.case_id for c in s1] != [c.case_id for c in s2]


class TestInitialInternalState:
    @staticmethod
    def test_global_step_starts_at_zero():
        opt = _make_optimizer()
        assert opt._global_step == 0

    @staticmethod
    def test_step_buffer_empty():
        opt = _make_optimizer()
        assert opt._step_buffer == []

    @staticmethod
    def test_meta_skill_context_empty():
        opt = _make_optimizer()
        assert opt._meta_skill_context == ""

    @staticmethod
    def test_ranked_patch_none():
        opt = _make_optimizer()
        assert opt._ranked_patch is None

    @staticmethod
    def test_prev_epoch_empty():
        opt = _make_optimizer()
        assert opt._prev_epoch_skill == ""
        assert opt._prev_epoch_comparison == []


class TestStateSerialization:
    @staticmethod
    def test_get_state_structure():
        opt = _make_optimizer()
        state = opt.get_state()
        assert "global_step" in state
        assert "step_buffer" in state
        assert "meta_skill_context" in state
        assert "scheduler" in state
        assert "prev_epoch_skill" in state
        assert "prev_epoch_comparison" in state

    @staticmethod
    def test_get_state_serializable():
        opt = _make_optimizer()
        opt._global_step = 5
        opt._meta_skill_context = "some context"
        opt._prev_epoch_skill = "prev skill"
        opt._prev_epoch_comparison = [
            {"case_id": "c1", "curr_score": 0.8, "curr_reason": "good"},
        ]
        opt._step_buffer = [{"step": 1, "n_edits": 3}]

        state = opt.get_state()
        import json
        serialized = json.dumps(state)
        deserialized = json.loads(serialized)
        assert deserialized["global_step"] == 5
        assert deserialized["meta_skill_context"] == "some context"

    @staticmethod
    def test_load_state_restores():
        opt = _make_optimizer()
        state = {
            "global_step": 10,
            "step_buffer": [{"step": 1, "n_edits": 2}],
            "meta_skill_context": "remembered context",
            "scheduler": {"current_step": 3},
            "prev_epoch_skill": "skill v2",
            "prev_epoch_comparison": [
                {"case_id": "c1", "prev_score": 0.5, "curr_score": 0.7},
            ],
        }
        opt.load_state(state)
        assert opt._global_step == 10
        assert opt._step_buffer == [{"step": 1, "n_edits": 2}]
        assert opt._meta_skill_context == "remembered context"
        assert opt._prev_epoch_skill == "skill v2"
        assert len(opt._prev_epoch_comparison) == 1

    @staticmethod
    def test_roundtrip():
        opt1 = _make_optimizer()
        opt1._global_step = 7
        opt1._meta_skill_context = "ctx"
        opt1._prev_epoch_skill = "old"
        opt1._prev_epoch_comparison = [{"case_id": "x"}]
        opt1._step_buffer = [{"step": 1}]

        state = opt1.get_state()

        opt2 = _make_optimizer()
        opt2.load_state(state)

        assert opt2._global_step == opt1._global_step
        assert opt2._meta_skill_context == opt1._meta_skill_context
        assert opt2._prev_epoch_skill == opt1._prev_epoch_skill
        assert opt2._prev_epoch_comparison == opt1._prev_epoch_comparison
        assert opt2._step_buffer == opt1._step_buffer

    @staticmethod
    def test_load_state_empty():
        opt = _make_optimizer()
        opt.load_state({})
        assert opt._global_step == 0
        assert opt._step_buffer == []
        assert opt._meta_skill_context == ""

    @staticmethod
    def test_prev_epoch_comparison_lightweight():
        """R4: comparison pairs are lightweight dicts, not full objects."""
        opt = _make_optimizer()
        opt._prev_epoch_comparison = [
            {"case_id": "c1", "curr_score": 0.8, "curr_reason": "ok"},
        ]
        state = opt.get_state()
        # Should be serializable as plain dicts
        import json
        json.dumps(state["prev_epoch_comparison"])
