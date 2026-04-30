# openjiuwen.agent_evolving.agent_rl.offline.store

**Rollout persistence** and **training metrics / diagnostics** (`RLMetricsTracker`, `TrainingStepMetrics`, `TrainingDiagnostics`) live in this package; metrics and diagnostics are implemented in `offline/store/metrics_tracker.py` (the former standalone `monitoring` page is merged here).

## Rollout persistence

## class openjiuwen.agent_evolving.agent_rl.offline.store.base.RolloutPersistence

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.base.RolloutPersistence(ABC)
```

Abstract interface for Rollout persistence.

### save_rollout(self, step: int, task_id: str, rollout: RolloutMessage, *, phase: str = "train") -> None

Persist a single rollout and its full trajectory.

**Parameters**:

* **step**(int): Current training step.
* **task_id**(str): Task identifier.
* **rollout**(RolloutMessage): Rollout message to persist.
* **phase**(str, optional): `"train"` or `"val"` — determines output subdirectory. Default: `"train"`.

### save_step_summary(self, step: int, metrics: Dict[str, Any]) -> None

Persist per-step training summary metrics.

**Parameters**:

* **step**(int): Step number.
* **metrics**(Dict[str, Any]): Metrics dictionary.

### query_rollouts(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]

Query historical rollouts by filters (for analysis/debugging).

**Parameters**:

* **filters**(Dict[str, Any]): Filter dictionary.
* **limit**(int, optional): Result limit. Default: `100`.

**Returns**:

**List[Dict[str, Any]]**, List of matching rollout documents.

### close(self) -> None

Release connections and clean up resources.

## class openjiuwen.agent_evolving.agent_rl.offline.store.file_store.FileRolloutStore

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.file_store.FileRolloutStore(save_path: str, flush_interval: int = 100)
```

Persist rollout data to local JSONL files, grouped by step range.

**Directory structure**:

```
save_path/
├── train/
│   └── rollouts/
│       ├── steps_000000_000099.jsonl
│       └── ...
├── val/
│   └── rollouts/
│       ├── steps_000000_000099.jsonl
│       └── ...
└── step_summaries/
    └── steps_000000_000099.jsonl
```

Each `.jsonl` file has one JSON object per line.

### __init__(self, save_path: str, flush_interval: int = 100) -> None

Initialize file rollout store.

**Parameters**:

* **save_path**(str): Root directory for all rollout output files.
* **flush_interval**(int, optional): Step range per file (e.g. 100 means steps 0-99 go to one file, 100-199 to the next). Default: `100`.

## class openjiuwen.agent_evolving.agent_rl.offline.store.null_store.NullRolloutStore

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.null_store.NullRolloutStore()
```

No-op implementation when persistence is disabled. All methods succeed silently with no I/O.

**Example**:

```python
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.base import RolloutPersistence
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.null_store import NullRolloutStore
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.file_store import FileRolloutStore
>>> 
>>> # Use NullRolloutStore (persistence disabled)
>>> store = NullRolloutStore()
>>> 
>>> # Use FileRolloutStore (persistence enabled)
>>> store = FileRolloutStore(save_path="/path/to/save", flush_interval=100)
```

## Metrics and diagnostics (`metrics_tracker`)

## class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.TrainingStepMetrics

```python
@dataclass class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.TrainingStepMetrics(step: int, epoch: int, verl_metrics: Dict[str, Any], avg_turns: float, reward_mean: float, consecutive_zero_reward_steps: int)
```

Dataclass encapsulating all metrics for a single training step log entry.

**Fields**:

* **step**(int): Training step.
* **epoch**(int): Training epoch.
* **verl_metrics**(Dict[str, Any]): Verl raw metrics dictionary.
* **avg_turns**(float): Average dialogue turns.
* **reward_mean**(float): Mean reward value.
* **consecutive_zero_reward_steps**(int): Consecutive zero-reward steps.

## class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.RLMetricsTracker

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.RLMetricsTracker(project_name: str, experiment_name: str, backends: List[str], config: Optional[Dict[str, Any]] = None)
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
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker import RLMetricsTracker, TrainingStepMetrics
>>>
>>> tracker = RLMetricsTracker(
...     project_name="my_project",
...     experiment_name="my_experiment",
...     backends=["tensorboard"],
... )
>>> step_metrics = TrainingStepMetrics(
...     step=0,
...     epoch=1,
...     verl_metrics={"loss": 0.5},
...     avg_turns=3.5,
...     reward_mean=0.8,
...     consecutive_zero_reward_steps=0,
... )
>>> tracker.log_training_step(step_metrics)
>>> tracker.finish()
```

## class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.TrainingDiagnostics

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.TrainingDiagnostics(tokenizer=None)
```

Consolidated stage-wise training diagnostics (e.g. `DIAG_DATA` encoding, `DIAG_S0`–`DIAG_S4` for batch assembly, advantages, actor updates). Used when `develop_mode` is enabled from `VerlTrainingExecutor`, `encoding`, `batch_builder`, etc.; mainly static helpers such as `diag_encoding`, `diag_batch_assembly`, `diag_after_reward`. See source in `metrics_tracker.py` for full detail.
