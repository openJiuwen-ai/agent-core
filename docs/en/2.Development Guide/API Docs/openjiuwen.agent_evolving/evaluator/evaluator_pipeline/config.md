# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.config

The `openjiuwen.agent_evolving.evaluator.evaluator_pipeline.config` module defines configuration classes for the evaluation pipeline.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.config.PipelineConfig

```
class PipelineConfig(agent: str, benchmark: str, evolution_mode: bool, max_iterations: int, convergence_check: bool, convergence_threshold: int, stagnation_patience: int, results_dir: Path, save_trajectory: bool, save_skill_history: bool, agent_config: dict[str, Any], bench_config: dict[str, Any], task_ids: list[str], tasks_filter: str)
```

Data class for pipeline configuration, defining various parameters for the evaluation process.

* **agent**(str, optional): Agent adapter name. Default: `"jiuwenswarm"`.
* **benchmark**(str, optional): Benchmark adapter name. Default: `"skillsbench"`.
* **evolution_mode**(bool, optional): Whether to enable evolution mode. Default: `False`.
* **max_iterations**(int, optional): Maximum number of iterations. Default: `1`.
* **convergence_check**(bool, optional): Whether to enable convergence checking. Default: `True`.
* **convergence_threshold**(int, optional): Convergence threshold (consecutive no-change count). Default: `2`.
* **stagnation_patience**(int, optional): Stagnation patience count. Default: `3`.
* **results_dir**(Path, optional): Results save directory. Default: `"./evolution_results"`.
* **save_trajectory**(bool, optional): Whether to save execution trajectory. Default: `True`.
* **save_skill_history**(bool, optional): Whether to save skill history. Default: `True`.
* **agent_config**(dict[str, Any], optional): Agent configuration dictionary. Default: `{}`.
* **bench_config**(dict[str, Any], optional): Benchmark configuration dictionary. Default: `{}`.
* **task_ids**(list[str], optional): List of task IDs to run. Default: `[]`.
* **tasks_filter**(str, optional): Task filter condition. Default: `""`.

### classmethod from_yaml(config_path: Path) -> PipelineConfig

Load configuration from YAML file.

**Parameters:**

* **config_path**(Path): Path to YAML configuration file.

**Returns:**

**PipelineConfig** - loaded configuration object.

### classmethod from_args(**overrides: Any) -> PipelineConfig

Create configuration from arguments (used for CLI argument overrides).

**Parameters:**

* **overrides**: Variable keyword arguments for overriding default configuration.

**Returns:**

**PipelineConfig** - configuration object.

### classmethod from_dict(data: dict[str, Any]) -> PipelineConfig

Create configuration from dictionary.

**Parameters:**

* **data**(dict[str, Any]): Configuration dictionary.

**Returns:**

**PipelineConfig** - configuration object.