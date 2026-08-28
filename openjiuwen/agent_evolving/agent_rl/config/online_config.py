# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Built-in runtime defaults and PPO overlay for online RL."""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Online PPO Hydra overlay (replaces ``online/yaml/ppo_online_trainer.yaml`` deltas)
# ---------------------------------------------------------------------------

BUILTIN_ONLINE_RL_CONFIG: dict[str, Any] = {}

ONLINE_PPO_VERL_HYDRA_OVERLAY: dict[str, Any] = {
    "data": {
        "train_files": "/dev/null",
        "val_files": "/dev/null",
        "train_batch_size": 8,
        "max_prompt_length": 2048,
        "max_response_length": 2048,
        "truncation": "truncate",
        "filter_overlong_prompts": False,
    },
    "algorithm": {
        "adv_estimator": "reinforce_plus_plus",
        "gamma": 1.0,
        "lam": 1.0,
        "use_kl_in_reward": True,
        "kl_penalty": "kl",
        "kl_ctrl": {
            "type": "fixed",
            "kl_coef": 0.001,
        },
        "filter_groups": False,
    },
    "actor_rollout_ref": {
        "hybrid_engine": True,
        "model": {
            "use_remove_padding": True,
            "enable_gradient_checkpointing": True,
            "lora_rank": 16,
            "lora_alpha": 32,
            "target_modules": "all-linear",
        },
        "actor": {
            "strategy": "fsdp",
            "ppo_mini_batch_size": 4,
            "ppo_micro_batch_size_per_gpu": 1,
            "ppo_epochs": 1,
            "use_kl_loss": False,
            "kl_loss_coef": 0.02,
            "entropy_coeff": 0.01,
            "clip_ratio": 0.2,
            "clip_ratio_low": 0.2,
            "clip_ratio_high": 0.28,
            "loss_agg_mode": "token-mean",
            "fsdp_config": {
                "param_offload": True,
                "optimizer_offload": True,
            },
            "optim": {
                "lr": 1e-5,
                "lr_scheduler_type": "constant",
            },
        },
        "ref": {
            "fsdp_config": {
                "param_offload": True,
            },
            "log_prob_micro_batch_size_per_gpu": 1,
        },
        "rollout": {
            "mode": "async",
            "name": "vllm",
            "tensor_model_parallel_size": 1,
            "enforce_eager": True,
            "gpu_memory_utilization": 0.05,
            "max_model_len": 512,
            "max_num_seqs": 1,
            "n": 1,
            "log_prob_micro_batch_size_per_gpu": 1,
        },
    },
    "trainer": {
        "total_epochs": 1,
        "total_training_steps": None,
        "nnodes": 1,
        "n_gpus_per_node": 2,
        "save_freq": -1,
        "test_freq": -1,
        "val_before_train": False,
        "critic_warmup": 0,
        "balance_batch": False,
        "default_local_dir": "/tmp/online_ppo_ckpt",
        "logger": ["console"],
        "project_name": "agent-online-rl",
        "experiment_name": "online-ppo",
        "device": "cuda",
        "resume_mode": "disable",
    },
    "reward_model": {
        "reward_manager": "naive",
    },
    "JiuwenRL": {
        "whole_trajectory": False,
        "final_keep_per_prompt": None,
        "custom_fn": {
            "classifier": "default_classify_rollouts",
            "validator": "default_validate_stop",
            "sampler": "default_sampling",
        },
    },
}
