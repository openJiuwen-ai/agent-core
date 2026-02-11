# session

`openjiuwen.core.session` is the session management module of the openJiuwen framework, providing session context management, state management, streaming output, and interaction capabilities for Agents and workflows.

**Classes**:

| CLASS | DESCRIPTION |
|-------|-------------|
| [InteractiveInput](./session/session.md) | Interactive input class. |
| [InteractionOutput](./session/session.md) | Interactive output class. |
| [BaseStreamMode](./session/stream/stream.md) | Streaming output mode enumeration. |
| [StreamSchemas](./session/session.md) | Streaming output data format base class. |
| [OutputSchema](./session/stream/stream.md) | Standard streaming output data format. |
| [TraceSchema](./session/stream/stream.md) | Debug information streaming output data format. |
| [CustomSchema](./session/stream/stream.md) | Custom streaming output data format. |
| [Checkpointer](./session/checkpointer.md) | Checkpoint abstract base class and implementations. |