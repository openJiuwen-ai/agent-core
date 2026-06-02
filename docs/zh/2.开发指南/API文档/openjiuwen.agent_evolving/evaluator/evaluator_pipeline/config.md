# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.config

`openjiuwen.agent_evolving.evaluator.evaluator_pipeline.config` 模块定义了评估流水线的配置类。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.config.PipelineConfig

```
class PipelineConfig(agent: str, benchmark: str, evolution_mode: bool, max_iterations: int, convergence_check: bool, convergence_threshold: int, stagnation_patience: int, results_dir: Path, save_trajectory: bool, save_skill_history: bool, agent_config: dict[str, Any], bench_config: dict[str, Any], task_ids: list[str], tasks_filter: str)
```

流水线配置的数据类，定义评估流程的各项参数。

* **agent**(str，可选)：Agent 适配器名称。默认值：`"jiuwenswarm"`。
* **benchmark**(str，可选)：基准测试适配器名称。默认值：`"skillsbench"`。
* **evolution_mode**(bool，可选)：是否启用进化模式。默认值：`False`。
* **max_iterations**(int，可选)：最大迭代次数。默认值：`1`。
* **convergence_check**(bool，可选)：是否启用收敛检查。默认值：`True`。
* **convergence_threshold**(int，可选)：收敛阈值（连续无变化次数）。默认值：`2`。
* **stagnation_patience**(int，可选)：停滞容忍次数。默认值：`3`。
* **results_dir**(Path，可选)：结果保存目录。默认值：`"./evolution_results"`。
* **save_trajectory**(bool，可选)：是否保存执行轨迹。默认值：`True`。
* **save_skill_history**(bool，可选)：是否保存技能历史。默认值：`True`。
* **agent_config**(dict[str, Any]，可选)：Agent 配置字典。默认值：`{}`。
* **bench_config**(dict[str, Any]，可选)：基准测试配置字典。默认值：`{}`。
* **task_ids**(list[str]，可选)：指定运行的任务 ID 列表。默认值：`[]`。
* **tasks_filter**(str，可选)：任务过滤条件。默认值：`""`。

### classmethod from_yaml(config_path: Path) -> PipelineConfig

从 YAML 配置文件加载配置。

**参数：**

* **config_path**(Path)：YAML 配置文件路径。

**返回：**

**PipelineConfig**，加载后的配置对象。

### classmethod from_args(**overrides: Any) -> PipelineConfig

从参数创建配置（用于命令行参数覆盖）。

**参数：**

* **overrides**：可变关键字参数，用于覆盖默认配置。

**返回：**

**PipelineConfig**，配置对象。

### classmethod from_dict(data: dict[str, Any]) -> PipelineConfig

从字典创建配置。

**参数：**

* **data**(dict[str, Any])：配置字典。

**返回：**

**PipelineConfig**，配置对象。