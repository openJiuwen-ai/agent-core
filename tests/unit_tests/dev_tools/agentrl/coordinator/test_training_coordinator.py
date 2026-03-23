# -*- coding: utf-8 -*-
"""Unit tests for TrainingCoordinator (tasks, caches, batch, executor, config validation)."""

import pytest

pytest.importorskip("torch")

try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from openjiuwen.dev_tools.agentrl.coordinator.training_coordinator import (
    TrainingCoordinator,
)
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutWithReward


def _coordinator_config(whole_trajectory=False):
    return {
        "data": {
            "max_prompt_length": 32,
            "max_response_length": 16,
        },
        "trainer": {
            "runtime_parallel_num": 2,
        },
        "actor_rollout_ref": {
            "rollout": {"n": 2},
        },
        "JiuwenRL": {
            "whole_trajectory": whole_trajectory,
            "custom_fn": {
                "classifier": "default_classify_rollouts",
                "validator": "default_validate_stop",
                "sampler": "default_sampling",
            },
            "final_keep_per_prompt": 8,
        },
    }


@pytest.fixture
def config():
    return _coordinator_config(whole_trajectory=False)


@pytest.fixture
def coordinator(config, mock_tokenizer):
    return TrainingCoordinator(config=config, tokenizer=mock_tokenizer, persistence=None)


@pytest.mark.skipif(not HAS_TORCH, reason="torch required for batch building")
class TestTrainingCoordinatorInitAndConfig:
    """Config validation: legal config initializes and whole_trajectory / pad_token_id match."""

    @staticmethod
    def test_init_with_legal_config_succeeds_and_whole_trajectory_false(config, mock_tokenizer):
        coord = TrainingCoordinator(config=config, tokenizer=mock_tokenizer, persistence=None)
        assert coord.whole_trajectory is False
        assert coord.batch_builder.pad_token_id == mock_tokenizer.pad_token_id
        assert coord.batch_builder.max_prompt_length == 32
        assert coord.batch_builder.max_response_length == 16

    @staticmethod
    def test_init_with_missing_jiuwenrl_whole_trajectory_defaults_false(mock_tokenizer):
        config = _coordinator_config(whole_trajectory=False)
        del config["JiuwenRL"]["whole_trajectory"]
        coord = TrainingCoordinator(config=config, tokenizer=mock_tokenizer, persistence=None)
        assert coord.whole_trajectory is False


class TestBuildInitialTasks:
    @staticmethod
    def test_build_initial_tasks_returns_dict_keyed_by_task_id_and_round_num(coordinator):
        rl_data = {"col_a": [1, 2], "col_b": [3, 4]}
        tasks = coordinator.build_initial_tasks(rl_data)
        assert isinstance(tasks, dict)
        rollout_n = 2
        batch_size = 2
        assert len(tasks) == batch_size * rollout_n
        for t in tasks.values():
            assert t.round_num == 0
            assert t.task_sample == {"col_a": 1, "col_b": 3} or t.task_sample == {"col_a": 2, "col_b": 4}

    @staticmethod
    def test_build_initial_tasks_empty_batch_returns_empty_dict(coordinator):
        rl_data = {"col_a": [], "col_b": []}
        tasks = coordinator.build_initial_tasks(rl_data)
        assert tasks == {}


class TestMergeCaches:
    @staticmethod
    def test_merge_caches_unifies_by_uid():
        pos = {"u1": [RolloutWithReward(input_prompt_ids=[1], output_response_ids=[2], reward=0.5)]}
        neg = {"u1": [RolloutWithReward(input_prompt_ids=[1], output_response_ids=[3], reward=-0.1)], "u2": []}
        merged = TrainingCoordinator.merge_caches(pos, neg)
        assert set(merged.keys()) == {"u1", "u2"}
        assert len(merged["u1"]) == 2
        assert len(merged["u2"]) == 0


class TestClearUpData:
    @staticmethod
    def test_clear_up_data_resets_caches_and_datastore(coordinator):
        coordinator.positive_cache["u1"] = [object()]
        coordinator.negative_cache["u2"] = [object()]
        coordinator.add_round_stats(1, [0.5])
        coordinator.clear_up_data()
        assert coordinator.positive_cache == {}
        assert coordinator.negative_cache == {}
        assert coordinator.get_turn_counts() == []
        assert coordinator.get_reward_lists() == []
        assert coordinator.datastore.is_finished()


class TestConfigureParallelExecutor:
    @staticmethod
    def test_configure_parallel_executor_calls_setters_on_executor(coordinator):
        def _task_runner(t):
            return None

        coordinator.configure_parallel_executor(task_runner=_task_runner)
        assert coordinator.parallel_executor is not None
        assert coordinator.parallel_executor.task_runner is _task_runner


@pytest.mark.skipif(not HAS_TORCH, reason="torch and tensordict required")
class TestBuildRlBatchFromCaches:
    @staticmethod
    def test_build_rl_batch_from_caches_returns_batch_and_merged_dict(coordinator):
        try:
            from tensordict import TensorDict  # noqa: F401
        except ImportError:
            pytest.skip("tensordict required")
        coordinator.positive_cache["u1"] = [
            RolloutWithReward(
                input_prompt_ids=[1, 2, 3],
                output_response_ids=[4, 5],
                reward=0.5,
                n_turns=1,
            )
        ]
        coordinator.negative_cache["u1"] = [
            RolloutWithReward(
                input_prompt_ids=[1, 2, 3],
                output_response_ids=[6, 7],
                reward=-0.1,
                n_turns=1,
            )
        ]
        device = torch.device("cpu")
        rl_batch, merged_dict = coordinator.build_rl_batch_from_caches(device)
        assert isinstance(rl_batch, tuple)
        assert len(rl_batch) == 2
        assert hasattr(rl_batch[0], "batch_size") or isinstance(rl_batch[0], dict)
        assert "u1" in merged_dict
        assert len(merged_dict["u1"]) == 2

    @staticmethod
    def test_build_rl_batch_from_caches_empty_raises(coordinator):
        """Empty positive and negative caches: _build_rl_batch_from_caches raises."""
        from openjiuwen.core.common.exception.errors import BaseError

        coordinator.positive_cache = {}
        coordinator.negative_cache = {}
        device = torch.device("cpu")
        with pytest.raises((Exception, BaseError)):
            coordinator.build_rl_batch_from_caches(device)
