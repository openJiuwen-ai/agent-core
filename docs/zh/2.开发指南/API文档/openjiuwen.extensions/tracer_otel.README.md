# tracer_otel

`openjiuwen.extensions.tracer_otel` 提供可选的 OpenTelemetry 链路追踪扩展，将框架内置 Tracer 产生的 Agent 与工作流执行事件转换为 OTel span，导出到 Jaeger、Zipkin、OTLP Collector 等后端。

该扩展独立于 `agent_teams.observability`，不调用全局 `trace.set_tracer_provider()`，因此可以与 `init_observability()` 共存，互不冲突。

**Modules**：

| MODULE | DESCRIPTION |
|---|---|
| [tracer_otel](./tracer_otel/tracer_otel.md) | OTel 链路追踪扩展，包含 `OtelTracerConfig` 配置、`init_otel_tracer` 初始化、`OtelAgentHandler` / `OtelWorkflowHandler` 处理器及注册流程。 |

> **说明**
>
> - 使用该扩展前，需先了解框架内置 Tracer 的扩展 handler 机制，详见 [session 模块 - Tracer 扩展 handler](../openjiuwen.core/session.README.md#tracer-扩展-handler)。
> - 该扩展通过 `TracerHandlerRegistry` 注册到全局，所有在注册之后创建的 `Tracer` 实例都会自动加载。
