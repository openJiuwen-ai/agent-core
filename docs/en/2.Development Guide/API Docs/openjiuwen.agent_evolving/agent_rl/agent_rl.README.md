# openjiuwen.agent_evolving.agent_rl

`openjiuwen.agent_evolving.agent_rl` is the **reinforcement learning (RL) training extension module** in openJiuwen, responsible for:

- Providing data structures and configuration schemas for RL training;
- Implementing the training executor based on verl;
- Providing the main training loop coordinator;
- Implementing multi-turn rollout orchestration;
- Providing a parallel runtime executor;
- Providing the high-level user entry point `OfflineRLOptimizer`.

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| [RLConfig](./config.md) | Top-level RL configuration. |
| [TrainingConfig](./config.md) | Training configuration covering data, model, algorithm, and Verl trainer parameters. |
| [RolloutConfig](./config.md) | Rollout / Actor optimizer configuration. |
| [AgentRuntimeConfig](./config.md) | Agent runtime hyperparameters. |
| [PersistenceConfig](./config.md) | Rollout persistence configuration. |
| [AdaConfig](./config.md) | Additional parameters for Ada rollout variant. |
| [Rollout](./offline/coordinator.md) | Single-turn dialogue rollout. |
| [RolloutMessage](./offline/coordinator.md) | Complete task execution result. |
| [RLTask](./offline/coordinator.md) | Minimal training task unit. |
| [RolloutWithReward](./offline/coordinator.md) | Standard MDP data unit. |
| [TaskQueue](./offline/coordinator.md) | Async task queue + Rollout result buffer. |
| [OfflineRLOptimizer](./optimizer.md) | Top-level RL training entry point. |
| [TaskRunner](./optimizer.md) | Ray remote Actor that coordinates training initialization and execution. |
| [MainTrainer](./rl_trainer.md) | Training loop coordinator (`DataLoader`, `BackendProxy`, `TrainingCoordinator`, validation, and `fit`). |
| [VerlTrainingExecutor](./rl_trainer.md) | Training executor subclassing verl's `RayPPOTrainer` (PPO/GRPO steps; rollout sleep/wake). |
| [RewardRegistry](./reward.md) | Reward function registry. |
| [RolloutPersistence](./offline/store.md) | Rollout persistence abstract interface. |
| [FileRolloutStore](./offline/store.md) | File-based Rollout persistence implementation. |
| [NullRolloutStore](./offline/store.md) | No-op Rollout persistence implementation. |
| [RLMetricsTracker](./offline/store.md) | Structured RL metrics tracker (`metrics_tracker`). |
| [TrainingStepMetrics](./offline/store.md) | Single training step metrics dataclass. |
| [TrainingDiagnostics](./offline/store.md) | Stage-wise training pipeline diagnostics in develop mode (`metrics_tracker`). |
| [BackendProxy](./proxy.md) | Reverse proxy providing a stable backend inference URL. |
| [RLRail](./offline/runtime.md) | `EvolutionRail` subclass for RL trajectory collection. |
| [RuntimeExecutor](./offline/runtime.md) | Self-contained single-task executor. |
| [ParallelRuntimeExecutor](./offline/runtime.md) | Parallel rollout execution engine. |
| [AgentFactory](./offline/runtime.md) | Factory creating a **DeepAgent** per RL task (see `offline/runtime.md`). |

**Online RL Module** (`agent_rl.online`):

| CLASS / FUNCTION | DESCRIPTION |
|------------------|-------------|
| `RLServiceConfig` | Static loopback RL Service configuration. |
| `build_rl_service_app` | RL Service HTTP assembly for Tasks, captures, trajectories, and Training Runs. |
| `TaskRegistry` | Redis-backed owner of Task, turn, capture, and reward state. |
| `CapturePipeline` | Complete OpenAI request/response validation and rewarded trajectory publication. |
| [JudgeScorer](./online/judge.md) | High-level async LLM-as-a-Judge scoring client. |
| [evaluate_judge_scores](./online/judge.md) | Core scoring entry; multi-vote averaging normalized to `[0, 1]`. |
| [RLOnlineRail](./online/rail.md) | Online RL trajectory collection rail hooking into the agent lifecycle. |
| [TrajectoryUploader](./online/rail.md) | Async uploader of rail-v1 batches to the gateway. |
| `TrainingRunner` | Explicit fixed-batch PPO, LoRA activation, cancellation, and recovery lifecycle. |
| `PPOTrainingExecutor` | Ray/verl PPO adapter used by an explicit Training Run. |

**Functions**:

| FUNCTION | DESCRIPTION |
|----------|-------------|
| [run_agent_and_collect_trajectory](./offline/runtime.md) | Run one Agent with a temporary `RLRail` and return its canonical trajectory. |
| [build_agent_factory](./offline/runtime.md) | Build default AgentFactory from runtime config and tools. |
| [register_reward](./reward.md) | Decorator for registering reward functions by name. |
| [get_ppo_ray_runtime_env](./optimizer.md) | Returns default runtime env config for PPO/GRPO Ray workers. |
