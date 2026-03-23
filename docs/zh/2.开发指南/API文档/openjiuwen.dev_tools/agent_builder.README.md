# agent_builder

`openjiuwen.dev_tools.agent_builder` 提供从**自然语言描述**构建 **LLM Agent** 与**工作流 Agent** 的能力：需求澄清、工作流设计、DL 生成与校验、Mermaid/平台 DSL 转换等，统一由 [AgentBuilder](./agent_builder/agent_builder.md) 入口按会话编排。

它的目标不是替代业务侧完整编排平台，而是把「对话式构建」常见步骤产品化，便于在研发环境快速生成可落地的 Agent/工作流配置。

## 关键依赖

- **大模型**：构建与检索依赖 [Model](../openjiuwen.core/foundation/llm/llm.md)。通过 `model_info`（如 `model_provider` / `model_name` / `api_key` 等）在 [executor](./agent_builder/executor.md) 中创建 `Model`。
- **会话状态**：多轮澄清与设计依赖 [HistoryManager](./agent_builder/executor.md)；可选 [ProgressReporter](./agent_builder/utils.md) 上报阶段进度。

> 建议：与 `prompt_builder`、`tune` 相同，适合在**研发/受控环境**调用；生产环境请控制调用频率、并发与成本。

**公开导出类型**（`from openjiuwen.dev_tools.agent_builder import ...`）：

| CLASS | DESCRIPTION |
|-------|-------------|
| [AgentBuilder](./agent_builder/agent_builder.md) | 统一构建入口类（源码 `main.py`）。 |
| [AgentBuilderExecutor](./agent_builder/executor.md) | 单次构建执行器。 |
| [HistoryManager](./agent_builder/executor.md) / [HistoryCache](./agent_builder/executor.md) | 会话对话历史。 |
| [BaseAgentBuilder](./agent_builder/builders.md) | 构建器抽象基类。 |
| [AgentBuilderFactory](./agent_builder/builders.md) | 按 [AgentType](./agent_builder/utils.md) 创建构建器。 |
| [LlmAgentBuilder](./agent_builder/builders/llm_agent.md) | LLM Agent 构建实现。 |
| [WorkflowBuilder](./agent_builder/builders/workflow.md) | 工作流 Agent 构建实现。 |

**按子模块划分的 API 文档**：

| 文档 | 说明 |
|------|------|
| [agent_builder.md](./agent_builder/agent_builder.md) | 包路径 `openjiuwen.dev_tools.agent_builder` 与 `AgentBuilder`。 |
| [builders.md](./agent_builder/builders.md) | `BaseAgentBuilder`、`AgentBuilderFactory`。 |
| [builders/llm_agent.md](./agent_builder/builders/llm_agent.md) | LLM Agent：`Clarifier`、`Generator`、`Transformer` 等。 |
| [builders/workflow.md](./agent_builder/builders/workflow.md) | 工作流：`WorkflowBuilder`、`IntentionDetector`、`DLGenerator` 等。 |
| [builders/workflow/dl_transformer.md](./agent_builder/builders/workflow/dl_transformer.md) | `DLTransformer` 与 `models`。 |
| [builders/workflow/dl_transformer/converters.md](./agent_builder/builders/workflow/dl_transformer/converters.md) | DL 节点到平台 DSL 的转换器。 |
| [builders/workflow/workflow_designer.md](./agent_builder/builders/workflow/workflow_designer.md) | `WorkflowDesigner`（SE 设计）。 |
| [executor.md](./agent_builder/executor.md) | `create_core_model`、`AgentBuilderExecutor`、历史管理。 |
| [resource.md](./agent_builder/resource.md) | `ResourceRetriever`、`PluginProcessor`。 |
| [utils.md](./agent_builder/utils.md) | 枚举、进度、工具函数。 |

**上级文档**：[openjiuwen.dev_tools](../openjiuwen.dev_tools.README.md)
