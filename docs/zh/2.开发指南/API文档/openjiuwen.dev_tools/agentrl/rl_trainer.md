# openjiuwen.dev_tools.agentrl.rl_trainer

`openjiuwen.dev_tools.agentrl.rl_trainer` 提供 **verl 集成下的训练执行与主循环编排**。类型由 Ray `TaskRunner` / 远程训练路径构造；大多数用户只需通过 [`RLOptimizer`](./optimizer.md) 使用，无需直接实例化本模块中的类。

---

## class openjiuwen.dev_tools.agentrl.rl_trainer.verl_executor.VerlTrainingExecutor

```
class openjiuwen.dev_tools.agentrl.rl_trainer.verl_executor.VerlTrainingExecutor(RayPPOTrainer)
```

直接继承 verl 的 `RayPPOTrainer`，实现 PPO/GRPO 训练步（含优势估计、奖励、actor/critic 更新、指标与 checkpoint 等）。单步逻辑与 `ppo_step.run_ppo_step` 协同（见源码 `rl_trainer/ppo_step.py`）。

**职责概要**：

* `sleep_rollout` / `wake_up_rollout`：与异步 rollout 管理器协同，在训练步之间切换 vLLM 后端地址。
* `setup_logger` / `log_metrics` / `save_checkpoint` / `load_checkpoint`：实验日志与检查点。
* 内部使用 `TrainingDiagnostics`（`monitoring/training_logger.py`）等辅助做序列级诊断。

**说明**：由 verl/Ray 侧在分布式环境中构造；不与 `RLOptimizer` 的公开 API 混用。

---

## class openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.MainTrainer

```
class openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.MainTrainer(rl_trainer, config: DictConfig, collate_fn=None, train_sampler=None, *, task_runner=None, agent_factory=None, task_data_fn=None, reward_fn=None, metrics_tracker=None, persistence=None)
```

训练循环**协调器**：将 `VerlTrainingExecutor`（参数中的 `rl_trainer`）、`TrainingCoordinator`（`coordinator/training_coordinator.py`）侧 rollout、`BackendProxy`、训练/验证 DataLoader 串成完整 RL 训练周期。

**要点**：

* 内部创建 [`BackendProxy`](./proxy.md)；启动后将 **`proxy_url`** 赋给可写的 `agent_factory.proxy_url`（见 [`AgentFactory`](./agent_runtime.md)），以便 ReActAgent 通过稳定 URL 访问 vLLM。
* **`update_backends(servers)`**：将当前步的 vLLM 地址同步到代理。
* **`validate()`**：验证集上跑一轮（要求验证 DataLoader 恰好一个 batch，见源码校验）。
* **`fit()`**：主训练循环（epoch、进度条、步级训练与可选验证）。

常规用法下由远程 `TaskRunner` 持有并驱动；进阶扩展训练管线时可阅读源码构造方式。
