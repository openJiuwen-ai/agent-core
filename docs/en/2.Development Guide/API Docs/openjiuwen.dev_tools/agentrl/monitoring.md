# openjiuwen.dev_tools.agentrl.monitoring

## class openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker.TrainingStepMetrics

```python
@dataclass class openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker.TrainingStepMetrics(step: int, epoch: int, verl_metrics: Dict[str, Any], avg_turns: float, reward_mean: float, consecutive_zero_reward_steps: int)
```

Dataclass encapsulating all metrics for a single training step log entry.

**Fields**:

* **step**(int): Training step.
* **epoch**(int): Training epoch.
* **verl_metrics**(Dict[str, Any]): Verl raw metrics dictionary.
* **avg_turns**(float): Average dialogue turns.
* **reward_mean**(float): Mean reward value.
* **consecutive_zero_reward_steps**(int): Consecutive zero-reward steps.

## class openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker.RLMetricsTracker

```python
class openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker.RLMetricsTracker(project_name: str, experiment_name: str, backends: List[str], config: Optional[Dict[str, Any]] = None)
```

Structured metrics tracker for RL training.

Uses verl's Tracking as the unified logging backend.

### __init__(self, project_name: str, experiment_name: str, backends: List[str], config: Optional[Dict[str, Any]] = None) -> None

Initialize metrics tracker.

**Parameters**:

* **project_name**(str): Project name.
* **experiment_name**(str): Experiment name.
* **backends**(List[str]): Logging backend list.
* **config**(Optional[Dict[str, Any]], optional): Config dictionary. Default: `None`.

### log_step(self, step: int, metrics: Dict[str, Any]) -> None

Log scalar metrics dictionary at given step.

### log_training_step(self, data: TrainingStepMetrics) -> None

Log complete training step with RL-augmented metrics.

### log_rollout_stats(self, step: int, rewards_by_uid: Dict[str, List[Dict[str, Any]]], total_positive: int = 0, total_negative: int = 0, total_training_samples: Optional[int] = None) -> None

Log structured rollout statistics.

### log_reward_distribution(self, step: int, rewards: List[float]) -> None

Log reward distribution histogram (WandB only).

### log_validation(self, step: int, val_metrics: Dict[str, Any]) -> None

Log validation metrics.

### finish(self) -> None

Clean up tracking resources.

**Example**:

```python
>>> from openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker import RLMetricsTracker, TrainingStepMetrics
>>> 
>>> # Initialize tracker
>>> tracker = RLMetricsTracker(
...     project_name="my_project",
...     experiment_name="my_experiment",
...     backends=["tensorboard"],
... )
>>> 
>>> # Log training step
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
>>> # Log rollout stats
>>> rewards_by_uid = {
...     "task_1": [{"global": 1.0}, {"global": 0.5}],
...     "task_2": [{"global": 0.8}],
... }
>>> tracker.log_rollout_stats(step=0, rewards_by_uid=rewards_by_uid, total_positive=2, total_negative=1)
>>> 
>>> # Finish
>>> tracker.finish()
```
