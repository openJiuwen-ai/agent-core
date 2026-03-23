# openjiuwen.dev_tools.agentrl.optimizer

## class openjiuwen.dev_tools.agentrl.optimizer.rl_optimizer.RLOptimizer

```
class openjiuwen.dev_tools.agentrl.optimizer.rl_optimizer.RLOptimizer(config: RLConfig)
```

Top-level RL training entry point.

**Usage**:

```python
optimizer = RLOptimizer(config)
optimizer.register_reward(my_reward_fn, name="my_reward")
optimizer.set_tools([calculator])
optimizer.set_task_data_fn(my_task_data_fn)
optimizer.train()
```

### __init__(self, config: RLConfig) -> None

Initialize RL optimizer.

**Parameters**:

* **config**(RLConfig): RL configuration.

### def set_tools(self, tools: list) -> None

Register tools for the Agent.

**Parameters**:

* **tools**(list): List of tools.

### def set_task_runner(self, task_runner) -> None

Set custom task runner: `async (RLTask) -> RolloutMessage`.

### def set_task_data_fn(self, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None

Set the function that converts dataset rows to Agent input.

### def register_reward(self, fn, name: Optional[str] = None) -> None

Register reward function in the global reward registry.

**Parameters**:

* **fn**: Reward function.
* **name**(Optional[str], optional): Reward name, default uses function name. Default: `None`.

### def set_agent_factory(self, factory: Callable[[RLTask], Any]) -> None

Override the default AgentFactory.

### def init_trainer(self) -> None

Initialize the Ray-based training system.

### def start_training(self) -> None

Start the training loop on the remote TaskRunner.

### def stop(self) -> None

Tear down TaskRunner actor and shut down Ray.

### def train(self) -> None

Initialize and run the full training pipeline in a single call.

**Example**:

```python
>>> from openjiuwen.dev_tools.agentrl import RLOptimizer
>>> from openjiuwen.dev_tools.agentrl.config.schemas import (
...     RLConfig,
...     TrainingConfig,
...     RolloutConfig,
...     AgentRuntimeConfig,
... )
>>> 
>>> # 1. Configure RL training parameters
>>> config = RLConfig(
...     training=TrainingConfig(
...         model_path="/path/to/your/model",
...         train_data_path="/path/to/train.jsonl",
...         val_data_path="/path/to/val.jsonl",
...         total_epochs=2,
...     ),
...     rollout=RolloutConfig(
...         rollout_n=8,
...     ),
...     runtime=AgentRuntimeConfig(
...         system_prompt="You are a helpful assistant.",
...         temperature=0.7,
...     ),
... )
>>> 
>>> # 2. Create optimizer and configure
>>> optimizer = RLOptimizer(config)
>>> optimizer.set_tools([your_tool1, your_tool2])
>>> 
>>> # 3. Register reward function
>>> def my_reward_fn(rollout_message):
...     # Compute reward based on rollout_message
...     return 1.0
>>> optimizer.register_reward(my_reward_fn, name="my_reward")
>>> 
>>> # 4. Run training
>>> optimizer.train()
```

---

## class openjiuwen.dev_tools.agentrl.optimizer.task_runner.TaskRunner

```
@ray.remote(num_cpus=1) class openjiuwen.dev_tools.agentrl.optimizer.task_runner.TaskRunner()
```

Ray remote Actor for coordinating training initialization and execution. Created and managed internally by `RLOptimizer`; advanced users may also use it directly for custom Ray deployment.

### __init__(self) -> None

Initialize TaskRunner.

### def init_trainer(self, config, *, task_runner=None, agent_factory=None, task_data_fn=None, reward_fn=None, metrics_tracker=None, persistence=None) -> None

Initialize all training components and Ray workers.

**Parameters**:

* **config**: Full Verl configuration (OmegaConf DictConfig).
* **task_runner**(optional): Custom task runner `async (RLTask) -> RolloutMessage`. Default: `None`.
* **agent_factory**(optional): Agent factory. Default: `None`.
* **task_data_fn**(optional): Function that converts dataset rows to Agent input. Default: `None`.
* **reward_fn**(optional): Rollout reward function. Default: `None`.
* **metrics_tracker**(optional): Metrics tracker. Default: `None`.
* **persistence**(optional): Rollout persistence implementation. Default: `None`.

### def start_trainer(self) -> None

Start the main training loop. Must call `init_trainer()` first.

**Exceptions**:

* **BaseError**: Raised when called before `init_trainer()`.

---

## func openjiuwen.dev_tools.agentrl.optimizer.task_runner.get_ppo_ray_runtime_env

```
def get_ppo_ray_runtime_env() -> dict
```

Returns default runtime environment configuration for PPO/GRPO Ray workers.

Inherits `working_dir` from Ray Job and merges in `PYTHONPATH` and environment variables required by agentrl (e.g. `TOKENIZERS_PARALLELISM`, `NCCL_DEBUG`, etc.).

**Returns**:

**dict**, A runtime environment dict usable with `ray.init(runtime_env=...)`.

**Example**:

```python
>>> from openjiuwen.dev_tools.agentrl.optimizer.task_runner import get_ppo_ray_runtime_env
>>> 
>>> runtime_env = get_ppo_ray_runtime_env()
>>> # Use with ray.init or Ray Job config
>>> import ray
>>> ray.init(runtime_env=runtime_env)
```
