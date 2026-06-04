# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline

`openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline` 模块提供评估流水线的核心实现。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.EvolutionPipeline

```
class EvolutionPipeline(config: PipelineConfig)
```

进化流水线核心类，协调 Agent 和 Benchmark 执行评估任务，支持单轮运行和多轮进化两种模式。

**参数：**

* **config**(PipelineConfig)：流水线配置对象。

### async run() -> list[PipelineResult]

运行整个评估流水线。

**返回：**

**list[PipelineResult]**，所有任务的执行结果列表。

**样例：**

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.evaluator.evaluator_pipeline import (
...     EvolutionPipeline,
...     PipelineConfig,
... )
>>> 
>>> # 创建配置
>>> config = PipelineConfig(
...     agent="jiuwenswarm",
...     benchmark="skillsbench",
...     evolution_mode=True,
...     max_iterations=5,
...     results_dir="./evolution_results",
...     agent_config={
...         "api_key": "your_api_key",
...         "model_name": "glm-5",
...     },
...     bench_config={
...         "tasks_dir": "./tasks",
...     },
... )
>>> 
>>> # 创建并运行流水线
>>> pipeline = EvolutionPipeline(config)
>>> results = asyncio.run(pipeline.run())
>>> 
>>> # 打印结果
>>> for result in results:
...     print(f"任务 {result.task_id}: {'通过' if result.convergence_achieved else '失败'}")
```

---

## func openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.create_agent(name: str, config: dict[str, Any]) -> BaseAgentAdapter

创建 Agent 适配器实例。

**参数：**

* **name**(str)：Agent 名称。
* **config**(dict[str, Any])：Agent 配置字典。

**返回：**

**BaseAgentAdapter**，Agent 适配器实例。

**异常：**

* **ValueError**：未知的 Agent 名称。

---

## func openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.create_bench(name: str, config: dict[str, Any]) -> BaseBenchAdapter

创建基准测试适配器实例。

**参数：**

* **name**(str)：基准测试名称。
* **config**(dict[str, Any])：基准测试配置字典。

**返回：**

**BaseBenchAdapter**，基准测试适配器实例。

**异常：**

* **ValueError**：未知的基准测试名称。