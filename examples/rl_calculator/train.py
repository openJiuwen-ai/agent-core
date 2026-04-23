# -*- coding: UTF-8 -*-
"""
Calculator RL training script.

Trains an agent to solve math problems using a calculator tool.
The agent must output the answer in ``### ANSWER: <answer> ###`` format.

Dataset (same idea as ``rl_nl2sql`` README: fetch from Google Drive, then point
``DATA_DIR`` at the unpacked folder):

    Download ``calc-x-data.zip`` from
    https://drive.google.com/file/d/1FQMyKLLd6hP9dw9rfZn1EZOWNvKaDsqw/view
    Unzip; the folder should contain ``train.parquet`` and ``test.parquet``.

Before training, edit this file to match your machine:
    Parquet: set ``DATA_DIR`` (or ``TrainingConfig.train_files`` /
    ``val_files``) to that directory.
    Model: set ``TrainingConfig.model_path`` to your local Hugging Face model.
    Optional: ``save_path``, ``n_gpus_per_node``, ``visible_device``, etc.

Run:
    cd examples/rl_calculator
    python train.py
"""

from sample_processing import task_data_fn
from prompts import CALCULATOR_SYSTEM_PROMPT
from reward import calc_reward
from tools import calculator

from openjiuwen.core.common.logging import logger
from openjiuwen.agent_evolving.agent_rl import RLConfig, OfflineRLOptimizer
from openjiuwen.agent_evolving.agent_rl.config.offline_config import (
    AgentRuntimeConfig,
    PersistenceConfig,
    RolloutConfig,
    TrainingConfig,
)

# ======================== Configuration ========================

# Directory that contains train.parquet / test.parquet (see README).
DATA_DIR = "/home/data"

system_prompt = CALCULATOR_SYSTEM_PROMPT.format(
    keywords={
        "role": "calculator assistant",
        "tool_name": "calculator",
        "task_type": "math",
        "answer_format": "### ANSWER: <answer> ###",
    }
).content

config = RLConfig(
    training=TrainingConfig(
        model_path="~/model/Qwen2.5-1.5B-Instruct",  # local HF model dir
        train_files=f"{DATA_DIR}/train.parquet",
        val_files=f"{DATA_DIR}/test.parquet",
        train_batch_size=32,
        total_epochs=2,
        max_prompt_length=3072,
        max_response_length=3072,
        n_gpus_per_node=4,
        visible_device="0,1,2,3",
        save_freq=200,
        test_freq=20,
        project_name="CalcX",
        experiment_name="calc_x_grpo",
        algorithm_adv_estimator="grpo",
        whole_trajectory=False,
        logger=["tensorboard"],
        val_before_train=False,
        save_path="./checkpoint/calx",
        micro_batch_size_per_gpu=2,
    ),
    rollout=RolloutConfig(
        rollout_n=4,
        actor_kl_loss_coef=0.02,
        actor_use_kl_loss=True,
        actor_optimizer_lr=5e-7,
        actor_clip_ratio_low=0.2,
        actor_clip_ratio_high=0.3,
    ),
    runtime=AgentRuntimeConfig(
        system_prompt=system_prompt,
        temperature=0.7,
        max_new_tokens=512,
    ),
    persistence=PersistenceConfig(
        enabled=True,
        save_path="./rollout_output",
        flush_interval=100,
        save_rollouts=True,
        save_step_summaries=True,
    ),
    # Switch to Ada for balanced sampling with multi-round rollout:
    # ada=AdaConfig(rollout_max_round=2, final_keep_per_prompt=8),
)

# ======================== Launch ========================


def main():
    optimizer = OfflineRLOptimizer(config)
    optimizer.register_reward(calc_reward, name="calc_reward")
    optimizer.set_tools([calculator])
    optimizer.set_task_data_fn(task_data_fn)

    logger.info("=== Starting Calculator RL Training ===")
    logger.info("Model: %s", config.training.model_path)
    logger.info("Train files: %s", config.training.train_files)
    logger.info("Algorithm: %s", config.training.algorithm_adv_estimator)
    logger.info("Epochs: %d", config.training.total_epochs)

    try:
        optimizer.train()
    except KeyboardInterrupt:
        logger.info("Training interrupted by user")
    finally:
        optimizer.stop()
        logger.info("Training complete")


if __name__ == "__main__":
    main()
