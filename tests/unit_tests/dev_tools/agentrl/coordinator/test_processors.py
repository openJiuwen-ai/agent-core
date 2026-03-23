# -*- coding: utf-8 -*-
"""Unit tests for RolloutClassifier, RolloutValidator, RolloutSampling, ProcessorsRegistry."""

import pytest

from openjiuwen.dev_tools.agentrl.coordinator.processors import (
    RolloutClassifier,
    RolloutValidator,
    RolloutSampling,
    ProcessorsRegistry,
)
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutWithReward


def _mdp(reward: float, uid: str = "u1") -> RolloutWithReward:
    return RolloutWithReward(
        input_prompt_ids=[1, 2],
        output_response_ids=[3, 4],
        reward=reward,
        n_turns=1,
    )


class TestRolloutClassifier:
    @staticmethod
    def test_classify_positive_negative_split():
        mdp_list = [_mdp(0.5), _mdp(-0.1), _mdp(1.0), _mdp(0.0)]
        pos, neg = RolloutClassifier.default_classify_rollouts(mdp_list)
        assert len(pos) == 2
        assert len(neg) == 2
        assert all(m.reward > 0 for m in pos)
        assert all(m.reward <= 0 for m in neg)

    @staticmethod
    def test_classify_empty_returns_empty_lists():
        pos, neg = RolloutClassifier.default_classify_rollouts([])
        assert pos == []
        assert neg == []


class TestRolloutValidator:
    @staticmethod
    def test_default_validate_stop_true_when_two_pos_and_one_reward_one():
        pos = [_mdp(0.5), _mdp(1.0)]
        neg = [_mdp(-0.1)]
        assert RolloutValidator.default_validate_stop(pos, neg) is True

    @staticmethod
    def test_default_validate_stop_false_when_less_than_two_pos():
        pos = [_mdp(1.0)]
        neg = []
        assert RolloutValidator.default_validate_stop(pos, neg) is False

    @staticmethod
    def test_default_validate_stop_false_when_no_reward_one():
        pos = [_mdp(0.5), _mdp(0.8)]
        neg = []
        assert RolloutValidator.default_validate_stop(pos, neg) is False

    @staticmethod
    def test_validate_stop_balanced_true_when_targets_met():
        pos = [_mdp(0.5)] * 4
        neg = [_mdp(-0.1)] * 4
        assert RolloutValidator.validate_stop_balanced(pos, neg, final_keep_per_prompt=8) is True

    @staticmethod
    def test_validate_stop_balanced_false_when_insufficient():
        pos = [_mdp(0.5)] * 2
        neg = [_mdp(-0.1)] * 2
        assert RolloutValidator.validate_stop_balanced(pos, neg, final_keep_per_prompt=8) is False

    @staticmethod
    def test_default_validate_stop_empty_lists_false():
        assert RolloutValidator.default_validate_stop([], []) is False


class TestRolloutSampling:
    @staticmethod
    def test_default_sampling_returns_deepcopy_unchanged_counts():
        pos_d = {"u1": [_mdp(0.5)], "u2": [_mdp(0.6), _mdp(0.7)]}
        neg_d = {"u1": [_mdp(-0.1)], "u2": [_mdp(-0.2)]}
        out_pos, out_neg = RolloutSampling.default_sampling(pos_d, neg_d)
        assert len(out_pos) == 2 and len(out_neg) == 2
        assert len(out_pos["u1"]) == 1 and len(out_pos["u2"]) == 2
        assert out_pos is not pos_d and out_neg is not neg_d

    @staticmethod
    def test_downsample_one_uid_balanced_target_total():
        pos_list = [_mdp(0.5)] * 6
        neg_list = [_mdp(-0.1)] * 6
        p_sel, n_sel = RolloutSampling.downsample_one_uid(
            pos_list, neg_list, target_total=8
        )
        assert len(p_sel) + len(n_sel) <= 8
        assert len(p_sel) <= 6 and len(n_sel) <= 6

    @staticmethod
    def test_sampling_ada_per_uid_downsampled():
        pos_d = {"u1": [_mdp(0.5)] * 10, "u2": [_mdp(0.6)] * 5}
        neg_d = {"u1": [_mdp(-0.1)] * 10, "u2": [_mdp(-0.2)] * 5}
        out_pos, out_neg = RolloutSampling.sampling_ada(
            pos_d, neg_d, final_keep_per_prompt=8
        )
        assert set(out_pos.keys()) == set(out_neg.keys()) == {"u1", "u2"}
        for uid in ["u1", "u2"]:
            total = len(out_pos[uid]) + len(out_neg[uid])
            assert total <= 8


class TestProcessorsRegistry:
    @staticmethod
    def test_get_classifier_default_callable_matches_static():
        reg = ProcessorsRegistry()
        fn = reg.get_classifier("default_classify_rollouts")
        mdp_list = [_mdp(0.5), _mdp(-0.1)]
        pos, neg = fn(mdp_list)
        assert len(pos) == 1 and len(neg) == 1

    @staticmethod
    def test_get_validator_validate_stop_balanced():
        reg = ProcessorsRegistry()
        fn = reg.get_validator("validate_stop_balanced")
        pos = [_mdp(0.5)] * 4
        neg = [_mdp(-0.1)] * 4
        assert fn(pos, neg, final_keep_per_prompt=8) is True

    @staticmethod
    def test_get_sampler_sampling_ada():
        reg = ProcessorsRegistry()
        fn = reg.get_sampler("sampling_ada")
        pos_d = {"u1": [_mdp(0.5)]}
        neg_d = {"u1": [_mdp(-0.1)]}
        out_pos, out_neg = fn(pos_d, neg_d, final_keep_per_prompt=8)
        assert "u1" in out_pos and "u1" in out_neg

    @staticmethod
    def test_get_classifier_unknown_raises():
        """Unknown classifier name raises (ValidationError from build_error)."""
        reg = ProcessorsRegistry()
        with pytest.raises(Exception) as exc_info:
            reg.get_classifier("unknown_classifier")
        assert "not found" in str(exc_info.value).lower() or "unknown" in str(exc_info.value).lower()

    @staticmethod
    def test_get_validator_unknown_raises():
        """Unknown validator name raises."""
        reg = ProcessorsRegistry()
        with pytest.raises(Exception):
            reg.get_validator("unknown_validator")

    @staticmethod
    def test_get_sampler_unknown_raises():
        """Unknown sampler name raises."""
        reg = ProcessorsRegistry()
        with pytest.raises(Exception):
            reg.get_sampler("unknown_sampler")
