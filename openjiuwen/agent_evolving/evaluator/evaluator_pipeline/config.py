# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class BuildConfigArgs:
    tasks_dir: str = "tasks"
    api_key: str | None = None
    api_base: str | None = None
    model_name: str = "glm-5"
    evolution_mode: bool = False
    max_iterations: int = 5
    task_ids: list[str] | None = None
    results_dir: str | None = None
    workspace_dir: str = "/workspace"
    evolution_wait_time: int = 60
    agent_timeout: int = 880
    skill_persistence_dir: str = "~/.jiuwenswarm/agent/workspace/skills"


@dataclass
class PipelineConfig:
    agent: str = "jiuwenswarm"
    benchmark: str = "skillsbench"

    evolution_mode: bool = False
    max_iterations: int = 1

    convergence_check: bool = True
    convergence_threshold: int = 2
    stagnation_patience: int = 3

    results_dir: Path = Path("./evolution_results")
    save_trajectory: bool = True
    save_skill_history: bool = True

    agent_config: dict[str, Any] = field(default_factory=dict)
    bench_config: dict[str, Any] = field(default_factory=dict)

    task_ids: list[str] = field(default_factory=list)
    tasks_filter: str = ""

    @classmethod
    def from_yaml(cls, config_path: Path) -> PipelineConfig:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        pipeline = data.get("pipeline", {})
        agent_cfg = data.get("agent_config", {})
        bench_cfg = data.get("bench_config", {})

        _resolve_env_vars(agent_cfg)
        _resolve_env_vars(bench_cfg)

        return cls(
            agent=pipeline.get("agent", "jiuwenswarm"),
            benchmark=pipeline.get("benchmark", "skillsbench"),
            evolution_mode=pipeline.get("evolution_mode", False),
            max_iterations=pipeline.get("max_iterations", 1),
            convergence_check=pipeline.get("convergence_check", True),
            convergence_threshold=pipeline.get("convergence_threshold", 2),
            stagnation_patience=pipeline.get("stagnation_patience", 3),
            results_dir=Path(pipeline.get("results_dir", "./evolution_results")),
            save_trajectory=pipeline.get("save_trajectory", True),
            save_skill_history=pipeline.get("save_skill_history", True),
            agent_config=agent_cfg,
            bench_config=bench_cfg,
        )

    @classmethod
    def from_args(cls, **overrides: Any) -> PipelineConfig:
        return cls(**overrides)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _resolve_env_vars(cfg: dict[str, Any]) -> None:
    for key, value in list(cfg.items()):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_name = value[2:-1]
            env_value = os.environ.get(env_name)
            if env_value:
                cfg[key] = env_value
