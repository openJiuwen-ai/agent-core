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