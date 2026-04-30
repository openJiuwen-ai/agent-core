# openjiuwen.agent_evolving.agent_rl.rl_trainer

`openjiuwen.agent_evolving.agent_rl.rl_trainer` 提供 **与 verl 集成的训练执行与主循环编排**。本模块中的类型一般在运行时由 Ray 远程路径（例如 `TaskRunner`）创建，而非在业务代码中直接 `import` 后手工实例化。

## class openjiuwen.agent_evolving.agent_rl.rl_trainer.verl_executor.VerlTrainingExecutor

```python
class openjiuwen.agent_evolving.agent_rl.rl_trainer.verl_executor.VerlTrainingExecutor(RayPPOTrainer)
```

继承 verl 的 `RayPPOTrainer`，负责单步 PPO/GRPO 训练（优势估计、奖励、actor/critic 更新、指标与 checkpoint 等）。单步计算入口为 `ppo_step.run_ppo_step`（见 `rl_trainer/ppo_step.py`）。

**职责概要**：

* `sleep_rollout` / `wake_up_rollout`：配合异步 rollout 管理器，在相邻训练步之间切换 vLLM 后端地址。
* `setup_logger` / `log_metrics` / `save_checkpoint` / `load_checkpoint`：实验日志与检查点。
* 借助 `TrainingDiagnostics`（`offline/store/metrics_tracker.py`）等组件在 `develop_mode` 下输出按阶段聚合的诊断日志。

**说明**：实例在 verl/Ray 分布式 Worker 内装配；本类不通过 `OfflineRLOptimizer` 作为面向使用者的直接调用入口对外暴露。

## class openjiuwen.agent_evolving.agent_rl.offline.main_trainer.MainTrainer

```python
class openjiuwen.agent_evolving.agent_rl.offline.main_trainer.MainTrainer(rl_trainer, config: DictConfig, collate_fn=None, train_sampler=None, *, task_runner=None, agent_factory=None, task_data_fn=None, reward_fn=None, metrics_tracker=None, persistence=None)
```

训练循环**协调器**：将 `VerlTrainingExecutor`（形参 `rl_trainer`）、`coordinator/training_coordinator.py` 中的 `TrainingCoordinator`（rollout 与样本组织）、[`BackendProxy`](./proxy.md) 以及训练/验证 `DataLoader` 组合为端到端 RL 训练流程。

**要点**：

* 内部创建 [`BackendProxy`](./proxy.md)；代理启动后把 **`proxy_url`** 写入可变的 `agent_factory.proxy_url`（见 [`AgentFactory`](./offline/runtime.md)），使 **`AgentFactory`** 产出的 rollout 智能体（**`DeepAgent`**）通过固定基 URL 访问 vLLM。
* **`update_backends(servers)`**：将当前训练步对应的 vLLM 地址列表下发给代理。
* **`validate()`**：在验证集上执行一轮前向；实现中对验证 `DataLoader` 的 batch 划分有前置假设，详见该类源码中的校验逻辑。
* **`fit()`**：主训练循环（epoch、进度条、按步训练与可选验证）。

在默认集成中，由远程 `TaskRunner` 持有 `MainTrainer` 并调用其 `fit()`。若需替换组件或改写训练管线，请参考源码中 `TaskRunner` 对 `MainTrainer` 的构造参数、依赖注入与调用顺序。
