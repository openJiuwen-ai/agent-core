import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class MetricConfig:
    name: str
    type: str


@dataclass
class PipelineConfig:
    name: str
    version: str
    task_id: str
    task_path: Path
    agent: str
    max_iterations: int
    convergence_threshold: int
    skill_persistence_dir: Path
    evolution_strategy: str
    dockerfile: Path
    build_context: Path
    container_timeout: int
    container_cpus: int
    container_memory_mb: int
    results_dir: Path
    save_trajectory: bool
    save_skill_history: bool
    generate_visualization: bool
    metrics: list[MetricConfig]
    agent_timeout: int = 880
    api_key: str | None = None
    api_base: str | None = None
    model_name: str = "qwen-plus"
    workspace_dir: str = "/workspace"
    docker_no_cache: bool = False
    docker_build_timeout: int = 3600
    evolution_wait_time: int = 60
    stagnation_patience: int = 3
    base_image: str = "jiuwenswarm-base:latest"
    base_image_dockerfile: Path | None = None
    auto_build_base_image: bool = True
    force_rebuild_base_image: bool = False
    auto_rewrite_dockerfile: bool = True
    base_image_install_mode: str = "auto"
    agent_core_git_url: str = "https://gitcode.com/openJiuwen/agent-core.git@develop"
    jiuwenswarm_git_url: str = "https://gitcode.com/openJiuwen/jiuwenswarm.git@develop"

    def with_task_id(self, task_id: str) -> "PipelineConfig":
        task_path = Path(f"./tasks/{task_id}")
        return PipelineConfig(
            name=self.name,
            version=self.version,
            task_id=task_id,
            task_path=task_path,
            agent=self.agent,
            max_iterations=self.max_iterations,
            convergence_threshold=self.convergence_threshold,
            skill_persistence_dir=self.skill_persistence_dir,
            evolution_strategy=self.evolution_strategy,
            dockerfile=task_path / "environment" / "Dockerfile",
            build_context=task_path / "environment",
            container_timeout=self.container_timeout,
            container_cpus=self.container_cpus,
            container_memory_mb=self.container_memory_mb,
            results_dir=self.results_dir,
            save_trajectory=self.save_trajectory,
            save_skill_history=self.save_skill_history,
            generate_visualization=self.generate_visualization,
            metrics=self.metrics,
            agent_timeout=self.agent_timeout,
            api_key=self.api_key,
            api_base=self.api_base,
            model_name=self.model_name,
            workspace_dir=self.workspace_dir,
            docker_no_cache=self.docker_no_cache,
            docker_build_timeout=self.docker_build_timeout,
            evolution_wait_time=self.evolution_wait_time,
            stagnation_patience=self.stagnation_patience,
            base_image=self.base_image,
            base_image_dockerfile=self.base_image_dockerfile,
            auto_build_base_image=self.auto_build_base_image,
            force_rebuild_base_image=self.force_rebuild_base_image,
            auto_rewrite_dockerfile=self.auto_rewrite_dockerfile,
            base_image_install_mode=self.base_image_install_mode,
            agent_core_git_url=self.agent_core_git_url,
            jiuwenswarm_git_url=self.jiuwenswarm_git_url,
        )

    @classmethod
    def from_yaml(cls, config_path: Path) -> "PipelineConfig":
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        pipeline = data.get("pipeline", {})
        input_cfg = data.get("input", {})
        evolution = data.get("evolution", {})
        container = data.get("container", {})
        output = data.get("output", {})
        metrics_cfg = data.get("metrics", [])

        metrics = [
            MetricConfig(name=m["name"], type=m["type"])
            for m in metrics_cfg
        ]

        skill_dir = Path(evolution.get("skill_persistence_dir", "~/.jiuwenswarm/agent/workspace/skills"))
        skill_dir = skill_dir.expanduser()

        api_key = input_cfg.get("api_key") or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
        api_base = input_cfg.get("api_base") or \
            os.environ.get("DASHSCOPE_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        model_name = input_cfg.get("model_name") or os.environ.get("MODEL_NAME", "qwen-plus")

        task_id = input_cfg.get("task_id", "")
        if not task_id:
            task_id = "__placeholder__"

        default_task_path = f"./tasks/{task_id}"
        task_path_str = input_cfg.get("task_path", default_task_path)
        default_dockerfile = f"{task_path_str}/environment/Dockerfile"
        default_build_context = f"{task_path_str}/environment"

        base_image_dockerfile_str = container.get("base_image_dockerfile")
        base_image_dockerfile = Path(base_image_dockerfile_str) if base_image_dockerfile_str else None

        return cls(
            name=pipeline.get("name", "evaluator-pipeline"),
            version=pipeline.get("version", "1.0.0"),
            task_id=task_id,
            task_path=Path(task_path_str),
            agent=input_cfg.get("agent", "jiuwenswarm"),
            max_iterations=evolution.get("max_iterations", 5),
            convergence_threshold=evolution.get("convergence_threshold", 2),
            skill_persistence_dir=skill_dir,
            evolution_strategy=evolution.get("strategy", "auto-patch"),
            dockerfile=Path(container.get("dockerfile", default_dockerfile)),
            build_context=Path(container.get("build_context", default_build_context)),
            container_timeout=container.get("timeout_sec", 900),
            container_cpus=container.get("cpus", 1),
            container_memory_mb=container.get("memory_mb", 2048),
            results_dir=Path(output.get("results_dir", "./evolution_results/jiuwenswarm")),
            save_trajectory=output.get("save_trajectory", True),
            save_skill_history=output.get("save_skill_history", True),
            generate_visualization=output.get("generate_visualization", True),
            metrics=metrics,
            agent_timeout=container.get("agent_timeout", 880),
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            workspace_dir=container.get("workspace_dir", "/workspace"),
            docker_no_cache=container.get("docker_no_cache", False),
            docker_build_timeout=container.get("docker_build_timeout", 3600),
            evolution_wait_time=evolution.get("wait_time", 30),
            stagnation_patience=evolution.get("stagnation_patience", 3),
            base_image=container.get("base_image", "jiuwenswarm-base:latest"),
            base_image_dockerfile=base_image_dockerfile,
            auto_build_base_image=container.get("auto_build_base_image", True),
            force_rebuild_base_image=container.get("force_rebuild_base_image", False),
            auto_rewrite_dockerfile=container.get("auto_rewrite_dockerfile", True),
            base_image_install_mode=container.get("base_image_install_mode", "auto"),
            agent_core_git_url=container.get(
                "agent_core_git_url", "https://gitcode.com/openJiuwen/agent-core.git@develop"),
            jiuwenswarm_git_url=container.get(
                "jiuwenswarm_git_url", "https://gitcode.com/openJiuwen/jiuwenswarm.git@develop"),
        )

    @classmethod
    def from_args(cls, task_id: str, **overrides: Any) -> "PipelineConfig":
        """Create config directly from arguments, no YAML file needed.

        All parameters have sensible defaults. Pass overrides as keyword
        arguments to customize specific fields.
        """
        task_path = Path(f"./tasks/{task_id}")
        api_key = overrides.pop("api_key", None) or \
            os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
        api_base = overrides.pop("api_base", None) or \
            os.environ.get("DASHSCOPE_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        model_name = overrides.pop("model_name", None) or os.environ.get("MODEL_NAME", "qwen-plus")

        defaults = dict(
            name="evaluator-pipeline",
            version="1.0.0",
            task_id=task_id,
            task_path=task_path,
            agent="jiuwenswarm",
            max_iterations=5,
            convergence_threshold=2,
            skill_persistence_dir=Path("~/.jiuwenswarm/agent/workspace/skills").expanduser(),
            evolution_strategy="auto-patch",
            dockerfile=task_path / "environment" / "Dockerfile",
            build_context=task_path / "environment",
            container_timeout=900,
            container_cpus=1,
            container_memory_mb=2048,
            results_dir=Path("./evolution_results/jiuwenswarm"),
            save_trajectory=True,
            save_skill_history=True,
            generate_visualization=True,
            metrics=[],
            agent_timeout=880,
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            workspace_dir="/workspace",
            docker_no_cache=False,
            docker_build_timeout=3600,
            evolution_wait_time=60,
            stagnation_patience=3,
            base_image="jiuwenswarm-base:latest",
            base_image_dockerfile=None,
            auto_build_base_image=True,
            force_rebuild_base_image=False,
            auto_rewrite_dockerfile=True,
            base_image_install_mode="auto",
            agent_core_git_url="https://gitcode.com/openJiuwen/agent-core.git@develop",
            jiuwenswarm_git_url="https://gitcode.com/openJiuwen/jiuwenswarm.git@develop",
        )
        defaults.update(overrides)
        return cls(**defaults)


@dataclass
class IterationResult:
    iteration: int
    skill_content: str | None
    skill_hash: str
    agent_output: str
    agent_trajectory: list[dict]
    agent_execution_time: float
    agent_tokens_used: int
    test_passed: bool
    test_pass_rate: float
    test_details: dict
    skill_changed: bool
    evolution_suggestions: str | None
    started_at: datetime
    completed_at: datetime
    evolution_events: list[dict] = field(default_factory=list)


@dataclass
class PipelineResult:
    task_id: str
    agent: str
    total_iterations: int
    convergence_achieved: bool
    results: list[IterationResult]
    metrics: dict[str, Any]
    skill_history: list[Path]
    output_dir: Path
    report_path: Path | None = None
