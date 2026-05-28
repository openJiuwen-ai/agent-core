# openjiuwen.agent_evolving.experience.online_orchestrator

普通 skill 与 team-skill rail 共享的在线演进管线协调器。

## class OnlineEvolutionOrchestrator

协调单个 skill target 的共享在线演进管线。

`evolve()` 返回 `OnlineEvolutionResult`。调用方通过 `result.status` 区分已暂存、已自动批准、正常无记录完成、输入为空和 skill 不存在等结果。
