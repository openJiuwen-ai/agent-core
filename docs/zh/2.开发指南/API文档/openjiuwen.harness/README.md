# openjiuwen.harness

`openjiuwen.harness` 提供 Agent Harness 框架的 API 文档——内置规划、上下文管理、子智能体生成和长期记忆，用于处理复杂的多步骤任务。

| 模块 | 说明 |
|---|---|
| [`deep_agent`](./deep_agent.md) | DeepAgent 核心类 |
| [`factory`](./factory.md) | `create_deep_agent` 工厂函数 |
| [`schema`](./schema/config.md) | 配置和状态 Schema（`DeepAgentConfig`、`VisionModelConfig`、`AudioModelConfig` 等） |
| [`task_loop`](./task_loop/task_loop.md) | 外层任务循环基础设施（`TaskLoopEventHandler`、`LoopCoordinator` 等） |
| [`workspace`](./workspace/workspace.md) | 工作区文件系统 Schema |
| [`rails`](./rails/rails.md) | 护栏扩展 |
| [`tools`](./tools/tools.md) | 内置工具实现 |
| [`subagents`](./subagents/subagents.md) | 预配置子智能体工厂 |
| [`prompts`](./prompts/prompts.md) | 系统提示词构建器 |
