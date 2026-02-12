# openjiuwen.agent_evolving

`openjiuwen.agent_evolving` 是 openJiuwen 中的**自演进训练与评估框架**，负责：

- 编排「评估 → 生成更新 → 写回」的自进化循环，与 [openjiuwen.core.operator](openjiuwen.core/operator.README.md) 配合对可优化算子进行迭代优化；
- 提供数据集抽象（Case、EvaluatedCase、CaseLoader）、训练进度与回调（Trainer、Progress、Callbacks）；
- 提供轨迹类型与抽取（Trajectory、TracerTrajectoryExtractor）、更新器（Updater、SingleDimUpdater、MultiDimUpdater）；
- 提供检查点与恢复（EvolveCheckpoint、FileCheckpointStore、DefaultCheckpointManager）；
- 提供优化器基类与 LLM 指令优化实现（BaseOptimizer、InstructionOptimizer）；
- 评估能力位于子模块 `openjiuwen.agent_evolving.evaluator`（BaseEvaluator、DefaultEvaluator、MetricEvaluator、Metric、ExactMatchMetric、LLMAsJudgeMetric）。

**文档索引**：

| 模块 | 说明 |
|------|------|
| [constant](openjiuwen.agent_evolving/constant.md) | 超参默认值与取值范围（TuneConstant） |
| [dataset](openjiuwen.agent_evolving/dataset.md) | 样本与加载（Case、EvaluatedCase、CaseLoader、shuffle_cases、split_cases） |
| [trainer](openjiuwen.agent_evolving/trainer.md) | 训练编排（Trainer、Progress、Callbacks） |
| [trajectory](openjiuwen.agent_evolving/trajectory.md) | 轨迹类型与抽取（Trajectory、TracerTrajectoryExtractor、iter_steps 等） |
| [updater](openjiuwen.agent_evolving/updater.md) | 更新器（Updater、SingleDimUpdater、MultiDimUpdater） |
| [checkpointing](openjiuwen.agent_evolving/checkpointing.md) | 检查点与恢复（EvolveCheckpoint、FileCheckpointStore、CheckpointManager） |
| [optimizer](openjiuwen.agent_evolving/optimizer.md) | 优化器（BaseOptimizer、TextualParameter、InstructionOptimizer） |
| [evaluator](openjiuwen.agent_evolving/evaluator.md) | 评估接口与指标（BaseEvaluator、DefaultEvaluator、MetricEvaluator、Metric） |

与自演进配合使用的**原子算子**见 [openjiuwen.core.operator](openjiuwen.core/operator.README.md)（Operator、LLMCallOperator、ToolCallOperator、MemoryCallOperator）。
