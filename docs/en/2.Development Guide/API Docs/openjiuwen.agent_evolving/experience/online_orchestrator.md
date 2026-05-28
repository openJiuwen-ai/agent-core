# openjiuwen.agent_evolving.experience.online_orchestrator

Shared online evolution pipeline coordinator for skill and team-skill rails.

## class OnlineEvolutionOrchestrator

Coordinate the shared online evolution pipeline for one skill target.

`evolve()` returns `OnlineEvolutionResult`. The result distinguishes staged changes, auto-approved changes, normal no-record completion, empty input, and missing skills via `result.status`.
