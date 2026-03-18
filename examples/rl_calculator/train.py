# -*- coding: UTF-8 -*-
"""
Calculator RL training script.

Trains an agent to solve math problems using a calculator tool.
The agent must output the answer in ``### ANSWER: <answer> ###`` format.
"""

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl import RLConfig, RLOptimizer
from openjiuwen.dev_tools.agentrl.config.schemas import (
    AgentRuntimeConfig,
    PersistenceConfig,
    RolloutConfig,
    TrainingConfig,
)

from .sample_processing import task_data_fn
from .prompts import CALCULATOR_SYSTEM_PROMPT
from .reward import calc_reward
from .tools import calculator

# ======================== Configuration ========================

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
        model_path="~/model/Qwen2.5-1.5B-Instruct",
        train_files=f"{DATA_DIR}/train.parquet",
        val_files=f"{DATA_DIR}/test.parquet",
        train_batch_size=32,
        gen_batch_size=32,
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
        whole_trajectory=True,
        logger=["tensorboard"],
        val_before_train=False,
        save_path="~/checkpoint/calx"
    ),
    rollout=RolloutConfig(
        rollout_n=4,
        rollout_max_round=1,
        actor_optimizer_lr=1e-6,
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
    optimizer = RLOptimizer(config)
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
