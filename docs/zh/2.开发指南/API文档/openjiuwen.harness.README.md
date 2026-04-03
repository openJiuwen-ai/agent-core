# openjiuwen.harness

`openjiuwen.harness` 提供 Agent Harness 框架的 API 文档——与其他智能体框架相同的核心工具调用循环，但内置了规划、上下文管理、子智能体生成和长期记忆能力，用于处理复杂的多步骤任务。基于 `openjiuwen.core` 原语构建。

| 模块 | 说明 |
|---|---|
| [`deep_agent`](./openjiuwen.harness/deep_agent.md) | DeepAgent 核心类 |
| [`factory`](./openjiuwen.harness/factory.md) | `create_deep_agent` 工厂函数 |
| [`schema`](./openjiuwen.harness/schema/config.md) | 配置和状态 Schema（`DeepAgentConfig`、`VisionModelConfig`、`AudioModelConfig` 等） |
| [`task_loop`](./openjiuwen.harness/task_loop/task_loop.md) | 外层任务循环基础设施（`TaskLoopEventHandler`、`LoopCoordinator` 等） |
| [`workspace`](./openjiuwen.harness/workspace/workspace.md) | 工作区文件系统 Schema |
| [`rails`](./openjiuwen.harness/rails/rails.md) | 护栏扩展 |
| [`tools`](./openjiuwen.harness/tools/tools.md) | 内置工具实现 |
| [`subagents`](./openjiuwen.harness/subagents/subagents.md) | 预配置子智能体工厂 |
| [`prompts`](./openjiuwen.harness/prompts/prompts.md) | 系统提示词构建器 |
