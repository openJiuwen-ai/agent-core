# openjiuwen.extensions.tracer_otel

`openjiuwen.extensions.tracer_otel` converts the Agent and workflow execution events emitted by the framework's built-in Tracer into OpenTelemetry spans, exporting them via OTLP (gRPC / HTTP) or to the console.

This module is independent of `agent_teams.observability`: `init_otel_tracer` does **not** call the global `trace.set_tracer_provider()`. The returned `Tracer` is bound directly to the `TracerProvider` created by this module, so it can coexist with `init_observability()` without conflict.

## class openjiuwen.extensions.tracer_otel.OtelTracerConfig

```python
class openjiuwen.extensions.tracer_otel.OtelTracerConfig(
    tracer_name: str = "openjiuwen.tracer.otel",
    exporter_type: str = "otlp",
    exporter_endpoint: str | None = None,
    protocol: str = "grpc",
    headers: dict[str, str] = None,
    service_name: str = "openjiuwen",
    service_version: str | None = None,
    sample_rate: float = 1.0,
    schedule_delay_millis: int = 5000,
    export_timeout_ms: int = 30000,
    max_export_batch_size: int = 512,
    redaction_enabled: bool = True,
    redact_prompts: bool | None = None,
    redact_completions: bool | None = None,
    max_attr_length: int = 4096,
)
```

Immutable OTel tracer configuration (`@dataclass(frozen=True)`). Independent of `ObservabilityConfig` — does not depend on `agent_teams`.

**Parameters**:

- **tracer_name**(str): OTel tracer name. Default `"openjiuwen.tracer.otel"`.
- **exporter_type**(str): Exporter type, either `"otlp"` (default) or `"console"`.
- **exporter_endpoint**(str | None): OTLP export endpoint. Not required when `exporter_type="console"`.
- **protocol**(str): OTLP transport protocol, `"grpc"` (default) or `"http"`. Only effective when `exporter_type="otlp"`.
- **headers**(dict[str, str]): Custom request headers for the OTLP exporter (e.g. authentication). Default empty.
- **service_name**(str): `service.name` in the OTel Resource. Default `"openjiuwen"`.
- **service_version**(str | None): `service.version` in the OTel Resource. Default `None` (recorded as `"unknown"`).
- **sample_rate**(float): Sampling probability in `[0.0, 1.0]`. Default `1.0` (full). Out-of-range values raise `ValueError`.
- **schedule_delay_millis**(int): `BatchSpanProcessor` export interval in milliseconds. Default `5000`.
- **export_timeout_ms**(int): `BatchSpanProcessor` export timeout in milliseconds. Default `30000`.
- **max_export_batch_size**(int): Maximum span count per `BatchSpanProcessor` batch. Default `512`.
- **redaction_enabled**(bool): Master redaction switch. When `True`, prompts and completions are SHA-256 hashed. Default `True`.
- **redact_prompts**(bool | None): Fine-grained prompt redaction override. `None` (default) falls back to `redaction_enabled`; `True` / `False` forces the value.
- **redact_completions**(bool | None): Fine-grained completion redaction override. Same semantics as `redact_prompts`.
- **max_attr_length**(int): Truncation cap for string attribute values. Default `4096`.

> **Note**
>
> - `redact_prompts` / `redact_completions` take precedence over `redaction_enabled`, so you can hash prompts while keeping completions in plaintext (or vice versa).

## function openjiuwen.extensions.tracer_otel.init_otel_tracer

```python
openjiuwen.extensions.tracer_otel.init_otel_tracer(config: OtelTracerConfig) -> trace.Tracer
```

Creates a `TracerProvider` from `OtelTracerConfig` and returns a `Tracer` instance.

- `exporter_type="console"`: uses `ConsoleSpanExporter` + `SimpleSpanProcessor` (for debugging).
- `exporter_type="otlp"`: uses `OTLPSpanExporter` + `BatchSpanProcessor`; the gRPC or HTTP implementation is selected by `protocol`.
- `protocol="http"`: the HTTP exporter requires the endpoint to end with `/v1/traces`; it is appended automatically if missing.

**Parameters**:

- **config**(OtelTracerConfig): Immutable configuration instance.

**Returns**:

- `opentelemetry.trace.Tracer`, ready to be passed into `OtelAgentHandler` / `OtelWorkflowHandler`.

**Raises**:

- **JiuWenBaseException**: raised when `exporter_type` or `protocol` is invalid (`StatusCode.COMMON_TASK_CONFIG_ERROR`). For details and resolution, see [StatusCode](../openjiuwen.core/common/exception/status_code.md).

> **Note**
>
> - This function does **not** call `trace.set_tracer_provider()`. The returned `Tracer` is bound directly to the provider created here, avoiding a conflict with `agent_teams.observability.init_observability()` over the global provider.

## class openjiuwen.extensions.tracer_otel.OtelAgentHandler

```python
class openjiuwen.extensions.tracer_otel.OtelAgentHandler(
    otel_tracer: trace.Tracer,
    config: OtelTracerConfig,
    trace_id: str | None = None,
)
```

Agent-dimension OTel handler, inheriting from `TraceExtAgentHandler`. Converts Agent events (LLM / plugin / chain / retriever / evaluator, …) into OTel spans.

- LLM events use `SpanKind.CLIENT` with the `gen_ai.*` standard semantic conventions; all other events use `SpanKind.INTERNAL` with `openjiuwen.agent.*`.
- Every handler method is wrapped in `try/except` so OTel failures never propagate to the business flow.

**Parameters**:

- **otel_tracer**(trace.Tracer): OTel Tracer instance returned by `init_otel_tracer`.
- **config**(OtelTracerConfig): Redaction / truncation configuration.
- **trace_id**(str | None): Tracer UUID used to bridge OTel traces with the built-in Tracer's UUID. When `None`, it is injected automatically by `TracerHandlerRegistry.register_handler` at registration time.

**Event methods**:

This class implements all abstract methods defined by `TraceExtAgentHandler` (`on_llm_start` / `on_llm_end` / `on_llm_error` / `on_llm_request`, `on_plugin_*`, `on_prompt_*`, `on_chain_*`, `on_retriever_*`, `on_evaluator_*`, `on_workflow_*`). For signatures and trigger timing, see [session module - Tracer extension handlers](../openjiuwen.core/session.README.md#tracer-extension-handlers).

## class openjiuwen.extensions.tracer_otel.OtelWorkflowHandler

```python
class openjiuwen.extensions.tracer_otel.OtelWorkflowHandler(
    otel_tracer: trace.Tracer,
    config: OtelTracerConfig,
    trace_id: str | None = None,
)
```

Workflow-dimension OTel handler, inheriting from `TraceExtWorkflowHandler`. Converts workflow node lifecycle events (`on_call_start` / `on_call_done` / `on_pre_invoke` / `on_invoke` / streaming events / interaction events) into OTel spans.

- LLM component nodes use `SpanKind.CLIENT`; all other components use `SpanKind.INTERNAL`.
- Parent-child relationships between workflow spans are built from `parent_node_id` plus the internal `_component_spans` / `_layer_root_spans` mappings, and support nested sub-workflows.
- Every span receives `end_time` / `elapsed_time` attributes on both the `on_call_done` path and the `on_invoke` error path.

**Parameters**:

- **otel_tracer**(trace.Tracer): OTel Tracer instance returned by `init_otel_tracer`.
- **config**(OtelTracerConfig): Redaction / truncation configuration.
- **trace_id**(str | None): Tracer UUID, same role as in `OtelAgentHandler`.

**Event methods**:

This class implements all abstract methods defined by `TraceExtWorkflowHandler` (`on_call_start` / `on_call_done` / `on_pre_invoke` / `on_pre_stream` / `on_invoke` / `on_post_invoke` / `on_post_stream` / `on_interact`).

## Registration and Usage

OTel handlers are registered globally via `TracerHandlerRegistry`. After registration, every newly created `Tracer` instance (i.e. every `agent.invoke` / `workflow.invoke`) picks them up automatically. The registration name must not collide with the built-in handler names (`tracer_agent` / `tracer_workflow`).

**Example**:

```python
>>> import asyncio
>>> import uuid
>>>
>>> from openjiuwen.core.workflow import Workflow, Start, End, WorkflowComponent
>>> from openjiuwen.core.workflow import create_workflow_session, Input, Output
>>> from openjiuwen.core.workflow.components import Session
>>> from openjiuwen.core.context_engine import ModelContext
>>> from openjiuwen.core.session.tracer import TracerHandlerRegistry
>>> from openjiuwen.extensions.tracer_otel import (
...     OtelTracerConfig,
...     init_otel_tracer,
...     OtelAgentHandler,
...     OtelWorkflowHandler,
... )
>>>
>>> # 1. Configure and initialize the OTel tracer (export to local console for debugging)
>>> config = OtelTracerConfig(
...     exporter_type="console",
...     service_name="my_agent_service",
...     redaction_enabled=False,
... )
>>> otel_tracer = init_otel_tracer(config)
>>>
>>> # 2. Register Agent / Workflow handlers (custom names; must not collide with built-in names)
>>> TracerHandlerRegistry.register_handler("otel_agent", OtelAgentHandler(otel_tracer, config))
>>> TracerHandlerRegistry.register_handler("otel_workflow", OtelWorkflowHandler(otel_tracer, config))
>>>
>>> # 3. Run your Agent / workflow normally; events during execution are exported as OTel spans
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"cmd": "${cmd}"})
>>> flow.set_end_comp("end", End(), inputs_schema={"result": "${start.cmd}"})
>>> flow.add_connection("start", "end")
>>>
>>> session_id = uuid.uuid4().hex
>>> output = asyncio.run(flow.invoke({"cmd": "hello"}, create_workflow_session(session_id=session_id)))
```

> **Note**
>
> - In production, set `exporter_type` to `"otlp"` and provide an `exporter_endpoint` pointing at an OTLP Collector or a backend such as Jaeger.
> - If you also want the built-in `TraceSchema` streaming output (pushing debug trace chunks to the client), keep the `BaseStreamMode.TRACE` streaming mode. The built-in handler and the OTel handler fire in parallel without interfering with each other.
> - `TracerHandlerRegistry.unregister_handler(name)` removes a single handler; `clear()` removes all (commonly used for test isolation).

## OTel Attribute Overview

Attributes written by the OTel handlers follow these naming conventions:

| Namespace | Purpose | Examples |
|---|---|---|
| `gen_ai.*` | GenAI standard semantic conventions, LLM events only | `gen_ai.system`, `gen_ai.prompt`, `gen_ai.completion`, `gen_ai.request.model`, `gen_ai.operation.name`, `gen_ai.tool.name` |
| `openjiuwen.agent.*` | Agent-dimension custom attributes (non-LLM) | `openjiuwen.agent.invoke_type`, `openjiuwen.agent.name`, `openjiuwen.agent.inputs`, `openjiuwen.agent.outputs`, `openjiuwen.agent.error_message` |
| `openjiuwen.workflow.*` | Workflow-dimension custom attributes | `openjiuwen.workflow.id`, `openjiuwen.workflow.component.type`, `openjiuwen.workflow.inputs`, `openjiuwen.workflow.outputs`, `openjiuwen.workflow.invoke_data`, `openjiuwen.workflow.error_message` |
| `openjiuwen.*` | Span base attributes (shared by Agent + Workflow) | `openjiuwen.trace.id`, `openjiuwen.invoke_id`, `openjiuwen.start_time`, `openjiuwen.end_time`, `openjiuwen.elapsed_time`, `openjiuwen.status`, `openjiuwen.error` |

> **Note**
>
> - Before writing `gen_ai.prompt` / `gen_ai.completion`, the handler calls `model_dump()` to normalize message objects such as `BaseMessage` into plain dicts, then serializes them as JSON — this avoids class-repr pollution in backend UIs.
> - Redaction is governed by `OtelTracerConfig`'s `redact_prompts` / `redact_completions` / `redaction_enabled`. When enabled, a SHA-256 hash is written; otherwise values are only truncated according to `max_attr_length`.
