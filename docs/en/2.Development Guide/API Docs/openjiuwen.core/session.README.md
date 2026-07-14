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

## Tracer extension handlers

The framework's built-in Tracer provides an extension-handler mechanism that forwards events to custom handlers during Agent / workflow execution, for integration with external observability backends.

- **`TraceExtAgentHandler` / `TraceExtWorkflowHandler`**: abstract base classes for extension handlers, defining all Agent and workflow event methods. Unlike the built-in `TraceBaseHandler`, they do not depend on `StreamWriterManager` or the Tracer's `SpanManager`, and are free to choose their own span management approach (e.g. an OpenTelemetry Tracer).
- **`TracerHandlerRegistry`**: global registry. Register a handler via `register_handler(name, handler)` before calling `agent.invoke` / `workflow.invoke`; every `Tracer` instance created afterwards picks it up automatically. The registration name must not collide with the built-in names (`tracer_agent` / `tracer_workflow`).

An OpenTelemetry extension built on this mechanism is provided out of the box, see [tracer_otel extension](../openjiuwen.extensions/tracer_otel.README.md).