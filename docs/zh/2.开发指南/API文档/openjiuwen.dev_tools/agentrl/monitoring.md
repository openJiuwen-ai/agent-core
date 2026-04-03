# openjiuwen.dev_tools.agentrl.monitoring

## class openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker.TrainingStepMetrics

```python
@dataclass class openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker.TrainingStepMetrics(step: int, epoch: int, verl_metrics: Dict[str, Any], avg_turns: float, reward_mean: float, consecutive_zero_reward_steps: int)
```

封装单个训练步日志条目的所有指标。

**字段**：

* **step**(int)：训练步数。
* **epoch**(int)：训练轮数。
* **verl_metrics**(Dict[str, Any])：Verl 原始指标字典。
* **avg_turns**(float)：平均对话轮次。
* **reward_mean**(float)：平均奖励值。
* **consecutive_zero_reward_steps**(int)：连续零奖励步数。

## class openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker.RLMetricsTracker

```python
class openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker.RLMetricsTracker(project_name: str, experiment_name: str, backends: List[str], config: Optional[Dict[str, Any]] = None)
```

用于 RL 训练的结构化指标跟踪器。

使用 verl 的 Tracking 作为统一日志后端。

### __init__(self, project_name: str, experiment_name: str, backends: List[str], config: Optional[Dict[str, Any]] = None) -> None

初始化指标跟踪器。

**参数**：

* **project_name**(str)：项目名称。
* **experiment_name**(str)：实验名称。
* **backends**(List[str])：日志后端列表。
* **config**(Optional[Dict[str, Any]]，可选)：配置字典。默认值：`None`。

### log_step(self, step: int, metrics: Dict[str, Any]) -> None

在给定步数下记录标量指标字典。

### log_training_step(self, data: TrainingStepMetrics) -> None

记录包含 RL 增强指标的完整训练步。

### log_rollout_stats(self, step: int, rewards_by_uid: Dict[str, List[Dict[str, Any]]], total_positive: int = 0, total_negative: int = 0, total_training_samples: Optional[int] = None) -> None

记录结构化的 rollout 统计信息。

### log_reward_distribution(self, step: int, rewards: List[float]) -> None

记录奖励分布直方图（仅 WandB）。

### log_validation(self, step: int, val_metrics: Dict[str, Any]) -> None

记录验证指标。

### finish(self) -> None

清理跟踪资源。

**样例**：

```python
>>> from openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker import RLMetricsTracker, TrainingStepMetrics
>>> 
>>> # 初始化跟踪器
>>> tracker = RLMetricsTracker(
...     project_name="my_project",
...     experiment_name="my_experiment",
...     backends=["tensorboard"],
... )
>>> 
>>> # 记录训练步
>>> step_metrics = TrainingStepMetrics(
...     step=0,
...     epoch=1,
...     verl_metrics={"loss": 0.5},
...     avg_turns=3.5,
...     reward_mean=0.8,
...     consecutive_zero_reward_steps=0,
... )
>>> tracker.log_training_step(step_metrics)
>>> 
>>> # 记录 rollout 统计
>>> rewards_by_uid = {
...     "task_1": [{"global": 1.0}, {"global": 0.5}],
...     "task_2": [{"global": 0.8}],
... }
>>> tracker.log_rollout_stats(step=0, rewards_by_uid=rewards_by_uid, total_positive=2, total_negative=1)
>>> 
>>> # 完成
>>> tracker.finish()
```
