# API文档

本章节按源码包结构组织 API 文档，确保与 `agent-core/openjiuwen` 一一对应。

| 包/模块 | 说明 |
|---|---|
| [`openjiuwen.core`](./openjiuwen.core.README.md) | 核心能力：workflow、session、runner、retrieval、memory 等。 |
| [`openjiuwen.agent_teams`](./openjiuwen.agent_teams.README.md) | 多智能体团队协作框架：Leader/Teammate 协作、传输层、持久化存储。 |
| [`openjiuwen.dev_tools`](./openjiuwen.dev_tools.README.md) | 开发工具：prompt_builder、tune（提示词生成/优化与调优）、[agent_builder](./openjiuwen.dev_tools/agent_builder.README.md)（Agent 构建）。 |
| [`openjiuwen.extensions`](./openjiuwen.extensions.README.md) | 可选扩展：如 message_queue。 |

