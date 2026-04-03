# openjiuwen.dev_tools.agentrl.rl_trainer

`openjiuwen.dev_tools.agentrl.rl_trainer` provides **Verl-integrated training execution and main-loop orchestration**. Types in this module are normally created at runtime on the Ray remote path (for example by `TaskRunner`), rather than being constructed directly in application code after a plain `import`.

## class openjiuwen.dev_tools.agentrl.rl_trainer.verl_executor.VerlTrainingExecutor

```python
class openjiuwen.dev_tools.agentrl.rl_trainer.verl_executor.VerlTrainingExecutor(RayPPOTrainer)
```

Subclasses verl's `RayPPOTrainer` and implements one PPO/GRPO training step (advantages, rewards, actor/critic updates, metrics, checkpoints, etc.). The per-step implementation is `ppo_step.run_ppo_step` (see `rl_trainer/ppo_step.py`).

**Responsibilities (summary)**:

* `sleep_rollout` / `wake_up_rollout`: work with the async rollout manager to rotate backend vLLM addresses between steps.
* `setup_logger` / `log_metrics` / `save_checkpoint` / `load_checkpoint`: experiment logging and checkpoints.
* Uses helpers such as `TrainingDiagnostics` (`monitoring/training_logger.py`) for diagnostics aggregated at sequence granularity.

**Note**: Instances are wired inside verl/Ray distributed workers; this class is not exposed to end users as a direct `RLOptimizer` entry point.

## class openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.MainTrainer

```python
class openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.MainTrainer(rl_trainer, config: DictConfig, collate_fn=None, train_sampler=None, *, task_runner=None, agent_factory=None, task_data_fn=None, reward_fn=None, metrics_tracker=None, persistence=None)
```

**Training loop coordinator**: combines `VerlTrainingExecutor` (the `rl_trainer` argument), `TrainingCoordinator` from `coordinator/training_coordinator.py` (rollout and sample handling), [`BackendProxy`](./proxy.md), and train/validation `DataLoader`s into an end-to-end RL training flow.

**Highlights**:

* Creates a [`BackendProxy`](./proxy.md) internally; after the proxy starts, writes **`proxy_url`** into the mutable `agent_factory.proxy_url` (see [`AgentFactory`](./agent_runtime.md)) so `ReActAgent` uses a stable base URL for vLLM.
* **`update_backends(servers)`**: supplies the current step's vLLM backend addresses to the proxy.
* **`validate()`**: runs one forward pass on the validation set; the implementation has preconditions on validation `DataLoader` batching—see the validation logic in the source.
* **`fit()`**: main training loop (epochs, progress bar, per-step training and optional validation).

In the default integration, a remote `TaskRunner` owns `MainTrainer` and calls `fit()`. To replace components or customize the pipeline, refer to how `TaskRunner` constructs `MainTrainer`, injects dependencies, and sequences calls in the source.
