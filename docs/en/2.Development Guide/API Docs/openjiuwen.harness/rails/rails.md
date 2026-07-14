# openjiuwen.harness.rails

Built-in guardrails that hook into the `DeepAgent` lifecycle. Rails are registered via `DeepAgent.add_rail()` or through the `rails` parameter in [`create_deep_agent`](../factory.md#function-openjiuwenharnesscreate_deep_agent).

## Overview

| Rail | Description |
|---|---|
| `DeepAgentRail` | Base rail class for `DeepAgent`-specific lifecycle hooks. |
| `ContextEngineeringRail` | Manages context window by compressing or offloading older messages. |
| `SysOperationRail` | Enforces file-system access boundaries (e.g. restrict to workspace). |
| `HeartbeatRail` | Emits periodic heartbeat events during long-running loops. |
| `MemoryRail` | Integrates long-term memory read/write into the agent loop. |
| `ProgressiveToolRail` | Dynamically loads and unloads tools based on relevance to the current task. |
| `SecurityRail` | Applies security policies such as command allowlists and path validation. |
| `SkillUseRail` | Enables the agent to discover and invoke learned skills; it does not generate, approve, or persist evolution records. |
| `SkillEvolutionRail` | Evolves existing regular skills from trajectories or user requests, with optional approval before `EvolutionStore` persistence. |
| `SubagentRail` | Manages sub-agent spawning via `enable_async_subagent` flag — sync mode registers `TaskTool`, async mode registers `session` tools. |
| `TaskCompletionRail` | Evaluates whether the current task is complete and triggers the stop condition. |
| `ContextEvolutionRail` | Persists and retrieves task-specific memory across iterations. |
| `TaskPlanningRail` | Generates and maintains a structured task plan during the loop. |
| `AskUserRail` | Pauses the loop to ask the user a clarifying question (interrupt). |
| `ConfirmInterruptRail` | Requires user confirmation before executing sensitive operations. |
| `TeamSkillCreateRail` | Auto-detects multi-agent collaboration patterns and suggests team skill creation. |
| `TeamSkillRail` | Compatibility alias for `TeamSkillEvolutionRail`; evolves existing team skills using aggregated team trajectories and approval-governed experience records. |

## Rail Lifecycle

Each rail can implement one or more of the following hooks:

- **before_round**: Called before the inner `ReActAgent` starts a round.
- **after_round**: Called after the inner agent completes a round.
- **before_tool_call**: Called before a tool is executed.
- **after_tool_call**: Called after a tool returns.
- **on_init**: Called once when the agent is configured.
- **on_complete**: Called when the task loop finishes.

Rails are executed in registration order. A rail may short-circuit by returning a signal (e.g. force-finish, interrupt) that prevents subsequent rails and the round itself from running.

## Online Skill Evolution

Regular skill and team skill evolution share the downstream lifecycle:

```text
signals -> local apply preview -> pending approval or auto-approved -> EvolutionStore persistence -> projection
```

- See [`skill_evolution_rail`](./evolution/skill_evolution_rail.md) for regular `SkillEvolutionRail`.
- See [`team_skill_evolution_rail`](./evolution/team_skill_evolution_rail.md) for `TeamSkillCreateRail`, `TeamSkillEvolutionRail`, and the `TeamSkillRail` compatibility alias.
- Evolution host events are buffered as `OutputSchema` objects. The canonical drain API is `drain_pending_host_events()`; `drain_pending_approval_events()` is a compatibility wrapper.
