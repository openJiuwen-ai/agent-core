# openjiuwen.agent_evolving.agent_rl.offline.store

**Rollout 持久化**与**训练指标 / 诊断**（`RLMetricsTracker`、`TrainingStepMetrics`、`TrainingDiagnostics`）均在该包内；指标与诊断实现位于 `offline/store/metrics_tracker.py`（原独立 `monitoring` 文档已并入本篇）。

## Rollout 持久化

## class openjiuwen.agent_evolving.agent_rl.offline.store.base.RolloutPersistence

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.base.RolloutPersistence(ABC)
```

Rollout 持久化的抽象接口。

### save_rollout(self, step: int, task_id: str, rollout: RolloutMessage, *, phase: str = "train") -> None

持久化单个 rollout 及其完整轨迹。

**参数**：

* **step**(int)：当前训练步数。
* **task_id**(str)：任务标识符。
* **rollout**(RolloutMessage)：要持久化的 rollout 消息。
* **phase**(str，可选)：`"train"` 或 `"val"` — 决定输出子目录。默认值：`"train"`。

### save_step_summary(self, step: int, metrics: Dict[str, Any]) -> None

持久化每步训练摘要指标。

**参数**：

* **step**(int)：步数。
* **metrics**(Dict[str, Any])：指标字典。

### query_rollouts(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]

按过滤器查询历史 rollouts（用于分析/调试）。

**参数**：

* **filters**(Dict[str, Any])：过滤器字典。
* **limit**(int，可选)：返回结果数量限制。默认值：`100`。

**返回**：

**List[Dict[str, Any]]**，匹配的 rollout 文档列表。

### close(self) -> None

释放连接并清理资源。

## class openjiuwen.agent_evolving.agent_rl.offline.store.file_store.FileRolloutStore

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.file_store.FileRolloutStore(save_path: str, flush_interval: int = 100)
```

将 rollout 数据持久化到本地 JSONL 文件，按步数范围分组。

**目录结构**：

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

每个 `.jsonl` 文件每行包含一个 JSON 对象。

### __init__(self, save_path: str, flush_interval: int = 100) -> None

初始化文件 rollout 存储。

**参数**：

* **save_path**(str)：所有 rollout 输出文件的根目录。
* **flush_interval**(int，可选)：每个文件的步数间隔（例如 100 表示步数 0-99 写入一个文件，100-199 写入下一个）。默认值：`100`。

## class openjiuwen.agent_evolving.agent_rl.offline.store.null_store.NullRolloutStore

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.null_store.NullRolloutStore()
```

当持久化禁用时的无操作实现。所有方法静默成功，不执行任何 I/O。

**样例**：

```python
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.base import RolloutPersistence
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.null_store import NullRolloutStore
>>> from openjiuwen.agent_evolving.agent_rl.offline.store.file_store import FileRolloutStore
>>> 
>>> # 使用 NullRolloutStore（持久化禁用）
>>> store = NullRolloutStore()
>>> 
>>> # 使用 FileRolloutStore（持久化启用）
>>> store = FileRolloutStore(save_path="/path/to/save", flush_interval=100)
```

## 训练指标与诊断（`metrics_tracker`）

## class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.TrainingStepMetrics

```python
@dataclass class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.TrainingStepMetrics(step: int, epoch: int, verl_metrics: Dict[str, Any], avg_turns: float, reward_mean: float, consecutive_zero_reward_steps: int)
```

封装单个训练步日志条目的所有指标。

**字段**：

* **step**(int)：训练步数。
* **epoch**(int)：训练轮数。
* **verl_metrics**(Dict[str, Any])：Verl 原始指标字典。
* **avg_turns**(float)：平均对话轮次。
* **reward_mean**(float)：平均奖励值。
* **consecutive_zero_reward_steps**(int)：连续零奖励步数。

## class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.RLMetricsTracker

```python
class openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker.RLMetricsTracker(project_name: str, experiment_name: str, backends: List[str], config: Optional[Dict[str, Any]] = None)
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

整合 RL 训练管线各阶段的日志诊断（如 `DIAG_DATA` 编码、`DIAG_S0`–`DIAG_S4` 的 batch / 优势 / actor 更新等）。在 `develop_mode` 下由 `VerlTrainingExecutor`、`encoding` 与 `batch_builder` 等调用；方法以静态为主（如 `diag_encoding`、`diag_batch_assembly`、`diag_after_reward`），完整说明见源码。
