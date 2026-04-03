# openjiuwen.dev_tools.agentrl

`openjiuwen.dev_tools.agentrl` 是 openJiuwen 中的**强化学习（RL）训练扩展模块**，负责：

- 提供 RL 训练所需的数据结构和配置 schema；
- 实现基于 verl 的训练执行器；
- 提供主训练循环协调器；
- 实现多轮 rollout 编排；
- 提供并行运行时执行器；
- 提供高级用户入口 `RLOptimizer`。

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| [RLConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemasrlconfig) | 顶级 RL 配置。 |
| [TrainingConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemastrainingconfig) | 训练配置，覆盖数据、模型、算法和 Verl 训练器参数。 |
| [RolloutConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemasrolloutconfig) | Rollout / Actor 优化器配置。 |
| [AgentRuntimeConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemasagentruntimeconfig) | Agent 运行时超参数。 |
| [PersistenceConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemaspersistenceconfig) | Rollout 持久化配置。 |
| [AdaConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemasadaconfig) | Ada rollout 变体的额外参数。 |
| [Rollout](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatorschemasrollout) | 单轮对话 rollout。 |
| [RolloutMessage](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatorschemasrolloutmessage) | 完整的任务执行结果。 |
| [RLTask](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatorschemasrltask) | 最小训练任务单元。 |
| [RolloutWithReward](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatorschemasrolloutwithreward) | 标准 MDP 数据单元。 |
| [TaskQueue](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatortask_queuetaskqueue) | 异步任务队列 + Rollout 结果缓冲区。 |
| [RLOptimizer](./agentrl/optimizer.md#class-openjiuwendev_toolsagentrloptimizerrl_optimizerrloptimizer) | 顶级 RL 训练入口点。 |
| [TaskRunner](./agentrl/optimizer.md#class-openjiuwendev_toolsagentrloptimizertask_runnertaskrunner) | Ray 远程 Actor，协调训练初始化与执行。 |
| [MainTrainer](./agentrl/rl_trainer.md#class-openjiuwendev_toolsagentrlrl_trainermain_trainermaintrainer) | 训练循环协调器（DataLoader、`BackendProxy`、`TrainingCoordinator`、验证与 `fit`）。 |
| [VerlTrainingExecutor](./agentrl/rl_trainer.md#class-openjiuwendev_toolsagentrlrl_trainerverl_executorverltrainingexecutor) | 继承 verl `RayPPOTrainer` 的训练执行器（PPO/GRPO 步与 rollout 睡/醒）。 |
| [RewardRegistry](./agentrl/reward.md#class-openjiuwendev_toolsagentrlrewardregistryrewardregistry) | 奖励函数注册表。 |
| [RolloutPersistence](./agentrl/rollout_store.md#class-openjiuwendev_toolsagentrlrollout_storebaserolloutpersistence) | Rollout 持久化抽象接口。 |
| [FileRolloutStore](./agentrl/rollout_store.md#class-openjiuwendev_toolsagentrlrollout_storefile_storefilerolloutstore) | 基于文件的 Rollout 持久化实现。 |
| [NullRolloutStore](./agentrl/rollout_store.md#class-openjiuwendev_toolsagentrlrollout_storenull_storenullrolloutstore) | 无操作 Rollout 持久化实现。 |
| [RLMetricsTracker](./agentrl/monitoring.md#class-openjiuwendev_toolsagentrlmonitoringmetrics_tracker-rlmetricstracker) | RL 训练结构化指标跟踪器。 |
| [TrainingStepMetrics](./agentrl/monitoring.md#class-openjiuwendev_toolsagentrlmonitoringmetrics_trackertrainingstepmetrics) | 单个训练步指标数据类。 |
| [BackendProxy](./agentrl/proxy.md#class-openjiuwendev_toolsagentrlproxybackend_proxybackendproxy) | 反向代理，提供稳定的后端推理 URL。 |
| [TrajectoryCollectionRail](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimetrajectorytrajectorycollectionrail) | 基于 AgentRail 的轨迹收集器。 |
| [TrajectoryCollector](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimetrajectorytrajectorycollector) | Agent 轨迹收集封装器。 |
| [RuntimeExecutor](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimeruntime_executorruntimeexecutor) | 自包含的单任务执行器。 |
| [ParallelRuntimeExecutor](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimeparallel_executorparallelruntimeexecutor) | 并行 rollout 执行引擎。 |
| [AgentFactory](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimeagent_factoryagentfactory) | 为每个 RL 任务创建 ReActAgent 实例的工厂。 |

**Functions**：

| FUNCTION | DESCRIPTION |
|----------|-------------|
| [build_agent_factory](./agentrl/agent_runtime.md#func-openjiuwendev_toolsagentrlagent_runtimeagent_factorybuild_agent_factory) | 从运行时配置和工具构建默认 AgentFactory。 |
| [register_reward](./agentrl/reward.md#func-openjiuwendev_toolsagentrlrewardregistryregister_reward) | 用于按名称注册奖励函数的装饰器。 |
| [get_ppo_ray_runtime_env](./agentrl/optimizer.md#func-openjiuwendev_toolsagentrloptimizertask_runnerget_ppo_ray_runtime_env) | 返回 PPO/GRPO Ray Worker 的默认运行时环境配置。 |
