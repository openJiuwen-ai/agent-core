# -*- coding: utf-8 -*-
"""Unit tests for RLBatchBuilder: padding, token_level_scores, generate_components, assemble, generate_rl_batch."""

import pytest

pytest.importorskip("torch")
pytest.importorskip("tensordict")

try:
    import torch
    from tensordict import TensorDict
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from openjiuwen.agent_evolving.agent_rl.offline.coordinator.batch_builder import RLBatchBuilder
from openjiuwen.agent_evolving.agent_rl.schemas import RolloutWithReward


@pytest.fixture
def builder():
    return RLBatchBuilder(
        max_prompt_length=16,
        pad_token_id=0,
        max_response_length=8,
    )


def _rollout(pid_len=4, rid_len=3, reward=0.5, loss_mask=None):
    return RolloutWithReward(
        input_prompt_ids=[1, 2, 3, 4][:pid_len],
        output_response_ids=[10, 20, 30][:rid_len],
        reward=reward,
        n_turns=1,
        loss_mask=loss_mask,
    )


class TestPaddingAndMask:
    @staticmethod
    def test_left_pad_length_and_mask(builder):
        ids = [1, 2, 3]
        padded, mask = builder.get_left_padded_ids_and_attention_mask(
            ids, max_length=8, pad_token_id=0
        )
        assert len(padded) == 8
        assert padded[:5] == [0] * 5 and padded[5:] == [1, 2, 3]
        assert mask[:5] == [0] * 5 and mask[5:] == [1, 1, 1]

    @staticmethod
    def test_left_pad_truncate_when_over_max(builder):
        ids = list(range(20))
        padded, mask = builder.get_left_padded_ids_and_attention_mask(
            ids, max_length=8, pad_token_id=0
        )
        assert len(padded) == 8
        assert padded == ids[-8:]
        assert mask == [1] * 8

    @staticmethod
    def test_right_pad_length_and_mask(builder):
        ids = [1, 2, 3]
        padded, mask = builder.get_right_padded_ids_and_attention_mask(
            ids, max_length=8, pad_token_id=0
        )
        assert len(padded) == 8
        assert padded[:3] == [1, 2, 3] and padded[3:] == [0] * 5
        assert mask[:3] == [1, 1, 1] and mask[3:] == [0] * 5


@pytest.mark.skipif(not HAS_TORCH, reason="torch and tensordict required")
class TestCreateTokenLevelScores:
    @staticmethod
    def test_token_level_scores_shape_and_reward_at_eos(builder):
        n_transition = 2
        resp_len = 4
        attn = torch.LongTensor([[0, 0, 1, 1, 1, 1], [0, 1, 1, 1, 1, 0]])
        position_ids = torch.LongTensor([[0, 0, 1, 2, 3, 4], [0, 1, 2, 3, 4, 0]])
        scores = torch.tensor([0.5, -0.2], dtype=torch.bfloat16)
        token_scores = builder.create_token_level_scores(
            attn, position_ids, scores, response_length=resp_len
        )
        assert token_scores.shape == (n_transition, resp_len)
        assert token_scores.sum(dim=1)[0].item() == pytest.approx(0.5, abs=1e-2)
        assert token_scores.sum(dim=1)[1].item() == pytest.approx(-0.2, abs=1e-2)


@pytest.mark.skipif(not HAS_TORCH, reason="torch and tensordict required")
class TestGenerateRlBatch:
    @staticmethod
    def test_generate_rl_batch_single_entry_returns_tensordict_and_meta(builder):
        rollout_dict = {"uid1": [_rollout(pid_len=4, rid_len=3)]}
        device = torch.device("cpu")
        batch, non_tensor = builder.generate_rl_batch(rollout_dict, device)
        assert isinstance(batch, TensorDict)
        assert "prompts" in batch and "responses" in batch and "input_ids" in batch
        assert batch["prompts"].shape[0] == 1
        assert "data_id_list" in non_tensor
        assert len(non_tensor["data_id_list"]) == 1

    @staticmethod
    def test_generate_rl_batch_empty_dict_returns_empty_batch(builder):
        device = torch.device("cpu")
        batch, non_tensor = builder.generate_rl_batch({}, device)
        assert batch.batch_size[0] == 0 or len(batch.get("prompts", [])) == 0
        assert len(non_tensor["data_id_list"]) == 0

    @staticmethod
    def test_generate_components_truncation_and_padding(builder):
        rollout_dict = {
            "u1": [_rollout(pid_len=20, rid_len=10)],
        }
        comp = builder.generate_components(
            rollout_dict,
            max_prompt_length=16,
            max_response_length=8,
        )
        assert len(comp["input_ids"]) == 1
        assert len(comp["input_ids"][0]) == 16
        assert len(comp["response_ids"][0]) == 8
