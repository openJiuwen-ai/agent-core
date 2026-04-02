# openjiuwen.harness

`openjiuwen.harness` provides the API documentation for the agent harness framework — the same core tool-calling loop as other agent frameworks, but with built-in planning, context management, sub-agent spawning, and long-term memory for handling complex, multi-step tasks. Built on top of `openjiuwen.core` primitives.

| Module | Description |
|---|---|
| [`deep_agent`](./openjiuwen.harness/deep_agent.md) | `DeepAgent` class — the primary agent harness runtime. |
| [`factory`](./openjiuwen.harness/factory.md) | `create_deep_agent` convenience factory function. |
| [`schema/config`](./openjiuwen.harness/schema/config.md) | Configuration dataclasses (`DeepAgentConfig`, `SubAgentConfig`, `VisionModelConfig`, `AudioModelConfig`). |
| [`schema/state`](./openjiuwen.harness/schema/state.md) | Persistent agent state (`DeepAgentState`). |
| [`schema/loop_event`](./openjiuwen.harness/schema/loop_event.md) | Loop event types and helpers (`DeepLoopEventType`, `DeepLoopEvent`). |
| [`schema/task`](./openjiuwen.harness/schema/task.md) | Task planning models (`TaskStatus`, `TaskItem`, `TaskPlan`). |
| [`task_loop`](./openjiuwen.harness/task_loop/task_loop.md) | Task-loop runtime (`LoopCoordinator`, `TaskLoopController`, event handler and executor). |
| [`workspace`](./openjiuwen.harness/workspace/workspace.md) | Workspace directory management (`Workspace`, `WorkspaceNode`). |
| [`rails`](./openjiuwen.harness/rails/rails.md) | Built-in guardrails overview (16 rails). |
| [`tools`](./openjiuwen.harness/tools/tools.md) | Built-in tools overview (27 tools). |
| [`subagents`](./openjiuwen.harness/subagents/subagents.md) | Sub-agent factory functions (`create_browser_agent`, `create_code_agent`, `create_research_agent`). |
| [`prompts`](./openjiuwen.harness/prompts/prompts.md) | Prompt assembly (`PromptMode`, `SystemPromptBuilder`, `PromptReport`, sanitizers). |
