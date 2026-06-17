# F_37: Observability OTel Trace

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-11 |
| 范围 | `openjiuwen/agent_teams/observability/` |
| 关联文档 | `doc/analysis/observability-span-design-v3.md` |

## 设计目标

将 Agent Team 运行期的 span 树通过 OTLP 协议发送到 OpenTelemetry 兼容后端（Langfuse / Grafana / Jaeger / SigNoz 等），用于事后排查和性能分析。

原则：
- 零侵入：observability 模块不修改 team/member/task 的运行逻辑
- 完整 trace 树：team root → agent iteration → LLM / tool / task / event spans
- 标准化属性：LLM/Tool 属性使用 `gen_ai.*` 语义规范，后端无关
- 异常安全：finally 兜底 + ActiveSpanTracker 确保 Span 不泄漏

## Span 树结构

```
team.{team_name}                                    ROOT
├── agent.{member}.task_iteration.{n}               AGENT
│   ├── llm.call                                    GENERATION
│   │   └── llm.reasoning                           SPAN（条件创建）
│   └── tool.{name}                                 TOOL
├── task.{task_id}                                  SPAN
│   ├── task.{task_id}.created
│   ├── task.{task_id}.claimed
│   └── task.{task_id}.completed
├── msg.{from}->{to}                                SPAN (duration≈0)
├── member.{name}.spawned                           SPAN (duration≈0)
└── team.completed                                  SPAN
```

## Observation Type 映射

| Span 名称 | Type | 设置方式 |
|-----------|------|----------|
| `team.{name}` | SPAN | ROOT span |
| `agent.{member}.task_iteration.{n}` | AGENT | `langfuse.observation.type = "agent"` |
| `llm.call` | GENERATION | `gen_ai.operation.name = "chat"` → Langfuse 推断 |
| `tool.{name}` | TOOL | `langfuse.observation.type = "tool"` |
| 其他 | SPAN | — |

## 模块职责

| 文件 | 触发时机 | 管理对象 |
|------|----------|----------|
| `rail.py` | `before_task_iteration` / `after_task_iteration` | Agent Span |
| `callback_handler.py` | LLM 输入/输出、Tool 开始/结束 | LLM Span + Tool Span |
| `monitor_handler.py` | TeamEvent 发布 | Task Span + Member/Message Event Span |
| `span_context.py` | 全局 | ContextVar 存取 + Span 生命周期工具函数 |
| `setup.py` | Runner 初始化 / shutdown | TracerProvider + ActiveSpanTracker |
| `semconv.py` | 跨模块引用 | `gen_ai.*`、`agentteam.*`、`langfuse.*` 属性键常量 |
| `config.py` | — | ObservabilityConfig |

## Span 生命周期

| Span 类型 | 创建 | 关闭 | 兜底 |
|-----------|------|------|------|
| Team | `Runner._maybe_attach_observability()` | `Runner._maybe_finalize_trace()` (finally) | ActiveSpanTracker |
| Agent | `rail.py` before_task_iteration | `rail.py` after_task_iteration | ActiveSpanTracker |
| LLM | `callback_handler._open_llm_span()` | `callback_handler._close_llm_span()` | ActiveSpanTracker |
| Tool | `callback_handler.on_tool_call_started()` | `callback_handler.on_tool_call_finished()` | ActiveSpanTracker |
| Task | `monitor_handler._open_task_span()` | `monitor_handler._close_task_span()` | ActiveSpanTracker |

## ContextVar

| ContextVar | 类型 | 用途 | 跨协程传播 |
|------------|------|------|-----------|
| `_team_span_ctx` | `Span\|None` | Root span，子 span 的 parent | `copy_context()` |
| `_current_agent_span` | `Span\|None` | 当前 iteration agent span | 否（per-worker 独立创建） |
| `_llm_span_stack` | `list[LlmSpanState]` | LLM input/output 匹配 | 否 |
| `_tool_span_map` | `dict[str, list[Span]]` | Tool input/output 匹配 | 否 |

## 属性规范

### LLM Span (`gen_ai.*` 标准语义)

| 属性 | 来源 | 说明 |
|------|------|------|
| `gen_ai.system` | 固定 `"openjiuwen"` | 系统标识 |
| `gen_ai.operation.name` | 固定 `"chat"` | Langfuse GENERATION 推断必需 |
| `gen_ai.provider.name` | kwargs | 模型提供商 |
| `gen_ai.request.model` | kwargs | 模型名称 |
| `gen_ai.request.temperature` | kwargs | 温度参数 |
| `gen_ai.prompt.{i}.role` | messages | 消息角色 |
| `gen_ai.prompt.{i}.content` | messages | 消息内容 |
| `gen_ai.completion.0.role` | 固定 `"assistant"` | 响应角色 |
| `gen_ai.completion.0.content` | response | 响应内容 |
| `gen_ai.usage.input_tokens` | usage | 输入 token |
| `gen_ai.usage.output_tokens` | usage | 输出 token |

### Tool Span

| 属性 | 说明 |
|------|------|
| `langfuse.observation.type` | `"tool"` |
| `gen_ai.tool.name` | 工具名称 |
| `gen_ai.tool.id` | 工具 ID |

### Agent Span

| 属性 | 说明 |
|------|------|
| `langfuse.observation.type` | `"agent"` |
| `agentteam.agent.id` | `{team_name}_{member_name}` |
| `agentteam.agent.name` | member_name |
| `agentteam.agent.role` | leader / teammate |

## 后端兼容性

通过 OTLP 协议 + `gen_ai.*` 标准语义实现后端无关：

```
gen_ai.* 属性 (OTel GenAI Semantic Conventions)
    │ OTLP (gRPC / HTTP)
    ├── Langfuse：原生识别 Agent/Generation/Tool 类型
    ├── Grafana Tempo：gen_ai.* 属性过滤和查询
    └── Jaeger / SigNoz：标准 Span 展示 + gen_ai.* 查询
```

`langfuse.observation.type` 为 Langfuse 扩展属性，不影响其他后端基本功能。

## 配置

```python
class ObservabilityConfig(BaseModel):
    enabled: bool = True
    exporter: Literal["otlp_grpc", "otlp_http", "console"] = "otlp_grpc"
    endpoint: str = "http://localhost:4317"
    sample_rate: float = 1.0
    redact_prompts: bool = False
    redact_completions: bool = False
    attribute_value_max_length: int = 8192
    export_timeout_ms: int = 5000
    # OTLP HTTP 直连时
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
```

## 事件采集

MonitorHandler 通过反射自动采集所有 TeamEvent，无需逐一手动编码：

```python
_ALL_TEAM_EVENT_TYPES = frozenset(
    getattr(TeamEvent, name)
    for name in dir(TeamEvent)
    if not name.startswith("_") and isinstance(getattr(TeamEvent, name), str)
)
```

特殊处理事件（6 类）：CREATED, CLEANED, TEAM_COMPLETED, TASK_*, MEMBER_*, MESSAGE/BROADCAST
其余事件通过 `_record_generic_event()` 自动采集。

## 关键机制

- **finally 兜底**：Team span 关闭 + `flush_child_spans()` 在 Runner finally 中执行
- **ActiveSpanTracker**：OTel SpanProcessor，per-trace 清理 + shutdown 全局兜底
