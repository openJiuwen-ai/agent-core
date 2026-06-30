# openjiuwen.extensions

`openjiuwen.extensions` 提供可选的扩展能力，用于对接外部基础设施或扩展运行环境。

**Modules**：

| MODULE | DESCRIPTION |
|---|---|
| [message_queue](./openjiuwen.extensions/message_queue.README.md) | 消息队列扩展，例如 Pulsar。 |
| [checkpointer](./openjiuwen.extensions/checkpointer.README.md) | 检查点扩展，例如 Redis。 |
| [a2a](./openjiuwen.extensions/a2a/README.md) | A2A 协议集成，用于远程客户端和服务端适配器。 |
| [tracer_otel](./openjiuwen.extensions/tracer_otel.README.md) | OpenTelemetry 链路追踪扩展，将 Agent 与工作流执行事件导出为 OTel span。 |
