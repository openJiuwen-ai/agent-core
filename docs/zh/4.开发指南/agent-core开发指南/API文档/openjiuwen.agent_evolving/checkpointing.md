# openjiuwen.agent_evolving.checkpointing

`openjiuwen.agent_evolving.checkpointing` 提供自演进训练的检查点数据结构、本地文件存储与默认的保存/恢复策略；以 Operator.get_state/load_state 为核心，不绑定具体优化器实现。

---

## class openjiuwen.agent_evolving.checkpointing.types.EvolveCheckpoint

单次训练检查点数据结构（用于恢复与审计）。

* **version**(str)：检查点版本标识。
* **run_id**(str)：本次运行 ID。
* **step**(Dict[str, int])：步信息（如 epoch、batch、global_step）。
* **best**(Dict[str, Any])：最佳信息（如 best_score）。
* **seed**(int，可选)：随机种子。
* **operators_state**(Dict[str, Dict[str, Any]])：算子 ID 到状态的映射，用于恢复各 Operator。
* **producer_state**(Dict[str, Any])：更新生产者内部状态（可为空）。
* **searcher_state**(Dict[str, Any])：参数搜索器状态（可为空）。
* **last_metrics**(Dict[str, Any])：最近轮次汇总指标（可为空）。

---

## class openjiuwen.agent_evolving.checkpointing.store_file.FileCheckpointStore

基于本地 JSON 文件的检查点存储，不依赖 core 的 checkpointer，便于调试与审计。

```text
class FileCheckpointStore(base_dir: str)
```

**参数**：

* **base_dir**(str)：检查点根目录；若不存在会自动创建。

### save_checkpoint(ckpt: EvolveCheckpoint, filename='latest.json') -> Optional[str]

将检查点序列化为 JSON 写入 base_dir 下指定文件名。

**返回**：

**str | None**，写入的完整路径；base_dir 为 None 时返回 None。

### load_checkpoint(path: str) -> Optional[EvolveCheckpoint]

从指定路径加载检查点。

**返回**：

**EvolveCheckpoint | None**，文件不存在或 base_dir 为 None 时返回 None。

### load_state_dict(path: str) -> Optional[Dict[str, Dict[str, Any]]]

从检查点 JSON 中仅读取 `operators_state`，用于推理侧加载算子状态（如 `op.load_state(state[operator_id])`）。

**返回**：

**Dict[str, Dict[str, Any]] | None**，operator_id 到 state 的映射；无 `operators_state` 或文件不存在时返回 None。

---

## class openjiuwen.agent_evolving.checkpointing.manager.CheckpointManager

检查点管理协议：决定何时保存、如何构建检查点、如何恢复。

### should_save(*, epoch: int, improved: bool) -> bool

根据当前轮次与是否提升决定是否保存。

### build_checkpoint(*, agent, progress, producer_state=None) -> EvolveCheckpoint

构建当前检查点（含算子状态、进度、producer 状态等）。

### restore(*, agent, checkpoint: EvolveCheckpoint) -> Dict[str, Any]

从检查点恢复 agent 的算子状态，并返回可供 Trainer 恢复 progress 的字典（如 start_epoch、best_score）。

---

## class openjiuwen.agent_evolving.checkpointing.manager.DefaultCheckpointManager

默认检查点管理器：在「验证提升」或「每 N 轮」时保存；恢复时恢复 operators_state 与 progress 的 best/epoch。

```text
class DefaultCheckpointManager(
    *,
    run_id: Optional[str] = None,
    checkpoint_version: str = "v1",
    save_every_n_epochs: int = 1,
    save_on_improve: bool = True,
)
```

**参数**：

* **run_id**(str，可选)：运行 ID；未传则用 uuid。
* **checkpoint_version**(str，可选)：检查点版本标识。默认值：`"v1"`。
* **save_every_n_epochs**(int，可选)：每 N 轮保存一次，至少为 1。默认值：`1`。
* **save_on_improve**(bool，可选)：验证提升时也保存。默认值：`True`。

### run_id -> str

当前 run_id。

### should_save(*, epoch, improved) -> bool

提升时或 epoch 为 save_every_n_epochs 的倍数时返回 True。

### build_checkpoint(*, agent, progress, producer_state=None) -> EvolveCheckpoint

对 agent 调用 get_operators()，快照各算子 get_state()，与 progress、producer_state 等一起组装为 EvolveCheckpoint。

### restore(*, agent, checkpoint: EvolveCheckpoint) -> Dict[str, Any]

将 checkpoint.operators_state 写回各算子，并返回 `{"start_epoch", "best_score", "run_id"}` 供 Trainer 恢复 progress。
