# openjiuwen.agent_evolving.trainer

`openjiuwen.agent_evolving.trainer` 提供自演进训练编排：评估 → 前向 → 更新器生成更新 → 写回算子 → 再评估，并支持检查点与恢复。

---

## class openjiuwen.agent_evolving.trainer.trainer.Trainer

编排「评估 → 生成更新 → 写回」的自进化循环，依赖 `Updater` 与 `BaseEvaluator`，可选检查点与恢复。

```text
class Trainer(
    *,
    updater: Updater,
    evaluator: BaseEvaluator,
    extractor: Optional[TracerTrajectoryExtractor] = None,
    callbacks: Optional[Callbacks] = None,
    num_parallel: int = TuneConstant.default_parallel_num,
    early_stop_score: float = TuneConstant.default_early_stop_score,
    checkpoint_dir: Optional[str] = None,
    resume_from: Optional[str] = None,
    checkpoint_every_n_epochs: int = 1,
    checkpoint_on_improve: bool = True,
    checkpoint_manager: Any = None,
)
```

**参数**：

* **updater**(Updater)：根据轨迹与评估结果生成参数更新。
* **evaluator**(BaseEvaluator)：对模型输出与期望答案打分。
* **extractor**(TracerTrajectoryExtractor，可选)：从 Session 抽取轨迹；默认 `TracerTrajectoryExtractor()`。
* **callbacks**(Callbacks，可选)：训练生命周期钩子。
* **num_parallel**(int，可选)：推理与评估的并行数。默认值：`TuneConstant.default_parallel_num`。
* **early_stop_score**(float，可选)：验证分数达到该值即停止。默认值：`TuneConstant.default_early_stop_score`。
* **checkpoint_dir**(str，可选)：检查点目录；为 `None` 则关闭检查点。
* **resume_from**(str，可选)：从中恢复的检查点路径。
* **checkpoint_every_n_epochs**(int，可选)：每 N 轮保存一次。默认值：`1`。
* **checkpoint_on_improve**(bool，可选)：验证提升时也保存。默认值：`True`。
* **checkpoint_manager**(Any，可选)：自定义检查点管理器；默认使用 `DefaultCheckpointManager`。

### set_callbacks(callbacks: Callbacks) -> None

设置训练生命周期回调（如进度打印、指标上报）。

### train(agent, train_cases=None, val_cases=None, num_iterations=TuneConstant.default_iteration_num, **kwargs) -> BaseAgent

执行自演进训练：先做验证基线评估，再多轮「训练前向 → 更新器更新 → 验证评估 → 检查点」。通过 `agent.get_operators()` 获取可优化算子并应用更新。

**参数**：

* **agent**(BaseAgent)：被优化的智能体，需实现 `get_operators()` 且支持带 session 的 invoke。
* **train_cases**(CaseLoader，可选)：训练集；黑盒优化器可不依赖此前向数据。
* **val_cases**(CaseLoader，可选)：验证集；未提供时使用 train_cases。
* **num_iterations**(int，可选)：最大训练轮数。
* **kwargs**：透传给 updater.update 的 config。

**返回**：

**BaseAgent**，训练后的智能体（内部参数已被 updater 更新）。

### forward(agent, cases) -> Tuple[float, List[EvaluatedCase], List[Trajectory], List[Any]]

单轮前向：对 cases 推理 → 评估 → 从各 Session 抽取轨迹。

**返回**：

**(平均分数, EvaluatedCase 列表, 轨迹列表, Session 列表)**。

### evaluate(agent, cases) -> Tuple[float, List[EvaluatedCase]]

仅推理与评估，不抽轨迹；返回平均分数与评估结果列表。

### predict_only(agent, cases) -> List[Dict]

仅推理，返回每条的模型输出列表，不返回 Session。

### predict(agent, cases) -> Tuple[List[Dict], List[Any]]

对每条 case 调用 agent.invoke（带 Session），并行度由 num_parallel 控制。

**返回**：

**(模型输出列表, Session 列表)**；Session 用于后续轨迹抽取。

### staticmethod apply_updates(operators: Dict[str, Operator], updates: Updates) -> None

将更新器给出的 updates 应用到算子字典；对 SingleDimUpdater 直接写回时 updates 可能为空，本方法会跳过。

---

## class openjiuwen.agent_evolving.trainer.progress.Progress

训练进度（当前/最大轮数、最佳分数、当前轮分数等），并提供按轮/按 batch 的迭代。

* **start_epoch**(int)：起始轮（恢复时用）。默认值：`0`。
* **current_epoch**(int)：当前轮。默认值：`0`。
* **max_epoch**(int)：最大轮数。默认值：`TuneConstant.default_iteration_num`。
* **current_batch_iter**(int)：当前 batch 步。默认值：`0`。
* **max_batch_iter**(int)：每轮 batch 步数。默认值：`1`。
* **best_score**(float)：历史最佳验证分数，取值范围 [0, 1]。默认值：`0.0`。
* **best_batch_score**(float)：当前轮内最佳 batch 分数。默认值：`0.0`。
* **current_epoch_score**(float)：当前轮验证分数。默认值：`0.0`。

### run_epoch() -> Generator[int, None, None]

迭代 1..max_epoch，每轮更新 current_epoch 并 yield 轮号。

### run_batch() -> Generator[int, None, None]

迭代 batch 步，更新 current_batch_iter；开始时会将 best_batch_score 置 0。

---

## class openjiuwen.agent_evolving.trainer.progress.Callbacks

训练生命周期钩子；子类可重写以接入日志、早停、指标上报等。

### on_train_begin(agent, progress, eval_info) -> None

训练开始（验证基线评估已完成）时调用。

### on_train_end(agent, progress, eval_info) -> None

训练结束时调用。

### on_train_epoch_begin(agent, progress) -> None

单轮训练开始时调用。

### on_train_epoch_end(agent, progress, eval_info) -> None

单轮训练结束时调用（best_score 已更新/参数已写回）。
