# runner

`openjiuwen.core.runner` provides a unified execution interface for Workflows, Agents, and Tools, full lifecycle management for Agent objects, and an async callback framework for event-driven logic, chaining, and filtering.

**Classes / modules**:

| CLASS / MODULE | DESCRIPTION |
|----------------|-------------|
| [Runner](runner/runner.md) | Unified execution interface for Workflow, Agent, Tool, and full lifecycle management |
| [callback](runner/callback/callback.README.md) | Async callback framework: event-driven, filtering, chains with rollback, metrics and hooks |
| [resource_manager](runner/resource_manager/resource_manager.md) | Resource manager for registering and resolving Workflows, Agents, Tools, etc. |
