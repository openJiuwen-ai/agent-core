# openjiuwen.agent_evolving.experience

`openjiuwen.agent_evolving.experience` coordinates the online experience lifecycle for skill and team-skill evolution.

**Documentation Index**:

| Module | Description |
|--------|-------------|
| [lifecycle](experience/lifecycle.md) | Stable lifecycle result contracts (`LocalApplyPreview`, `HostFacingExperienceResult`, `PendingCommitResult`, `RebuildRequest`) |
| [online_orchestrator](experience/online_orchestrator.md) | Online pipeline coordinator (`OnlineEvolutionOrchestrator`) |
| [scorer](experience/scorer.md) | Experience scoring and maintenance (`ExperienceScorer`, score helpers) |
| [skill_experience_manager](experience/skill_experience_manager.md) | Experience lifecycle manager (`ExperienceManager`) |
| [tracker](experience/tracker.md) | Experience presentation tracking and scoring (`ExperienceTracker`) |
| [types](experience/types.md) | Evolution context and lifecycle DTOs (`EvolutionContext`, `ExperienceProposal`, `ExperienceApprovalRequest`, `ExperienceApplyResult`, `PendingChange`) |
