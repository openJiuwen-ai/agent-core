# openjiuwen.agent_evolving.agent_rl

`openjiuwen.agent_evolving.agent_rl` 是 openJiuwen 中的**强化学习（RL）训练扩展模块**，负责：

- 提供 RL 训练所需的数据结构和配置 schema；
- 实现基于 verl 的训练执行器；
- 提供主训练循环协调器；
- 实现多轮 rollout 编排；
- 提供并行运行时执行器；
- 提供高级用户入口 `OfflineRLOptimizer`。

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| [RLConfig](./config.md) | 顶级 RL 配置。 |
| [TrainingConfig](./config.md) | 训练配置，覆盖数据、模型、算法和 Verl 训练器参数。 |
| [RolloutConfig](./config.md) | Rollout / Actor 优化器配置。 |
| [AgentRuntimeConfig](./config.md) | Agent 运行时超参数。 |
| [PersistenceConfig](./config.md) | Rollout 持久化配置。 |
| [AdaConfig](./config.md) | Ada rollout 变体的额外参数。 |
| [Rollout](./offline/coordinator.md) | 单轮对话 rollout。 |
| [RolloutMessage](./offline/coordinator.md) | 完整的任务执行结果。 |
| [RLTask](./offline/coordinator.md) | 最小训练任务单元。 |
| [RolloutWithReward](./offline/coordinator.md) | 标准 MDP 数据单元。 |
| [TaskQueue](./offline/coordinator.md) | 异步任务队列 + Rollout 结果缓冲区。 |
| [OfflineRLOptimizer](./optimizer.md) | 顶级 RL 训练入口点。 |
| [TaskRunner](./optimizer.md) | Ray 远程 Actor，协调训练初始化与执行。 |
| [MainTrainer](./rl_trainer.md) | 训练循环协调器（DataLoader、`BackendProxy`、`TrainingCoordinator`、验证与 `fit`）。 |
| [VerlTrainingExecutor](./rl_trainer.md) | 继承 verl `RayPPOTrainer` 的训练执行器（PPO/GRPO 步与 rollout 睡/醒）。 |
| [RewardRegistry](./reward.md) | 奖励函数注册表。 |
| [RolloutPersistence](./offline/store.md) | Rollout 持久化抽象接口。 |
| [FileRolloutStore](./offline/store.md) | 基于文件的 Rollout 持久化实现。 |
| [NullRolloutStore](./offline/store.md) | 无操作 Rollout 持久化实现。 |
| [RLMetricsTracker](./offline/store.md) | RL 训练结构化指标跟踪器（`metrics_tracker`）。 |
| [TrainingStepMetrics](./offline/store.md) | 单个训练步指标数据类。 |
| [TrainingDiagnostics](./offline/store.md) | 开发模式下的训练管线分阶段诊断（`metrics_tracker`）。 |
| [BackendProxy](./proxy.md) | 反向代理，提供稳定的后端推理 URL。 |
| [RLRail](./offline/runtime.md) | 继承 `EvolutionRail` 的 RL 轨迹收集 Rail。 |
| [RuntimeExecutor](./offline/runtime.md) | 自包含的单任务执行器。 |
| [ParallelRuntimeExecutor](./offline/runtime.md) | 并行 rollout 执行引擎。 |
| [AgentFactory](./offline/runtime.md) | 为每个 RL 任务创建 **DeepAgent** 的可调用工厂。 |

**Online RL 模块**（`agent_rl.online`）：

| CLASS / FUNCTION | DESCRIPTION |
|------------------|-------------|
| [GatewayConfig](./online/gateway.md) | Online-RL Gateway 运行时配置 dataclass。 |
| [build_app_from_config](./online/gateway.md) | 从 `GatewayConfig` 装配 FastAPI 应用的生产入口。 |
| [build_gateway_app](./online/gateway.md) | FastAPI 装配，注册路由（`/health`、`/v1/gateway/stats`、`/v1/gateway/upload/batch`、`/v1/chat/completions`、全量代理）。 |
| [InferenceNotifier](./online/inference.md) | vLLM LoRA 热加载通知器。 |
| [JudgeScorer](./online/judge.md) | LLM-as-a-Judge 高层异步评分客户端。 |
| [evaluate_judge_scores](./online/judge.md) | 核心评分入口，多投票平均并归一化到 `[-1, 1]`。 |
| [LauncherPaths](./online/launcher.md) | 在线 RL 循环编排的路径布局 dataclass。 |
| [run_online_rl_loop](./online/launcher.md) | 顶层编排入口，拉起并监管各服务进程。 |
| [RLOnlineRail](./online/rail.md) | 钩入智能体生命周期的在线 RL 轨迹采集 Rail。 |
| [TrajectoryUploader](./online/rail.md) | 异步上传 rail-v1 批次到 gateway 的上传器。 |
| [OnlineTrainingScheduler](./online/scheduler.md) | 后台线程轮询 Redis 并触发 PPO 训练批次的调度器。 |
| [PPOTrainingExecutor](./online/scheduler.md) | Ray/verl PPO runner 生命周期与批次执行器。 |

**Functions**：

| FUNCTION | DESCRIPTION |
|----------|-------------|
| [run_agent_and_collect_trajectory](./offline/runtime.md) | 使用临时 `RLRail` 运行一次 Agent，并返回 canonical trajectory。 |
| [build_agent_factory](./offline/runtime.md) | 从运行时配置和工具构建默认 AgentFactory。 |
| [register_reward](./reward.md) | 用于按名称注册奖励函数的装饰器。 |
| [get_ppo_ray_runtime_env](./optimizer.md) | 返回 PPO/GRPO Ray Worker 的默认运行时环境配置。 |
