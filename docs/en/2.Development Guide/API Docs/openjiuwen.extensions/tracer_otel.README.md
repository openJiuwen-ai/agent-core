# tracer_otel

`openjiuwen.extensions.tracer_otel` is an optional OpenTelemetry tracing extension that converts the Agent and workflow execution events emitted by the framework's built-in Tracer into OTel spans, exporting them to backends such as Jaeger, Zipkin, or an OTLP Collector.

This extension is independent of `agent_teams.observability` and does **not** call the global `trace.set_tracer_provider()`, so it can coexist with `init_observability()` without conflict.

**Modules**:

| MODULE | DESCRIPTION |
|---|---|
| [tracer_otel](./tracer_otel/tracer_otel.md) | OTel tracing extension, including `OtelTracerConfig`, `init_otel_tracer`, `OtelAgentHandler` / `OtelWorkflowHandler`, and the registration flow. |

> **Note**
>
> - Before using this extension, familiarize yourself with the framework's built-in Tracer extension-handler mechanism, see [session module - Tracer extension handlers](../openjiuwen.core/session.README.md#tracer-extension-handlers).
> - This extension is registered globally via `TracerHandlerRegistry`. All `Tracer` instances created after registration will pick it up automatically.
