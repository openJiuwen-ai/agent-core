# openjiuwen.dev_tools.agentrl

`openjiuwen.dev_tools.agentrl` is the **reinforcement learning (RL) training extension module** in openJiuwen, responsible for:

- Providing data structures and configuration schemas for RL training;
- Implementing the training executor based on verl;
- Providing the main training loop coordinator;
- Implementing multi-turn rollout orchestration;
- Providing a parallel runtime executor;
- Providing the high-level user entry point `RLOptimizer`.

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| [RLConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemasrlconfig) | Top-level RL configuration. |
| [TrainingConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemastrainingconfig) | Training configuration covering data, model, algorithm, and Verl trainer parameters. |
| [RolloutConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemasrolloutconfig) | Rollout / Actor optimizer configuration. |
| [AgentRuntimeConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemasagentruntimeconfig) | Agent runtime hyperparameters. |
| [PersistenceConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemaspersistenceconfig) | Rollout persistence configuration. |
| [AdaConfig](./agentrl/config.md#class-openjiuwendev_toolsagentrlconfigschemasadaconfig) | Additional parameters for Ada rollout variant. |
| [Rollout](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatorschemasrollout) | Single-turn dialogue rollout. |
| [RolloutMessage](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatorschemasrolloutmessage) | Complete task execution result. |
| [RLTask](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatorschemasrltask) | Minimal training task unit. |
| [RolloutWithReward](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatorschemasrolloutwithreward) | Standard MDP data unit. |
| [TaskQueue](./agentrl/coordinator.md#class-openjiuwendev_toolsagentrlcoordinatortask_queuetaskqueue) | Async task queue + Rollout result buffer. |
| [RLOptimizer](./agentrl/optimizer.md#class-openjiuwendev_toolsagentrloptimizerrl_optimizerrloptimizer) | Top-level RL training entry point. |
| [TaskRunner](./agentrl/optimizer.md#class-openjiuwendev_toolsagentrloptimizertask_runnertaskrunner) | Ray remote Actor that coordinates training initialization and execution. |
| [MainTrainer](./agentrl/rl_trainer.md#class-openjiuwendev_toolsagentrlrl_trainermain_trainermaintrainer) | Training loop coordinator (`DataLoader`, `BackendProxy`, `TrainingCoordinator`, validation, and `fit`). |
| [VerlTrainingExecutor](./agentrl/rl_trainer.md#class-openjiuwendev_toolsagentrlrl_trainerverl_executorverltrainingexecutor) | Training executor subclassing verl's `RayPPOTrainer` (PPO/GRPO steps; rollout sleep/wake). |
| [RewardRegistry](./agentrl/reward.md#class-openjiuwendev_toolsagentrlrewardregistryrewardregistry) | Reward function registry. |
| [RolloutPersistence](./agentrl/rollout_store.md#class-openjiuwendev_toolsagentrlrollout_storebaserolloutpersistence) | Rollout persistence abstract interface. |
| [FileRolloutStore](./agentrl/rollout_store.md#class-openjiuwendev_toolsagentrlrollout_storefile_storefilerolloutstore) | File-based Rollout persistence implementation. |
| [NullRolloutStore](./agentrl/rollout_store.md#class-openjiuwendev_toolsagentrlrollout_storenull_storenullrolloutstore) | No-op Rollout persistence implementation. |
| [RLMetricsTracker](./agentrl/monitoring.md#class-openjiuwendev_toolsagentrlmonitoringmetrics_tracker-rlmetricstracker) | RL training structured metrics tracker. |
| [TrainingStepMetrics](./agentrl/monitoring.md#class-openjiuwendev_toolsagentrlmonitoringmetrics_trackertrainingstepmetrics) | Single training step metrics dataclass. |
| [BackendProxy](./agentrl/proxy.md#class-openjiuwendev_toolsagentrlproxybackend_proxybackendproxy) | Reverse proxy providing a stable backend inference URL. |
| [TrajectoryCollectionRail](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimetrajectorytrajectorycollectionrail) | AgentRail-based trajectory collector. |
| [TrajectoryCollector](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimetrajectorytrajectorycollector) | Agent trajectory collection wrapper. |
| [RuntimeExecutor](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimeruntime_executorruntimeexecutor) | Self-contained single-task executor. |
| [ParallelRuntimeExecutor](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimeparallel_executorparallelruntimeexecutor) | Parallel rollout execution engine. |
| [AgentFactory](./agentrl/agent_runtime.md#class-openjiuwendev_toolsagentrlagent_runtimeagent_factoryagentfactory) | Factory for creating ReActAgent instances for each RL task. |

**Functions**:

| FUNCTION | DESCRIPTION |
|----------|-------------|
| [build_agent_factory](./agentrl/agent_runtime.md#func-openjiuwendev_toolsagentrlagent_runtimeagent_factorybuild_agent_factory) | Build default AgentFactory from runtime config and tools. |
| [register_reward](./agentrl/reward.md#func-openjiuwendev_toolsagentrlrewardregistryregister_reward) | Decorator for registering reward functions by name. |
| [get_ppo_ray_runtime_env](./agentrl/optimizer.md#func-openjiuwendev_toolsagentrloptimizertask_runnerget_ppo_ray_runtime_env) | Returns default runtime env config for PPO/GRPO Ray workers. |
