# session

`openjiuwen.core.session`是openJiuwen框架的会话管理模块，提供Agent和工作流的会话上下文管理、状态管理、流式输出和交互能力。

**Classes**：

| CLASS | DESCRIPTION |
|-------|-------------|
| [InteractiveInput](./session/session.md) | 交互输入类。 |
| [InteractionOutput](./session/session.md) | 交互输出类。 |
| [BaseStreamMode](./session/stream/stream.md) | 流式输出模式枚举。 |
| [StreamSchemas](./session/session.md) | 流式输出数据格式基类。|
| [OutputSchema](./session/stream/stream.md) | 标准流式输出数据格式。 |
| [TraceSchema](./session/stream/stream.md) | 调试信息流式输出数据格式。 |
| [CustomSchema](./session/stream/stream.md) | 自定义流式输出数据格式。 |
| [Checkpointer](./session/checkpointer.md) | 检查点抽象基类及实现。 |

## Tracer 扩展 handler

框架内置 Tracer 提供扩展 handler 机制，允许在 Agent / 工作流执行期间将事件转发到自定义处理器，用于对接外部可观测性后端。

- **`TraceExtAgentHandler` / `TraceExtWorkflowHandler`**：扩展 handler 抽象基类，定义 Agent 与工作流全部事件方法。与内置 `TraceBaseHandler` 不同，不依赖 `StreamWriterManager` 或 Tracer 的 `SpanManager`，可自由选择自身的 span 管理方式（例如 OpenTelemetry Tracer）。
- **`TracerHandlerRegistry`**：全局注册表。在调用 `agent.invoke` / `workflow.invoke` 之前通过 `register_handler(name, handler)` 注册，之后创建的所有 `Tracer` 实例都会自动加载。注册名不可与内置名（`tracer_agent` / `tracer_workflow`）重复。

框架已提供基于该机制的 OpenTelemetry 扩展实现，详见 [tracer_otel 扩展](../openjiuwen.extensions/tracer_otel.README.md)。