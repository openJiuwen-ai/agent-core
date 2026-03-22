# openjiuwen.dev_tools

`openjiuwen.dev_tools` provides openJiuwen's **Development Tools (DevTools)** module, targeting "development/debugging/iteration" scenarios, mainly covering three types of capabilities:
- **Prompt Building and Optimization (prompt_builder)**:
  - Used to convert "task descriptions/requirements/feedback/bad cases" into more robust prompt templates.
  - Suitable for quickly converging quality in prompt engineering phase, reducing manual repeated trial and error.
- **Agent Tuning (tune)**:
  - Used to combine `Optimizer + Evaluator + Trainer` into a repeatable training process.
  - Suitable for automated evaluation and iteration of prompt parameters (system/user prompt, etc.) when there is a case dataset.
- **Agent Reinforcement Learning (agentrl)**:
  - RL training extension based on verl, supporting multi-turn rollout orchestration, parallel execution, and structured trajectory collection.
  - Provides high-level entry point `RLOptimizer` for simplified RL training pipeline configuration and execution.

> Note: `dev_tools` targets "development iteration", not recommended for uncontrolled calls in production main path (may bring additional LLM costs and time consumption).
>
> Production side usually uses solidified prompt/agent configurations, tuning process is recommended to run offline or in controlled environments.

**Modules**:

| MODULE | DESCRIPTION |
|--------|-------------|
| [prompt_builder](./openjiuwen.dev_tools/prompt_builder.README.md) | Prompt building and optimization tools. |
| [tune](./openjiuwen.dev_tools/tune.README.md) | Agent tuning tools. |
| [agentrl](./openjiuwen.dev_tools/agentrl.README.md) | RL training extension based on verl. |
