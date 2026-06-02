# openjiuwen.agent_evolving

`openjiuwen.agent_evolving` is the self-evolving training and evaluation framework in openJiuwen.

**Documentation Index**:

| Module | Description |
|--------|-------------|
| [constant](openjiuwen.agent_evolving/constant.md) | Hyperparameter defaults and value ranges (TuneConstant) |
| [dataset](openjiuwen.agent_evolving/dataset.md) | Samples and loading (Case, EvaluatedCase, CaseLoader, shuffle_cases, split_cases) |
| [experience](openjiuwen.agent_evolving/experience.md) | Online experience lifecycle orchestration (OnlineEvolutionOrchestrator, ExperienceManager, ExperienceTracker, ExperienceScorer) |
| [signal](openjiuwen.agent_evolving/signal.md) | Evolution signal detection and fingerprint utilities (EvolutionSignal, SignalDetector, TeamSignalDetector) |
| [trainer](openjiuwen.agent_evolving/trainer.md) | Training orchestration (Trainer, Progress,Callbacks) |
| [trajectory](openjiuwen.agent_evolving/trajectory.md) | Trajectory types, extraction, stores, and runtime aggregation registry (Trajectory, TrajectoryExtractor, InMemoryTrajectoryRegistry, etc.) |
| [updater](openjiuwen.agent_evolving/updater.md) | Updaters (`Updater`, `SingleDimUpdater`, `MultiDimUpdater`) |
| [checkpointing](openjiuwen.agent_evolving/checkpointing.md) | Checkpoint and restore (`EvolveCheckpoint`, `FileCheckpointStore`, `CheckpointManager`) |
| [optimizer](openjiuwen.agent_evolving/optimizer/optimizer.md) | Optimizer base classes (BaseOptimizer, TextualParameter, InstructionOptimizer) |
| [optimizer/skill_call/team_skill_experience_optimizer](openjiuwen.agent_evolving/optimizer/skill_call/team_skill_experience_optimizer.md) | Team skill optimizer (TeamSkillExperienceOptimizer, experience record generation) |
| [evaluator](openjiuwen.agent_evolving/evaluator/evaluator.md) | Evaluation interfaces and metrics (BaseEvaluator, DefaultEvaluator, MetricEvaluator, Metric) |
| [evaluator/evaluator_pipeline](openjiuwen.agent_evolving/evaluator/evaluator_pipeline/README.md) | Skill evaluation and evolution pipeline (EvolutionPipeline, BaseAgentAdapter, BaseBenchAdapter, SkillManager) |
| [agent_rl](openjiuwen.agent_evolving/agent_rl/agent_rl.README.md) | VERL-based RL training (`RLConfig`, `OfflineRLOptimizer`, rollout coordination, reward registry) |

For atomic operators used with self-evolving, see [openjiuwen.core.operator](openjiuwen.core/operator.README.md).
