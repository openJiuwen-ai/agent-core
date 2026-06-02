# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline

The `openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline` module provides the core implementation of the evaluation pipeline.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.EvolutionPipeline

```
class EvolutionPipeline(config: PipelineConfig)
```

Core evolution pipeline class that coordinates Agents and Benchmarks to execute evaluation tasks, supporting both single-run and multi-run evolution modes.

**Parameters:**

* **config**(PipelineConfig): Pipeline configuration object.

### async run() -> list[PipelineResult]

Run the entire evaluation pipeline.

**Returns:**

**list[PipelineResult]** - list of execution results for all tasks.

**Example:**

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.evaluator.evaluator_pipeline import (
...     EvolutionPipeline,
...     PipelineConfig,
... )
>>> 
>>> # Create configuration
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
>>> # Create and run pipeline
>>> pipeline = EvolutionPipeline(config)
>>> results = asyncio.run(pipeline.run())
>>> 
>>> # Print results
>>> for result in results:
...     print(f"Task {result.task_id}: {'Passed' if result.convergence_achieved else 'Failed'}")
```

---

## func openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.create_agent(name: str, config: dict[str, Any]) -> BaseAgentAdapter

Create Agent adapter instance.

**Parameters:**

* **name**(str): Agent name.
* **config**(dict[str, Any]): Agent configuration dictionary.

**Returns:**

**BaseAgentAdapter** - Agent adapter instance.

**Exceptions:**

* **ValueError**: Unknown Agent name.

---

## func openjiuwen.agent_evolving.evaluator.evaluator_pipeline.pipeline.create_bench(name: str, config: dict[str, Any]) -> BaseBenchAdapter

Create benchmark adapter instance.

**Parameters:**

* **name**(str): Benchmark name.
* **config**(dict[str, Any]): Benchmark configuration dictionary.

**Returns:**

**BaseBenchAdapter** - benchmark adapter instance.

**Exceptions:**

* **ValueError**: Unknown benchmark name.