# agent_builder

`openjiuwen.dev_tools.agent_builder` builds **LLM Agents** and **workflow Agents** from **natural language**: clarification, workflow design, DL generation/validation, Mermaid/platform DSL conversion, etc., orchestrated per session through the [AgentBuilder](./agent_builder/agent_builder.md) entry point.

It does not replace a full production orchestration stack; it productizes common “conversational build” steps for faster agent/workflow configs in development.

## Key dependencies

- **LLM**: Retrieval and generation use [Model](../openjiuwen.core/foundation/llm/llm.md). Pass `model_info` (e.g. `model_provider` / `model_name` / `api_key`) so [executor](./agent_builder/executor.md) can construct `Model`.
- **Session state**: Multi-turn flows use [HistoryManager](./agent_builder/executor.md); optional [ProgressReporter](./agent_builder/utils.md) reports stage progress.

> Like `prompt_builder` and `tune`, prefer **offline/controlled** use; in production, cap rate, concurrency, and cost.

**Exported symbols** (`from openjiuwen.dev_tools.agent_builder import ...`):

| CLASS | DESCRIPTION |
|-------|-------------|
| [AgentBuilder](./agent_builder/agent_builder.md) | Unified build entry (`main.py`, `openjiuwen.dev_tools.agent_builder.main.AgentBuilder`). |
| [AgentBuilderExecutor](./agent_builder/executor.md) | Single-run executor. |
| [HistoryManager](./agent_builder/executor.md) / [HistoryCache](./agent_builder/executor.md) | Dialog history. |
| [BaseAgentBuilder](./agent_builder/builders.md) | Abstract builder base class. |
| [AgentBuilderFactory](./agent_builder/builders.md) | Factory using [AgentType](./agent_builder/utils.md). |
| [LlmAgentBuilder](./agent_builder/builders/llm_agent.md) | LLM Agent builder. |
| [WorkflowBuilder](./agent_builder/builders/workflow.md) | Workflow Agent builder. |

**API docs by submodule**:

| Document | Notes |
|----------|--------|
| [agent_builder.md](./agent_builder/agent_builder.md) | Package `openjiuwen.dev_tools.agent_builder` and `AgentBuilder`. |
| [builders.md](./agent_builder/builders.md) | `BaseAgentBuilder`, `AgentBuilderFactory`. |
| [builders/llm_agent.md](./agent_builder/builders/llm_agent.md) | LLM Agent: `Clarifier`, `Generator`, `Transformer`, etc. |
| [builders/workflow.md](./agent_builder/builders/workflow.md) | Workflow: `WorkflowBuilder`, `IntentionDetector`, `DLGenerator`, etc. |
| [builders/workflow/dl_transformer.md](./agent_builder/builders/workflow/dl_transformer.md) | `DLTransformer` and `models`. |
| [builders/workflow/dl_transformer/converters.md](./agent_builder/builders/workflow/dl_transformer/converters.md) | DL node → platform DSL converters. |
| [builders/workflow/workflow_designer.md](./agent_builder/builders/workflow/workflow_designer.md) | `WorkflowDesigner` (SE design). |
| [executor.md](./agent_builder/executor.md) | `create_core_model`, `AgentBuilderExecutor`, history. |
| [resource.md](./agent_builder/resource.md) | `ResourceRetriever`, `PluginProcessor`. |
| [utils.md](./agent_builder/utils.md) | Enums, progress, helpers. |

**Parent**：[openjiuwen.dev_tools](../openjiuwen.dev_tools.README.md)
