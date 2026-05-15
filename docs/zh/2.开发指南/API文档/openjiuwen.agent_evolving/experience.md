# openjiuwen.agent_evolving.experience

`openjiuwen.agent_evolving.experience` 负责 skill 与 team-skill 在线经验生命周期编排。

**文档索引**：

| 模块 | 说明 |
|------|------|
| [lifecycle](openjiuwen.agent_evolving/experience/lifecycle.md) | 稳定生命周期结果 contract（`LocalApplyPreview`、`HostFacingExperienceResult`、`PendingCommitResult`、`RebuildRequest`） |
| [online_orchestrator](openjiuwen.agent_evolving/experience/online_orchestrator.md) | 在线管线协调器（`OnlineEvolutionOrchestrator`） |
| [scorer](openjiuwen.agent_evolving/experience/scorer.md) | 经验评分与维护（`ExperienceScorer`、评分辅助函数） |
| [skill_experience_manager](openjiuwen.agent_evolving/experience/skill_experience_manager.md) | 经验生命周期管理器（`ExperienceManager`） |
| [tracker](openjiuwen.agent_evolving/experience/tracker.md) | 经验展示跟踪与评分（`ExperienceTracker`） |
| [types](openjiuwen.agent_evolving/experience/types.md) | 演进上下文与生命周期 DTO（`EvolutionContext`、`ExperienceProposal`、`ExperienceApprovalRequest`、`ExperienceApplyResult`、`PendingChange`） |
