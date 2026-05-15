# openjiuwen.agent_evolving

`openjiuwen.agent_evolving` 是 openJiuwen 中的自演进训练与评估框架。

**文档索引**：

| 模块 | 说明 |
|------|------|
| [constant](openjiuwen.agent_evolving/constant.md) | 超参默认值与取值范围（TuneConstant） |
| [dataset](openjiuwen.agent_evolving/dataset.md) | 样本与加载（Case、EvaluatedCase、CaseLoader、shuffle_cases、split_cases） |
| [experience](openjiuwen.agent_evolving/experience.md) | 在线经验生命周期编排（OnlineEvolutionOrchestrator、ExperienceManager、ExperienceTracker、ExperienceScorer） |
| [signal](openjiuwen.agent_evolving/signal.md) | 演进信号检测与 fingerprint 工具（EvolutionSignal、SignalDetector、TeamSignalDetector） |
| [trainer](openjiuwen.agent_evolving/trainer.md) | 训练编排（Trainer、Progress、Callbacks） |
| [trajectory](openjiuwen.agent_evolving/trajectory.md) | 轨迹类型与抽取（Trajectory、TrajectoryExtractor、iter_steps 等） |
| [updater](openjiuwen.agent_evolving/updater.md) | 更新器（`Updater`、`SingleDimUpdater`、`MultiDimUpdater`） |
| [checkpointing](openjiuwen.agent_evolving/checkpointing.md) | 检查点与恢复（`EvolveCheckpoint`、`FileCheckpointStore`、`CheckpointManager`） |
| [optimizer](openjiuwen.agent_evolving/optimizer/optimizer.md) | 优化器基类（BaseOptimizer、TextualParameter、InstructionOptimizer） |
| [optimizer/skill_call/team_skill_experience_optimizer](openjiuwen.agent_evolving/optimizer/skill_call/team_skill_experience_optimizer.md) | 团队技能优化器（TeamSkillExperienceOptimizer、经验记录生成） |
| [evaluator](openjiuwen.agent_evolving/evaluator.md) | 评估接口与指标（BaseEvaluator、DefaultEvaluator、MetricEvaluator、Metric） |
| [agent_rl](openjiuwen.agent_evolving/agent_rl/agent_rl.README.md) | 基于 VERL 的 RL 训练（`RLConfig`、`OfflineRLOptimizer`、rollout 编排、奖励注册等） |

与自演进配合使用的原子算子见 [openjiuwen.core.operator](openjiuwen.core/operator.README.md)。
