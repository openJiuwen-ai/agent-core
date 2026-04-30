# openjiuwen.agent_evolving

`openjiuwen.agent_evolving` is the **self-evolving training and evaluation framework** in openJiuwen, responsible for:

- Orchestrating "evaluate -> generate updates -> writeback" self-evolution cycle, collaborating with [openjiuwen.core.operator](openjiuwen.core/operator.README.md) for iterative optimization of optimizable operators;
- Providing dataset abstractions (Case, EvaluatedCase, CaseLoader), training progress and callbacks (Trainer, Progress,Callbacks);
- Providing trajectory types and extraction (Trajectory, TracerTrajectoryExtractor), updaters (Updater, SingleDimUpdater, MultiDimUpdater);
- Providing checkpoint and restore capabilities (EvolveCheckpoint, FileCheckpointStore, DefaultCheckpointManager);
- Providing optimizer base classes and LLM instruction optimization implementations (BaseOptimizer, InstructionOptimizer);
- Evaluation capabilities are located in submodule `openjiuwen.agent_evolving.evaluator` (BaseEvaluator, DefaultEvaluator, MetricEvaluator, Metric, ExactMatchMetric, LLMAsJudgeMetric).

**Documentation Index**:

| Module | Description |
|--------|-------------|
| [constant](openjiuwen.agent_evolving/constant.md) | Hyperparameter defaults and value ranges (TuneConstant) |
| [dataset](openjiuwen.agent_evolving/dataset.md) | Samples and loading (Case, EvaluatedCase, CaseLoader, shuffle_cases, split_cases) |
| [trainer](openjiuwen.agent_evolving/trainer.md) | Training orchestration (Trainer, Progress,Callbacks) |
| [trajectory](openjiuwen.agent_evolving/trajectory.md) | Trajectory types and extraction (Trajectory, TracerTrajectoryExtractor, iter_steps, etc.) |
| [updater](openjiuwen.agent_evolving/updater.md) | Updaters (Updater, SingleDimUpdater, MultiDimUpdater) |
| [checkpointing](openjiuwen.agent_evolving/checkpointing.md) | Checkpoint and restore (EvolveCheckpoint, FileCheckpointStore, CheckpointManager) |
| [optimizer](openjiuwen.agent_evolving/optimizer/optimizer.md) | Optimizer base classes (BaseOptimizer, TextualParameter, InstructionOptimizer) |
| [team_skill_optimizer](openjiuwen.agent_evolving/optimizer/team_skill_optimizer.md) | Team skill optimizer (TeamSkillOptimizer, PATCH generation) |
| [evaluator](openjiuwen.agent_evolving/evaluator.md) | Evaluation interfaces and metrics (BaseEvaluator, DefaultEvaluator, MetricEvaluator, Metric) |
| [agent_rl](openjiuwen.agent_evolving/agent_rl/agent_rl.README.md) | VERL-based RL training (`RLConfig`, `OfflineRLOptimizer`, rollout coordination, reward registry) |

For **atomic operators** used with self-evolving, see [openjiuwen.core.operator](openjiuwen.core/operator.README.md) (Operator, LLMCallOperator, ToolCallOperator, MemoryCallOperator).
