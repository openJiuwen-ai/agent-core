# openjiuwen.dev_tools.agentrl.rollout_store

## class openjiuwen.dev_tools.agentrl.rollout_store.base.RolloutPersistence

```
class openjiuwen.dev_tools.agentrl.rollout_store.base.RolloutPersistence(ABC)
```

Rollout 持久化的抽象接口。

### async save_rollout(self, step: int, task_id: str, rollout: RolloutMessage, *, phase: str = "train") -> None

持久化单个 rollout 及其完整轨迹。

**参数**：

* **step**(int)：当前训练步数。
* **task_id**(str)：任务标识符。
* **rollout**(RolloutMessage)：要持久化的 rollout 消息。
* **phase**(str，可选)：`"train"` 或 `"val"` — 决定输出子目录。默认值：`"train"`。

### async save_step_summary(self, step: int, metrics: Dict[str, Any]) -> None

持久化每步训练摘要指标。

**参数**：

* **step**(int)：步数。
* **metrics**(Dict[str, Any])：指标字典。

### async query_rollouts(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]

按过滤器查询历史 rollouts（用于分析/调试）。

**参数**：

* **filters**(Dict[str, Any])：过滤器字典。
* **limit**(int，可选)：返回结果数量限制。默认值：`100`。

**返回**：

**List[Dict[str, Any]]**，匹配的 rollout 文档列表。

### async close(self) -> None

释放连接并清理资源。

---

## class openjiuwen.dev_tools.agentrl.rollout_store.file_store.FileRolloutStore

```
class openjiuwen.dev_tools.agentrl.rollout_store.file_store.FileRolloutStore(save_path: str, flush_interval: int = 100)
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

---

## class openjiuwen.dev_tools.agentrl.rollout_store.null_store.NullRolloutStore

```
class openjiuwen.dev_tools.agentrl.rollout_store.null_store.NullRolloutStore()
```

当持久化禁用时的无操作实现。所有方法静默成功，不执行任何 I/O。

**样例**：

```python
>>> from openjiuwen.dev_tools.agentrl.rollout_store.base import RolloutPersistence
>>> from openjiuwen.dev_tools.agentrl.rollout_store.null_store import NullRolloutStore
>>> from openjiuwen.dev_tools.agentrl.rollout_store.file_store import FileRolloutStore
>>> 
>>> # 使用 NullRolloutStore（持久化禁用）
>>> store = NullRolloutStore()
>>> 
>>> # 使用 FileRolloutStore（持久化启用）
>>> store = FileRolloutStore(save_path="/path/to/save", flush_interval=100)
```
