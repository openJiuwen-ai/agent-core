# -*- coding: utf-8 -*-
"""Unit tests for RolloutEncoder: build (per-turn), build_whole_trajectory; schemas validation in context."""

import pytest

from openjiuwen.dev_tools.agentrl.coordinator.encoding import RolloutEncoder
from openjiuwen.dev_tools.agentrl.coordinator.schemas import (
    RolloutMessage,
    Rollout,
    RolloutWithReward,
)


@pytest.fixture
def encoder(mock_tokenizer):
    return RolloutEncoder(tokenizer=mock_tokenizer)


def _rollout_msg_single_turn(encoder):
    """One turn: rollout_info length 1, reward_list length 1."""
    return RolloutMessage(
        task_id="t1",
        origin_task_id="o1",
        rollout_id="r1",
        rollout_info=[
            Rollout(
                turn_id=0,
                input_prompt={"message": [{"role": "user", "content": "hi"}]},
                output_response={"role": "assistant", "content": "hello"},
            )
        ],
        reward_list=[0.5],
        global_reward=0.5,
        turn_count=1,
        round_num=0,
    )


def _rollout_msg_three_turns(encoder):
    """Three turns: rollout_info length 3, reward_list length 3."""
    return RolloutMessage(
        task_id="t1",
        origin_task_id="o1",
        rollout_id="r1",
        rollout_info=[
            Rollout(
                turn_id=i,
                input_prompt={"message": [{"role": "user", "content": f"q{i}"}]},
                output_response={"role": "assistant", "content": f"a{i}"},
            )
            for i in range(3)
        ],
        reward_list=[0.3, 0.5, 0.8],
        global_reward=0.8,
        turn_count=3,
        round_num=0,
    )


class TestRolloutEncoderBuild:
    """Per-turn mode: each turn -> one RolloutWithReward."""

    @staticmethod
    def test_build_single_turn_returns_one_sample(encoder):
        msg = _rollout_msg_single_turn(encoder)
        out = encoder.build(msg)
        assert len(out) == 1
        assert isinstance(out[0], RolloutWithReward)
        assert out[0].reward == 0.5
        assert out[0].n_turns == 1
        assert len(out[0].input_prompt_ids) > 0 and len(out[0].output_response_ids) > 0

    @staticmethod
    def test_build_three_turns_returns_three_samples_same_global_reward(encoder):
        """Encoder uses global_reward for all turns (GRPO semantics)."""
        msg = _rollout_msg_three_turns(encoder)
        out = encoder.build(msg)
        assert len(out) == 3
        for sample in out:
            assert sample.reward == msg.global_reward
            assert sample.n_turns == 3


class TestRolloutEncoderBuildWholeTrajectory:
    """Whole-trajectory: all turns -> one sample with loss_mask."""

    @staticmethod
    def test_build_whole_trajectory_single_turn_falls_back_to_build(encoder):
        msg = _rollout_msg_single_turn(encoder)
        out = encoder.build_whole_trajectory(msg)
        assert len(out) == 1
        assert out[0].loss_mask is None

    @staticmethod
    def test_build_whole_trajectory_multi_turn_one_sample_with_loss_mask(encoder):
        msg = _rollout_msg_three_turns(encoder)
        out = encoder.build_whole_trajectory(msg)
        assert len(out) == 1
        assert out[0].loss_mask is not None
        assert len(out[0].loss_mask) == len(out[0].output_response_ids)
        assert out[0].n_turns == 3


class TestSchemasInContext:
    """Validate that legal RolloutMessage produces non-empty RolloutWithReward (no separate schemas file)."""

    @staticmethod
    def test_legal_rollout_message_produces_non_empty_build(encoder):
        msg = _rollout_msg_single_turn(encoder)
        out = encoder.build(msg)
        assert len(out) == 1
        assert out[0].input_prompt_ids and out[0].output_response_ids


class TestRolloutEncoderBoundary:
    """Boundary and exception tests per test suggestion report §4.2."""

    @staticmethod
    def test_build_empty_rollout_info_returns_empty_list(encoder):
        """rollout_info empty: build returns [] without error."""
        msg = RolloutMessage(
            task_id="t1",
            origin_task_id="o1",
            rollout_id="r1",
            rollout_info=[],
            reward_list=[],
            global_reward=0.0,
            turn_count=0,
            round_num=0,
        )
        out = encoder.build(msg)
        assert out == []

    @staticmethod
    def test_build_whole_trajectory_empty_rollout_info_returns_empty_list(encoder):
        """rollout_info empty: build_whole_trajectory returns []."""
        msg = RolloutMessage(
            task_id="t1",
            origin_task_id="o1",
            rollout_id="r1",
            rollout_info=[],
            reward_list=[],
            global_reward=0.0,
            turn_count=0,
            round_num=0,
        )
        out = encoder.build_whole_trajectory(msg)
        assert out == []

    @staticmethod
    def test_build_uses_global_reward_when_reward_list_shorter_than_turns(encoder):
        """reward_list shorter than rollout_info: uses global_reward for all turns."""
        msg = RolloutMessage(
            task_id="t1",
            origin_task_id="o1",
            rollout_id="r1",
            rollout_info=[
                Rollout(
                    turn_id=i,
                    input_prompt={"message": [{"role": "user", "content": f"q{i}"}]},
                    output_response={"role": "assistant", "content": f"a{i}"},
                )
                for i in range(3)
            ],
            reward_list=[0.1],
            global_reward=0.8,
            turn_count=3,
            round_num=0,
        )
        out = encoder.build(msg)
        assert len(out) == 3
        for sample in out:
            assert sample.reward == 0.8
