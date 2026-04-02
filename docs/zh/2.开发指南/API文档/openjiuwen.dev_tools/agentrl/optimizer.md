# openjiuwen.dev_tools.agentrl.optimizer

## class openjiuwen.dev_tools.agentrl.optimizer.rl_optimizer.RLOptimizer

```python
class openjiuwen.dev_tools.agentrl.optimizer.rl_optimizer.RLOptimizer(config: RLConfig)
```

顶级 RL 训练入口点。

**Usage**：

```python
optimizer = RLOptimizer(config)
optimizer.register_reward(my_reward_fn, name="my_reward")
optimizer.set_tools([calculator])
optimizer.set_task_data_fn(my_task_data_fn)
optimizer.train()
```

### __init__(self, config: RLConfig) -> None

初始化 RL 优化器。

**参数**：

* **config**(RLConfig)：RL 配置。

### set_tools(self, tools: list) -> None

为 Agent 注册工具。

**参数**：

* **tools**(list)：工具列表。

### set_task_runner(self, task_runner) -> None

设置自定义任务运行器：`async (RLTask) -> RolloutMessage`。

### set_task_data_fn(self, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None

设置将数据集行转换为 Agent 输入的函数。

### register_reward(self, fn, name: Optional[str] = None) -> None

在全局奖励注册表中注册奖励函数。

**参数**：

* **fn**：奖励函数。
* **name**(Optional[str]，可选)：奖励名称，默认使用函数名。默认值：`None`。

### set_agent_factory(self, factory: Callable[[RLTask], Any]) -> None

覆盖默认的 AgentFactory。

### init_trainer(self) -> None

初始化基于 Ray 的训练系统。

### start_training(self) -> None

在远程 TaskRunner 上启动训练循环。

### stop(self) -> None

销毁远程 `TaskRunner` Actor，并执行 Ray 运行时关闭流程。

### train(self) -> None

在一个调用中初始化并运行完整训练流水线。

**样例**：

```python
>>> from openjiuwen.dev_tools.agentrl import RLOptimizer
>>> from openjiuwen.dev_tools.agentrl.config.schemas import (
...     RLConfig,
...     TrainingConfig,
...     RolloutConfig,
...     AgentRuntimeConfig,
... )
>>> 
>>> # 1. 配置 RL 训练参数
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
>>> # 2. 创建优化器并配置
>>> optimizer = RLOptimizer(config)
>>> optimizer.set_tools([your_tool1, your_tool2])
>>> 
>>> # 3. 注册奖励函数
>>> def my_reward_fn(rollout_message):
...     # 根据 rollout_message 计算奖励
...     return 1.0
>>> optimizer.register_reward(my_reward_fn, name="my_reward")
>>> 
>>> # 4. 运行训练
>>> optimizer.train()
```

## class openjiuwen.dev_tools.agentrl.optimizer.task_runner.TaskRunner

```python
@ray.remote(num_cpus=1) class openjiuwen.dev_tools.agentrl.optimizer.task_runner.TaskRunner()
```

Ray 远程 Actor，负责训练初始化与执行调度。默认由 `RLOptimizer` 在内部创建并持有；也可在 Ray 集群侧自行实例化，用于自定义部署拓扑或接入方式。

### __init__(self) -> None

初始化 TaskRunner。

### init_trainer(self, config, *, task_runner=None, agent_factory=None, task_data_fn=None, reward_fn=None, metrics_tracker=None, persistence=None) -> None

初始化所有训练组件和 Ray workers。

**参数**：

* **config**：Verl 完整配置（OmegaConf DictConfig）。
* **task_runner**(optional)：自定义任务运行器 `async (RLTask) -> RolloutMessage`。默认值：`None`。
* **agent_factory**(optional)：Agent 工厂。默认值：`None`。
* **task_data_fn**(optional)：将数据集行转换为 Agent 输入的函数。默认值：`None`。
* **reward_fn**(optional)：Rollout 奖励函数。默认值：`None`。
* **metrics_tracker**(optional)：指标跟踪器。默认值：`None`。
* **persistence**(optional)：Rollout 持久化实现。默认值：`None`。

### start_trainer(self) -> None

启动主训练循环。必须先调用 `init_trainer()`。

**异常**：

* **BaseError**：在 `init_trainer()` 之前调用时抛出。

## func openjiuwen.dev_tools.agentrl.optimizer.task_runner.get_ppo_ray_runtime_env

```python
def get_ppo_ray_runtime_env() -> dict
```

返回用于 PPO/GRPO Ray Worker 的默认运行时环境配置。

继承 Ray Job 的 `working_dir`，并合并 agentrl 所需的 `PYTHONPATH` 及环境变量（如 `TOKENIZERS_PARALLELISM`、`NCCL_DEBUG` 等）。

**返回**：

**dict**，可供 `ray.init(runtime_env=...)` 使用的运行时环境字典。

**样例**：

```python
>>> from openjiuwen.dev_tools.agentrl.optimizer.task_runner import get_ppo_ray_runtime_env
>>> 
>>> runtime_env = get_ppo_ray_runtime_env()
>>> # 用于 ray.init 或 Ray Job 配置
>>> import ray
>>> ray.init(runtime_env=runtime_env)
```
