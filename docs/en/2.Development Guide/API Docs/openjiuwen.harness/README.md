# openjiuwen.harness

API documentation for the agent harness framework — built-in planning, context management, sub-agent spawning, and long-term memory for complex, multi-step tasks.

| Module | Description |
|---|---|
| [`deep_agent`](./deep_agent.md) | `DeepAgent` class — the primary agent harness runtime |
| [`factory`](./factory.md) | `create_deep_agent` convenience factory function |
| [`schema/config`](./schema/config.md) | Configuration dataclasses (`DeepAgentConfig`, `SubAgentConfig`, `VisionModelConfig`, `AudioModelConfig`) |
| [`schema/state`](./schema/state.md) | Persistent agent state (`DeepAgentState`) |
| [`schema/loop_event`](./schema/loop_event.md) | Loop event types and helpers (`DeepLoopEventType`, `DeepLoopEvent`) |
| [`schema/task`](./schema/task.md) | Task planning models (`TaskStatus`, `TaskItem`, `TaskPlan`) |
| [`task_loop`](./task_loop/task_loop.md) | Task-loop runtime (`LoopCoordinator`, `TaskLoopController`, event handler and executor) |
| [`workspace`](./workspace/workspace.md) | Workspace directory management (`Workspace`, `WorkspaceNode`) |
| [`rails`](./rails/rails.md) | Built-in guardrails overview (16 rails) |
| [`tools`](./tools/tools.md) | Built-in tools overview (27 tools) |
| [`subagents`](./subagents/subagents.md) | Sub-agent factory functions (`create_browser_agent`, `create_code_agent`, `create_research_agent`) |
| [`prompts`](./prompts/prompts.md) | Prompt assembly (`PromptMode`, `SystemPromptBuilder`, `PromptReport`, sanitizers) |
