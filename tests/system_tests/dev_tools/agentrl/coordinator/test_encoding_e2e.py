# -*- coding: utf-8 -*-
"""System test for RolloutEncoder: full encode flow with mock tokenizer."""

import pytest

from openjiuwen.dev_tools.agentrl.coordinator.encoding import RolloutEncoder
from openjiuwen.dev_tools.agentrl.coordinator.schemas import (
    RolloutMessage,
    Rollout,
    RolloutWithReward,
)


def _mock_tokenizer():
    """Mock tokenizer for RolloutEncoder tests."""

    class MockTokenizer:
        pad_token_id = 0

        @staticmethod
        def apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, tools=None
        ):
            if isinstance(messages, list) and messages:
                parts = []
                for m in messages:
                    if isinstance(m, dict):
                        role = m.get("role", "user")
                        content = m.get("content", "") or ""
                        if isinstance(content, list):
                            content = " ".join(
                                c.get("text", str(c)) for c in content if isinstance(c, dict)
                            )
                        parts.append(f"<{role}>{content}")
                    else:
                        parts.append(str(m))
                return " ".join(parts) + (" " if add_generation_prompt else "")
            return ""

        @staticmethod
        def encode(text, add_special_tokens=True):
            if not text:
                return []
            return [ord(c) % 100 for c in text[:50]]

    return MockTokenizer()


@pytest.fixture
def encoder():
    return RolloutEncoder(tokenizer=_mock_tokenizer())


def _rollout_msg_single_turn():
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


def _rollout_msg_three_turns():
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


def test_encoder_build_single_turn_e2e(encoder):
    """E2E: single turn produces one RolloutWithReward with token ids."""
    msg = _rollout_msg_single_turn()
    out = encoder.build(msg)
    assert len(out) == 1
    assert isinstance(out[0], RolloutWithReward)
    assert out[0].reward == 0.5
    assert out[0].n_turns == 1
    assert len(out[0].input_prompt_ids) > 0
    assert len(out[0].output_response_ids) > 0


def test_encoder_build_three_turns_e2e(encoder):
    """E2E: three turns produce three samples with shared global reward."""
    msg = _rollout_msg_three_turns()
    out = encoder.build(msg)
    assert len(out) == 3
    for sample in out:
        assert sample.reward == msg.global_reward
        assert sample.n_turns == 3


def test_encoder_build_whole_trajectory_multi_turn_e2e(encoder):
    """E2E: whole-trajectory mode produces one sample with loss_mask for multi-turn."""
    msg = _rollout_msg_three_turns()
    out = encoder.build_whole_trajectory(msg)
    assert len(out) == 1
    assert out[0].loss_mask is not None
    assert len(out[0].loss_mask) == len(out[0].output_response_ids)
    assert out[0].n_turns == 3
