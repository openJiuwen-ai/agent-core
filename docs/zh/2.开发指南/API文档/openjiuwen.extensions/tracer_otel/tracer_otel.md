# openjiuwen.extensions.tracer_otel

`openjiuwen.extensions.tracer_otel` 将框架内置 Tracer 产生的 Agent 与工作流执行事件转换为 OpenTelemetry span，并通过 OTLP（gRPC / HTTP）或 Console 导出。

该模块独立于 `agent_teams.observability`：`init_otel_tracer` 不会调用全局 `trace.set_tracer_provider()`，返回的 `Tracer` 实例直接绑定到本模块创建的 `TracerProvider`，因此可与 `init_observability()` 共存而不冲突。

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

不可变的 OTel tracer 配置（`@dataclass(frozen=True)`）。独立于 `ObservabilityConfig`，不依赖 `agent_teams`。

**参数**：

- **tracer_name**(str)：OTel tracer 名称。默认 `"openjiuwen.tracer.otel"`。
- **exporter_type**(str)：导出器类型，可选 `"otlp"`（默认）或 `"console"`。
- **exporter_endpoint**(str | None)：OTLP 导出端点。`exporter_type="console"` 时无需设置。
- **protocol**(str)：OTLP 传输协议，`"grpc"`（默认）或 `"http"`。仅 `exporter_type="otlp"` 时生效。
- **headers**(dict[str, str])：OTLP 导出器自定义请求头（鉴权等）。默认空。
- **service_name**(str)：OTel Resource 的 `service.name`。默认 `"openjiuwen"`。
- **service_version**(str | None)：OTel Resource 的 `service.version`。默认 `None`（内部记为 `"unknown"`）。
- **sample_rate**(float)：采样概率，范围 `[0.0, 1.0]`。默认 `1.0`（全量）。超出范围会抛出 `ValueError`。
- **schedule_delay_millis**(int)：`BatchSpanProcessor` 导出间隔（毫秒）。默认 `5000`。
- **export_timeout_ms**(int)：`BatchSpanProcessor` 导出超时（毫秒）。默认 `30000`。
- **max_export_batch_size**(int)：`BatchSpanProcessor` 单批最大 span 数。默认 `512`。
- **redaction_enabled**(bool)：脱敏总开关。`True` 时对 prompt / completion 做 SHA-256 哈希。默认 `True`。
- **redact_prompts**(bool | None)：prompt 脱敏细粒度覆盖。`None`（默认）回退到 `redaction_enabled`；`True` / `False` 强制覆盖。
- **redact_completions**(bool | None)：completion 脱敏细粒度覆盖。语义同 `redact_prompts`。
- **max_attr_length**(int)：attribute 字符串截断上限。默认 `4096`。

> **说明**
>
> - `redact_prompts` / `redact_completions` 优先级高于 `redaction_enabled`，便于在保留 completion 明文的同时对 prompt 做哈希（或反之）。

## function openjiuwen.extensions.tracer_otel.init_otel_tracer

```python
openjiuwen.extensions.tracer_otel.init_otel_tracer(config: OtelTracerConfig) -> trace.Tracer
```

根据 `OtelTracerConfig` 创建 `TracerProvider` 并返回 `Tracer` 实例。

- `exporter_type="console"`：使用 `ConsoleSpanExporter` + `SimpleSpanProcessor`（调试用）。
- `exporter_type="otlp"`：使用 `OTLPSpanExporter` + `BatchSpanProcessor`，根据 `protocol` 选择 gRPC 或 HTTP 实现。
- `protocol="http"`：HTTP 导出器要求端点以 `/v1/traces` 结尾，若未提供会自动补全。

**参数**：

- **config**(OtelTracerConfig)：不可变配置实例。

**返回**：

- `opentelemetry.trace.Tracer`，可直接传入 `OtelAgentHandler` / `OtelWorkflowHandler`。

**异常**：

- **JiuWenBaseException**：`exporter_type` 或 `protocol` 取值不合法时抛出（`StatusCode.COMMON_TASK_CONFIG_ERROR`）。具体详细信息和解决方法，参见 [StatusCode](../openjiuwen.core/common/exception/status_code.md)。

> **说明**
>
> - 该函数**不会**调用 `trace.set_tracer_provider()`，返回的 `Tracer` 直接绑定本模块创建的 provider，避免与 `agent_teams.observability.init_observability()` 争夺全局 provider。

## class openjiuwen.extensions.tracer_otel.OtelAgentHandler

```python
class openjiuwen.extensions.tracer_otel.OtelAgentHandler(
    otel_tracer: trace.Tracer,
    config: OtelTracerConfig,
    trace_id: str | None = None,
)
```

Agent 维度的 OTel handler，继承自 `TraceExtAgentHandler`。将 LLM / 插件 / 链 / 检索器 / 评估器等 Agent 事件转换为 OTel span。

- LLM 事件使用 `SpanKind.CLIENT` 与 `gen_ai.*` 标准语义约定；其余事件使用 `SpanKind.INTERNAL` 与 `openjiuwen.agent.*`。
- 每个 handler 方法都有 `try/except` 保护，OTel 异常不会传播到业务流程。

**参数**：

- **otel_tracer**(trace.Tracer)：由 `init_otel_tracer` 返回的 OTel Tracer 实例。
- **config**(OtelTracerConfig)：脱敏 / 截断配置。
- **trace_id**(str | None)：Tracer UUID，用于桥接 OTel trace 与内置 Tracer 的 UUID。`None` 时由 `TracerHandlerRegistry.register_handler` 在注册时自动注入。

**事件方法**：

该类实现 `TraceExtAgentHandler` 定义的全部抽象方法（`on_llm_start` / `on_llm_end` / `on_llm_error` / `on_llm_request`、`on_plugin_*`、`on_prompt_*`、`on_chain_*`、`on_retriever_*`、`on_evaluator_*`、`on_workflow_*`）。方法签名与触发时机详见 [session 模块 - Tracer 扩展 handler](../openjiuwen.core/session.README.md#tracer-扩展-handler)。

## class openjiuwen.extensions.tracer_otel.OtelWorkflowHandler

```python
class openjiuwen.extensions.tracer_otel.OtelWorkflowHandler(
    otel_tracer: trace.Tracer,
    config: OtelTracerConfig,
    trace_id: str | None = None,
)
```

工作流维度的 OTel handler，继承自 `TraceExtWorkflowHandler`。将工作流节点生命周期事件（`on_call_start` / `on_call_done` / `on_pre_invoke` / `on_invoke` / 流式事件 / 交互事件）转换为 OTel span。

- LLM 组件节点使用 `SpanKind.CLIENT`；其余组件使用 `SpanKind.INTERNAL`。
- 通过 `parent_node_id` + 内部 `_component_spans` / `_layer_root_spans` 映射构建工作流 span 的父子链，支持嵌套子工作流。
- 每个 span 在 `on_call_done` 与 `on_invoke` 错误路径都会写入 `end_time` / `elapsed_time`。

**参数**：

- **otel_tracer**(trace.Tracer)：由 `init_otel_tracer` 返回的 OTel Tracer 实例。
- **config**(OtelTracerConfig)：脱敏 / 截断配置。
- **trace_id**(str | None)：Tracer UUID，作用同 `OtelAgentHandler`。

**事件方法**：

该类实现 `TraceExtWorkflowHandler` 定义的全部抽象方法（`on_call_start` / `on_call_done` / `on_pre_invoke` / `on_pre_stream` / `on_invoke` / `on_post_invoke` / `on_post_stream` / `on_interact`）。

## 注册与使用

OTel handler 通过 `TracerHandlerRegistry` 全局注册。注册后所有新建 `Tracer` 实例（即每次 `agent.invoke` / `workflow.invoke`）都会自动加载。注册名不能与内置 handler 名（`tracer_agent` / `tracer_workflow`）重复。

**样例**：

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
>>> # 1. 配置并初始化 OTel tracer（导出到本地 console 用于调试）
>>> config = OtelTracerConfig(
...     exporter_type="console",
...     service_name="my_agent_service",
...     redaction_enabled=False,
... )
>>> otel_tracer = init_otel_tracer(config)
>>>
>>> # 2. 注册 Agent / Workflow handler（注册名自定义，不可与内置名冲突）
>>> TracerHandlerRegistry.register_handler("otel_agent", OtelAgentHandler(otel_tracer, config))
>>> TracerHandlerRegistry.register_handler("otel_workflow", OtelWorkflowHandler(otel_tracer, config))
>>>
>>> # 3. 正常执行 Agent / 工作流，执行期间的事件会以 OTel span 形式导出
>>> flow = Workflow()
>>> flow.set_start_comp("start", Start(), inputs_schema={"cmd": "${cmd}"})
>>> flow.set_end_comp("end", End(), inputs_schema={"result": "${start.cmd}"})
>>> flow.add_connection("start", "end")
>>>
>>> session_id = uuid.uuid4().hex
>>> output = asyncio.run(flow.invoke({"cmd": "hello"}, create_workflow_session(session_id=session_id)))
```

> **说明**
>
> - 生产环境通常将 `exporter_type` 设为 `"otlp"`，并提供 `exporter_endpoint` 指向 OTLP Collector 或 Jaeger 等后端。
> - 若需同时启用内置 `TraceSchema` 流式输出（向客户端推送调试 trace chunk），保持 `BaseStreamMode.TRACE` 流式模式即可，内置 handler 与 OTel handler 会并行触发，互不干扰。
> - `TracerHandlerRegistry.unregister_handler(name)` 可移除单个 handler，`clear()` 可清空全部（常用于测试隔离）。

## OTel Attribute 概览

OTel handler 写入的 attribute 遵循以下命名约定：

| 命名空间 | 用途 | 示例 |
|---|---|---|
| `gen_ai.*` | GenAI 标准语义约定，LLM 事件专用 | `gen_ai.system`、`gen_ai.prompt`、`gen_ai.completion`、`gen_ai.request.model`、`gen_ai.operation.name`、`gen_ai.tool.name` |
| `openjiuwen.agent.*` | Agent 维度自定义属性（非 LLM） | `openjiuwen.agent.invoke_type`、`openjiuwen.agent.name`、`openjiuwen.agent.inputs`、`openjiuwen.agent.outputs`、`openjiuwen.agent.error_message` |
| `openjiuwen.workflow.*` | 工作流维度自定义属性 | `openjiuwen.workflow.id`、`openjiuwen.workflow.component.type`、`openjiuwen.workflow.inputs`、`openjiuwen.workflow.outputs`、`openjiuwen.workflow.invoke_data`、`openjiuwen.workflow.error_message` |
| `openjiuwen.*` | Span 基础属性（Agent + Workflow 共享） | `openjiuwen.trace.id`、`openjiuwen.invoke_id`、`openjiuwen.start_time`、`openjiuwen.end_time`、`openjiuwen.elapsed_time`、`openjiuwen.status`、`openjiuwen.error` |

> **说明**
>
> - `gen_ai.prompt` / `gen_ai.completion` 在写入前会先调用 `model_dump()` 将 `BaseMessage` 等消息对象归一化为纯 dict，再 JSON 序列化，避免类名 repr 污染后端展示。
> - 脱敏策略由 `OtelTracerConfig` 的 `redact_prompts` / `redact_completions` / `redaction_enabled` 控制；启用时写入的是 SHA-256 哈希值，否则仅按 `max_attr_length` 截断。
