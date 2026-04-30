# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Built-in runtime defaults and PPO overlay for online RL."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Launcher runtime config
# ---------------------------------------------------------------------------


Port = Annotated[int | None, Field(ge=1, le=65535)]


class VLLMServiceConfig(BaseModel):
    model_path: str = Field(default="/path/to/your/model")
    model_name: str = Field(default="Qwen3-4B-Thinking-2507")
    host: str = Field(default="127.0.0.1")
    port: Port = None
    gpu_ids: str = Field(default="0,1")
    tp: int = Field(default=2, ge=1)
    existing_url: str | None = None
    health_timeout: float = Field(default=300.0, gt=0)
    env: dict[str, str] = Field(
        default_factory=lambda: {"VLLM_ALLOW_RUNTIME_LORA_UPDATING": "1"},
    )
    extra_args: list[str] = Field(
        default_factory=lambda: [
            "--enable-lora",
            "--max-loras",
            "4",
            "--max-lora-rank",
            "32",
            "--enable-auto-tool-choice",
            "--tool-call-parser",
            "hermes",
            "--max-model-len",
            "32768",
            "--gpu-memory-utilization",
            "0.85",
        ],
    )


class JudgeConfig(VLLMServiceConfig):
    port: Port = None
    gpu_ids: str = Field(default="2,3")
    health_timeout: float = Field(default=600.0, gt=0)
    reuse_inference_if_same_model: bool = True
    env: dict[str, str] = Field(default_factory=dict)
    extra_args: list[str] = Field(
        default_factory=lambda: [
            "--max-model-len",
            "8192",
            "--gpu-memory-utilization",
            "0.85",
            "--max-num-seqs",
            "16",
        ],
    )


class GatewayServiceConfig(BaseModel):
    host: str = Field(default="127.0.0.1")
    port: Port = None
    redis_url: str | None = None
    record_dir: str = Field(default="records")
    log_level: str = Field(default="info")
    health_timeout: float = Field(default=30.0, gt=0)
    disable_trajectory_collection: bool = True
    env: dict[str, str] = Field(default_factory=dict)


class TrajectoryConfig(BaseModel):
    batch_size: int = Field(default=4, ge=1)
    mode: str = Field(default="feedback_level")


class TrainingConfig(BaseModel):
    gpu_ids: str = Field(default="4,5")
    threshold: int = Field(default=4, ge=1)
    scan_interval: int = Field(default=30, ge=1)
    ppo_config: str | None = None
    lora_repo: str | None = None


class JiuwenConfig(BaseModel):
    enabled: bool = True
    agent_server_port: Port = None
    app_host: str = Field(default="127.0.0.1")
    ws_port: Port = None
    web_host: str = Field(default="127.0.0.1")
    web_port: Port = None


class OnlineRLConfig(BaseModel):
    """Top-level launcher config (CLI / optional YAML overlays merge on top of defaults)."""

    demo: bool = False
    inference: VLLMServiceConfig = Field(default_factory=VLLMServiceConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    gateway: GatewayServiceConfig = Field(default_factory=GatewayServiceConfig)
    trajectory: TrajectoryConfig = Field(default_factory=TrajectoryConfig)
    training: TrainingConfig = Field(default_factory=TrainingConfig)
    jiuwen: JiuwenConfig = Field(default_factory=JiuwenConfig)

    @model_validator(mode="after")
    def _sync_and_validate_launch(self) -> OnlineRLConfig:
        if self.judge.reuse_inference_if_same_model:
            self.judge.model_path = self.inference.model_path
            self.judge.model_name = self.inference.model_name
        if self.inference.existing_url is None and self.inference.port is None:
            raise ValueError(
                "inference.port is required when inference.existing_url is not set "
                "(set via YAML or e.g. --vllm-port).",
            )
        if self.judge.existing_url is None and self.judge.port is None:
            raise ValueError(
                "judge.port is required when judge.existing_url is not set "
                "(YAML or e.g. --judge-port).",
            )
        if self.gateway.port is None:
            raise ValueError("gateway.port is required (--gateway-port or YAML).")
        if not self.gateway.redis_url:
            raise ValueError("gateway.redis_url is required (--redis-url or YAML).")
        if self.jiuwen.enabled:
            if self.jiuwen.agent_server_port is None:
                raise ValueError("jiuwen.agent_server_port is required when jiuwen.enabled is true.")
            if self.jiuwen.ws_port is None:
                raise ValueError("jiuwen.ws_port is required when jiuwen.enabled is true.")
            if self.jiuwen.web_port is None:
                raise ValueError("jiuwen.web_port is required when jiuwen.enabled is true.")
        return self


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
            "ppo_micro_batch_size_per_gpu": 2,
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
            "log_prob_micro_batch_size_per_gpu": 2,
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
            "log_prob_micro_batch_size_per_gpu": 2,
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
