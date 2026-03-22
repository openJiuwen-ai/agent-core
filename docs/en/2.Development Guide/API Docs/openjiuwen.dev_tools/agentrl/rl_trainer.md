# openjiuwen.dev_tools.agentrl.rl_trainer

`openjiuwen.dev_tools.agentrl.rl_trainer` provides **Verl-integrated training execution and main-loop orchestration**. These types are constructed on the Ray `TaskRunner` / remote training path; most users should use [`RLOptimizer`](./optimizer.md) and do not need to instantiate classes in this module directly.

---

## class openjiuwen.dev_tools.agentrl.rl_trainer.verl_executor.VerlTrainingExecutor

```
class openjiuwen.dev_tools.agentrl.rl_trainer.verl_executor.VerlTrainingExecutor(RayPPOTrainer)
```

Extends verl's `RayPPOTrainer` to run the PPO/GRPO training step (advantages, rewards, actor/critic updates, metrics, checkpoints, etc.). The step pipeline coordinates with `ppo_step.run_ppo_step` (see `rl_trainer/ppo_step.py` in the source tree).

**Responsibilities (summary)**:

* `sleep_rollout` / `wake_up_rollout`: coordinate with the async rollout manager and rotate backend vLLM addresses between steps.
* `setup_logger` / `log_metrics` / `save_checkpoint` / `load_checkpoint`: experiment logging and checkpoints.
* Uses helpers such as `TrainingDiagnostics` (`monitoring/training_logger.py`) for sequence-level diagnostics.

**Note**: Built by the verl/Ray stack inside distributed workers; not part of the public `RLOptimizer` API surface.

---

## class openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.MainTrainer

```
class openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.MainTrainer(rl_trainer, config: DictConfig, collate_fn=None, train_sampler=None, *, task_runner=None, agent_factory=None, task_data_fn=None, reward_fn=None, metrics_tracker=None, persistence=None)
```

**Training loop coordinator**: wires `VerlTrainingExecutor` (the `rl_trainer` argument), `TrainingCoordinator` (`coordinator/training_coordinator.py`) for rollouts, `BackendProxy`, and train/validation DataLoaders into a full RL training cycle.

**Highlights**:

* Creates a [`BackendProxy`](./proxy.md) internally; after start, assigns **`proxy_url`** to a writable `agent_factory.proxy_url` (see [`AgentFactory`](./agent_runtime.md)) so `ReActAgent` talks to vLLM through a stable URL.
* **`update_backends(servers)`**: pushes the current step's vLLM addresses into the proxy.
* **`validate()`**: runs one validation pass (expects exactly one validation batch; see source guardrails).
* **`fit()`**: main training loop (epochs, progress bar, per-step training and optional validation).

Normally owned and driven by the remote `TaskRunner`; read the source when extending the training pipeline.
