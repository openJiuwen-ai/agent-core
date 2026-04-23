# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Online RL module (stub).

Planned directory structure for future online RL migration:

    online/
    ├── gateway/          # FastAPI proxy: intercept LLM calls, record trajectories
    │   ├── config.py
    │   ├── constants.py
    │   ├── server.py
    │   ├── proxy.py
    │   ├── state.py
    │   ├── processor.py
    │   ├── forwarder.py
    │   ├── judge_scorer.py
    │   ├── message_utils.py
    │   ├── output_router.py
    │   ├── recorder.py
    │   ├── reward_computor.py
    │   ├── utils.py
    │   └── verl_converter.py
    ├── judge/            # LLM-as-Judge scoring service
    │   ├── judge_server.py
    │   ├── judge_client.py
    │   └── reward_bridge.py
    ├── scheduler/        # Online training scheduler
    │   └── online_training_scheduler.py
    ├── trainer/          # Online PPO trainer (Ray Actor)
    │   └── online_ppo_trainer.py
    ├── storage/          # Online-specific persistence
    │   ├── models.py
    │   ├── trajectory_store.py
    │   ├── redis_trajectory_store.py
    │   └── lora_repo.py
    └── inference/        # vLLM LoRA hot-loading
        └── notifier.py

This module will be implemented in a future iteration.
"""
