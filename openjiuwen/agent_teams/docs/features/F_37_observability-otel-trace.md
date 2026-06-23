# F_37: Observability OTel Trace

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-17 |
| 范围 | `openjiuwen/agent_teams/observability/` |
| Refs | #1013 |

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
| `rail.py` | `before_task_iteration` / `after_task_iteration` | Agent Span + 级联排空 LLM/Tool 子 span + 跨 member ContextVar 清理 |
| `callback_handler.py` | LLM 输入/输出、Tool 开始/结束、Agent invoke 输入 | LLM Span + Tool Span + `_finalize_llm_span_output` 共享输出组装 |
| `monitor_handler.py` | TeamEvent 发布 | Task Span + Member/Message Event Span（**不**关 team span） |
| `span_context.py` | 全局 | ContextVar 存取 + `close_team_agent_spans` + `finalize_trace` + `flush_child_spans` |
| `setup.py` | Runner 初始化 / shutdown | TracerProvider + ActiveSpanTracker + `finalize_team_trace` |
| `semconv.py` | 跨模块引用 | `gen_ai.*`、`agentteam.*`、`langfuse.*` 属性键常量（标准 + `t_` 双写） |
| `config.py` | — | ObservabilityConfig |

## Span 生命周期

两层关闭模型：

| 层级 | 触发点 | 管理对象 |
|------|--------|----------|
| **Agent span 关闭** | `rail.py after_task_iteration` | Agent span + 级联排空所有子 LLM/tool span |
| **Team span 关闭** | `Runner._maybe_finalize_trace()` (finally) | Team span + task span + ActiveSpanTracker 兜底 |

具体生命周期：

| Span 类型 | 创建 | 正常关闭 | 异常/兜底 |
|-----------|------|----------|-----------|
| Team | `Runner._maybe_attach_observability()` | `Runner._maybe_finalize_trace()` (finally) | ActiveSpanTracker (shutdown) |
| Agent | `rail.py` before_task_iteration | `rail.py` after_task_iteration | ① orphan 检测（`before_task_iteration` 中同 member 残留关闭）② ActiveSpanTracker |
| LLM | `callback_handler._open_llm_span()` | `callback_handler._close_llm_span()` / `on_llm_output()` | Agent span 关闭时级联排空 `_llm_span_stack` |
| Tool | `callback_handler.on_tool_call_started()` | `callback_handler.on_tool_call_finished()` / `on_tool_call_error()` | Agent span 关闭时级联排空 `_tool_span_map` |
| Task | `monitor_handler._open_task_span()` | `monitor_handler._close_task_span()` | `monitor_handler.close_team_spans()` / `close_all_spans()` |

关键设计决策：
- **monitor_handler 不关闭 team span**（`_record_team_cleaned` 仅记录事件子 span + 关本 member 的 agent span）。Team span 生命周期由 Runner finally 唯一管理，避免 CLEANED 事件与 finally 竞争导致的提前关闭。
- **Agent span 关闭时级联排空子 span**——遍历 `_tool_span_map` 所有 bucket 和 `_llm_span_stack` 全部条目，`end()` 但不设 `Status(OK)`（保持 UNSET），让仪表盘能一眼识别"被异常兜底关闭的 span"。
- **无有效 parent 时不创建 LLM/tool span**——`_get_parent_context_for_llm_tool()` 在 agent span 和 team span 都不可用时返回 `None`，调用方跳过 span 创建，杜绝孤立 span。
- **Team span output 双路径**：`on_agent_invoke_output` 写 agent.invoke() 的**最终**返回值；`after_task_iteration` 写 leader 每次 iteration 的 per-iteration 结果。最终值以 invoke_output 为准，per-iteration 值为中间快照。

## ContextVar

| ContextVar | 类型 | 用途 | 跨协程传播 | 跨 member 处理 |
|------------|------|------|-----------|---------------|
| `_team_span_ctx` | `Span\|None` | Root span，子 span 的 parent | `copy_context()` | 共享同一 team span 对象（正确行为） |
| `_current_agent_span` | `Span\|None` | 当前 iteration agent span | 否 | `before_task_iteration` 检测跨 member → 只清 ContextVar 不关 span（83943fa50） |
| `_llm_span_stack` | `list[LlmSpanState]` | LLM input/output 匹配 | 否 | 跨 member 时 `set([])` 清理（COW 保证不修改 leader 原列表） |
| `_tool_span_map` | `dict[str, list[Span]]` | Tool input/output 匹配 | 否 | 跨 member 时 `set({})` 清理（COW 保证不修改 leader 原字典） |

跨 member 安全的核心机制：
- **伪共享**：`copy_context()` 浅拷贝容器引用，但所有修改走 `ContextVar.set()`（COW），不原地修改共享对象。
- **清理时机**：`before_task_iteration` 在检测到 `_current_agent_span` 的 `AT_MEMBER_NAME` 与当前 member 不同时，同步清理三个 observability ContextVar 为初始值。

## 属性规范

### LLM Span (`gen_ai.*` 标准语义)

| 属性 | 来源 | 说明 |
|------|------|------|
| `gen_ai.system` | 固定 `"openjiuwen"` | 系统标识 |
| `gen_ai.operation.name` | 固定 `"chat"` | Langfuse GENERATION 推断必需 |
| `gen_ai.provider.name` | kwargs | 模型提供商 |
| `gen_ai.request.model` | kwargs | 模型名称 |
| `gen_ai.request.temperature` | kwargs | 温度参数 |
| `gen_ai.request.message_count` | `len(messages)` | 消息总数 |
| `gen_ai.prompt.{i}.role` | messages | 消息角色（全量） |
| `gen_ai.prompt.{i}.content` | messages | 消息内容（全量） |
| `langfuse.observation.input` | delta：上次 LLM 调用之后新增的消息 | Langfuse UI 输入显示（精简）。首次调用/压缩后展示去 system 全量 |
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
| `gen_ai.request.message_count` | 上一次 LLM 调用的消息数（用于 delta 输入计算，见关键机制） |

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

- **两层关闭模型**：Agent span 关闭（Rail）→ 级联排空 LLM/tool 子 span；Team span 关闭（Runner finally）→ task span + ActiveSpanTracker 兜底。monitor_handler 不参与 team span 生命周期。
- **级联排空**：`after_task_iteration` 和 `close_team_agent_spans` 在关闭 agent span 前遍历 `_tool_span_map` 和 `_llm_span_stack`，`end()` 所有残留子 span。不设 `Status(OK)`（保持 UNSET），以区分"正常关闭"与"异常兜底"。
- **无 parent 不创建**：`_get_parent_context_for_llm_tool()` 在无有效父 span 时返回 `None`，调用方跳过 span 创建，杜绝孤立 span。
- **显式 parent 选择**：LLM/tool span 优先挂 agent span，fallback 到 team span；两阶段都不可用时拒绝创建。
- **跨 member 安全**：`before_task_iteration` 检测到跨 member 的 stale ContextVar 时清理全部 observability ContextVar（不关别人的 span）。COW（写时复制）保证 leader 与 teammate 的 `_llm_span_stack` / `_tool_span_map` 永不互相修改。
- **GEN_AI_PROMPT 双写**：`_open_llm_span` 同时写标准键（`gen_ai.prompt.{i}`）和 Langfuse 命名空间键（`langfuse.gen_ai.prompt.{i}`），前者供通用 OTel 后端，后者供 Langfuse UI 渲染。`GEN_AI_COMPLETION` 同理。相关常量已从 `GEN_AI_T_*` 重命名为 `LANGFUSE_GEN_AI_*`，收束到 semconv.py 的 `langfuse.*` 段。
- **`_finalize_llm_span_output` 共享方法**：`_close_llm_span`（非流式）与 `on_llm_output`（流式）共用同一份 completion/output/ reasoning/close 逻辑，消除 ~130 行重复。
- **ActiveSpanTracker**：OTel SpanProcessor，使用**强引用 set** 追踪所有活跃 span。`on_start` 时添加 → `on_end` 时移除（正常关闭的不堆积）。在 `finalize_trace` 和 `shutdown_observability` 中兜底关闭残留 span。`force_flush` 为 no-op（避免 `close_team_spans` 时误关其他 trace 的 span）。
- **级联关闭 cancelled 标记**：`after_task_iteration` 和 `close_team_agent_spans` 的级联排空逻辑中，已为未正常产出 output 的 LLM/tool span 设置 `langfuse.observation.output = "cancelled"`，便于在 Langfuse 中识别被中断的调用。
- **LLM input delta**：`langfuse.observation.input` 采用基于消息计数的增量展示策略。同一个 agent span 内 LLM 调用是顺序的——将当前消息数 `gen_ai.request.message_count` 写入 agent span 的 attribute，下次 LLM 调用读取上一次的计数，仅展示 `messages[prev_count:]` 的增量消息。首次调用或上下文压缩导致消息数减少时展示去 system 的全量消息，确保 input 始终有值。全量消息通过 `gen_ai.prompt.{i}.*` 保留。
- **team span 创建前清理**：`_maybe_attach_observability` 检测到已结束的 team span 时先 `clear_team_span()` 再创建新的，避免复用已关闭的 span 对象。
