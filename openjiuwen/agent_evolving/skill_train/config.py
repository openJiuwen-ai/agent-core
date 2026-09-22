# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Configuration for ReflACT offline skill training."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SkillTrainConfig:
    """Dataclass config for SkillReflACTTrainer."""

    env_name: str = "searchqa"
    output_dir: str = "./outputs/skill_train"
    skill_init: str = ""
    num_epochs: int = 4
    train_size: int = 0
    batch_size: int = 40
    accumulation: int = 1
    minibatch_size: int = 8
    merge_batch_size: int = 8
    analyst_workers: int = 16
    edit_budget: int = 4
    min_edit_budget: int = 2
    lr_scheduler: str = "cosine"
    failure_only: bool = False
    use_gate: bool = True
    gate_metric: str = "hard"
    gate_mixed_weight: float = 0.5
    # Cap for selection_eval_baseline / gate_eval on valid_seen (0 = full split).
    selection_eval_size: int = 40
    seed: int = 42
    num_parallel: int = 4
    skill_update_mode: str = "patch"
    # Align skill_train configs/_base_/default.yaml optimizer + model defaults
    use_slow_update: bool = True
    slow_update_samples: int = 20
    slow_update_gate_with_selection: bool = False
    longitudinal_pair_policy: str = "mixed"
    use_meta_skill: bool = True
    reasoning_effort: Optional[str] = "medium"
    # Target (agent under evaluation) backend: "openai_chat" or
    # "jiuwenswarm_cli_exec" (SkillOpt ``claude_code_exec`` pattern with the
    # jiuwenswarm CLI as the harness). The optimizer always uses chat.
    target_backend: str = "openai_chat"
    # Inject ``jiuwenswarm_trace_steps.txt`` into the analyst prompt when the
    # target is the jiuwenswarm CLI (mirrors ``claude_trace_to_optimizer``).
    jiuwenswarm_trace_to_optimizer: bool = True
    jiuwenswarm_cli_path: str = ""
    jiuwenswarm_gateway_url: str = ""
    jiuwenswarm_chat_mode: str = ""
    jiuwenswarm_instance_name: str = ""
    env_kwargs: Dict[str, Any] = field(default_factory=dict)
    # "train" = ReflACT offline loop; "sleep" = OTLP Trajectory sleep cycle
    mode: str = "train"

    def to_trainer_cfg(self) -> Dict[str, Any]:
        """Flatten into a dict consumed by EnvAdapter.setup()."""
        cfg: Dict[str, Any] = {
            "env": self.env_name,
            "num_epochs": self.num_epochs,
            "train_size": self.train_size,
            "batch_size": self.batch_size,
            "accumulation": self.accumulation,
            "minibatch_size": self.minibatch_size,
            "merge_batch_size": self.merge_batch_size,
            "analyst_workers": self.analyst_workers,
            "edit_budget": self.edit_budget,
            "min_edit_budget": self.min_edit_budget,
            "lr_scheduler": self.lr_scheduler,
            "failure_only": self.failure_only,
            "use_gate": self.use_gate,
            "gate_metric": self.gate_metric,
            "gate_mixed_weight": self.gate_mixed_weight,
            "selection_eval_size": self.selection_eval_size,
            "seed": self.seed,
            "skill_update_mode": self.skill_update_mode,
            "use_slow_update": self.use_slow_update,
            "slow_update_samples": self.slow_update_samples,
            "slow_update_gate_with_selection": self.slow_update_gate_with_selection,
            "longitudinal_pair_policy": self.longitudinal_pair_policy,
            "use_meta_skill": self.use_meta_skill,
            "reasoning_effort": self.reasoning_effort,
            "target_backend": self.target_backend,
            "jiuwenswarm_trace_to_optimizer": self.jiuwenswarm_trace_to_optimizer,
            "jiuwenswarm_cli_path": self.jiuwenswarm_cli_path,
            "jiuwenswarm_gateway_url": self.jiuwenswarm_gateway_url,
            "jiuwenswarm_chat_mode": self.jiuwenswarm_chat_mode,
            "jiuwenswarm_instance_name": self.jiuwenswarm_instance_name,
            "out_root": self.output_dir,
        }
        if self.skill_init:
            cfg["skill_init"] = self.skill_init
        cfg.update(self.env_kwargs)
        return cfg
