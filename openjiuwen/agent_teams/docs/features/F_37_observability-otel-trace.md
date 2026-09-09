# F_37: Observability OTel Trace

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-23 |
| 范围 | `openjiuwen/agent_teams/observability/` + `openjiuwen/harness/observability/`（agent 层 span 的归属层） |
| Refs | #1013 |

## 设计目标

将 Agent Team 运行期的 span 树通过 OTLP 协议发送到 OpenTelemetry 兼容后端（Langfuse / Grafana / Jaeger / SigNoz 等），用于事后排查和性能分析。

原则：
- 零侵入：observability 模块不修改 team/member/task 的运行逻辑
- 完整 trace 树：team root → agent iteration → LLM / tool / task / event spans
- 标准化属性：LLM/Tool 属性使用 `gen_ai.*` 语义规范，后端无关
- 异常安全：`finally` 块 + `ActiveSpanTracker` flush 确保 Span 不泄漏
- 并发安全：swarmflow 多 worker 场景下 span 归属精确，无跨 worker/跨 team 窃取

## Span 树结构

```
team.{team_name}                                         ROOT
├── agent.{member}.task_iteration.{n}                    AGENT（Iteration：一次 Agent Loop 控制循环）
│   └── agent.{member}.react_iteration.{n}               AGENT（Step：一次物理模型请求及其工具，条件创建）
│       ├── llm.call                                     GENERATION
│       │   └── llm.reasoning                            SPAN（条件创建：有 reasoning 文本时）
│       ├── tool.{name}                                  TOOL
│       └── agent.{subagent}.invoke                      AGENT（subagent 嵌套）
│           ├── llm.call
│           └── tool.{name}
├── agent.{member}.invoke                                AGENT（单轮 subagent standalone）
│   └── llm.call
├── task.{task_id}                                       SPAN
│   ├── task.{task_id}.created
│   ├── task.{task_id}.claimed
│   ├── task.{task_id}.plan_request                      SPAN（plan 模式，duration≈0）
│   ├── task.{task_id}.plan_response                     SPAN（plan 模式，duration≈0）
│   └── task.{task_id}.completed
├── msg.{from}->{to}                                     SPAN (duration≈0)
├── member.{name}.spawned                                SPAN (duration≈0)
└── team.completed                                       SPAN
```

所有 member（leader、teammate、swarmflow worker）的 span 结构一致，统一为 `agent.{member_name}.task_iteration.{n}`。

**agent 层自身是嵌套的**：`react_iteration` Step 由 `before_model_call` 在 `ctx.inputs.react_iteration > 0` 时开启、`after_react_iteration` 关闭，代表一次物理模型请求连同它触发的工具，挂在同轮的 `task_iteration` 之下。因此"agent span 的父节点必然是 team span"不成立——成立的是「每条 agent 父链只经过 agent span，最终落到 team span」。`react_iteration` 缺席时（单轮 invoke 路径、iteration 未编号）`llm.call` / `tool.*` 直接挂在外层 agent span 下。

**Swarmflow 并发 worker**：每个 worker 在自己的 asyncio task 中运行，共享同一个 trace。
Worker 的 `agent.wf-worker-*` span 直接挂在 `team.{name}` 下，其 `llm.call` / `tool.*` 子 span 通过 OTel parent span identity（而非 asyncio task id）与父 agent span 关联。
多个 worker 的 LLM 回调可能 fire 在不同的 asyncio task 上（stream forwarding），
`ActiveSpanTracker` 通过 parent span identity 匹配精确定位到正确的 llm.call span。

## Observation Type 映射

| Span 名称 | Type | 设置方式 |
|-----------|------|----------|
| `team.{name}` | SPAN | ROOT span |
| `agent.{member}.task_iteration.{n}` | AGENT | `langfuse.observation.type = "agent"` |
| `agent.{member}.react_iteration.{n}` | AGENT | `langfuse.observation.type = "agent"`；另带 `openjiuwen.trajectory.record.kind = "step"` + `openjiuwen.step.id` / `openjiuwen.step.number` |
| `llm.call` | GENERATION | `gen_ai.operation.name = "chat"` → Langfuse 推断 |
| `llm.reasoning` | SPAN | 挂在 `llm.call` 下，`gen_ai.completion.0.is_reasoning = True` |
| `tool.{name}` | TOOL | `langfuse.observation.type = "tool"` |
| 其他 | SPAN | — |

## 核心类

| 类 | 解决什么问题 |
|----|-------------|
| `AgentObservabilityRail`（harness） | agent span 的创建和关闭，与 team 无关：`before_task_iteration` 创建 agent span，`after_task_iteration` 关闭并清理其下所有子 span。单 agent / team 成员 / subagent 用的是同一个实现 |
| `TeamObservabilityRail`（agent_teams） | team 专有增量：把 `agentteam.*` 身份块作为 `AgentSpanDecoration` 停放到回调上下文，由 agent rail 在开 span 时套用；leader 轮次结果打到 team root span 上。**不继承 agent rail，也不重开 span** |
| `AgentSpanDecoration` | 两个 rail 之间的交接契约：`attributes` 开 span 时套用，`input/output_attribute_keys` 指定把脱敏后的 query/result 再镜像到哪些键。停放在 `ctx.extra`（同一 hook 链与同一轮 before/after 共享同一个 ctx），因此不需要用 ContextVar 猜 span 归属 |
| `AgentSpanScope` | agent span 的生命周期封装。`close()` 时统一做四件事：设 output → 清子 span → `span.end()` → 恢复父 agent span |
| `OtelCallbackHandler` | LLM 和 tool span 的创建和关闭。响应回调框架的 LLM/Tool 事件，开/关对应的 OTel span |
| `LlmSpanState` | LLM span 的运行时状态（启动时间、是否流式、首个 chunk 到达时间）。附着在 OTel Span 对象上，不受 asyncio task 切换影响 |
| `ActiveSpanTracker` | OTel `SpanProcessor`，按 `trace_id` 索引所有活跃 span。在 trace 内按 `parent_span_id` 查找当前 `llm.call` span。`flush_spans_for_trace` 和 `flush_all_spans` 在 team 结束和 shutdown 时清理残留 span |

## 核心流程

agent rail 和 `OtelCallbackHandler` 通过两套回调独立工作，二者共享 OTel Span 对象。

0. **team 增量停放**：`TeamObservabilityRail.before_task_iteration`（priority 12，先跑）把 `agentteam.*` 停放到 `ctx.extra`
1. **agent span 打开**：`AgentObservabilityRail.before_task_iteration`（priority 10）创建 agent span，套用停放的增量，存入 ContextVar
2. **llm span 打开**：LLM 回调触发 → 创建 llm.call span，挂在当前 agent span 下。同时创建 `LlmSpanState` 附着在 span 上记录计时信息
3. **流式 chunk**：每次 chunk 回调 → 在 trace 内按 parent span_id 查找当前 llm span → 记录事件和 timing
4. **llm span 关闭**：LLM 结束回调 → 查找 span → 设 completion、usage 等属性 → `span.end()`
5. **agent span 关闭**：`AgentSpanScope.close()` → 关闭未完成的 tool span 和 llm span → 关闭 agent span → 恢复父 agent span 为当前

并发 worker 场景下，在第 3/4 步查找 span 时，通过 `span.parent.span_id` 精确匹配——每个 worker 只找到自己的 span。

并发 worker 场景下，`ActiveSpanTracker` 在 trace 内所有 recording 的 `llm.call` span 中，逐一比对 `span.parent.span_id`，返回 parent 匹配当前 agent span 的 span。每个 worker 的 llm span 只匹配其所属的 agent span，不会误取其他 worker 的 span。

## 状态存储一览

| 存储位置                                       | 内容                                        | 生命周期                                                      |
| ------------------------------------------ | ----------------------------------------- | --------------------------------------------------------- |
| `_team_span_ctx` (ContextVar)              | 当前 team root Span                         | team 创建 → `finalize_trace` 清除                             |
| `_current_agent_span` (ContextVar)         | 当前 iteration/invoke agent Span            | `before_task_iteration` 设置 → `AgentSpanScope.close` 恢复/清除 |
| `_tool_span_map` (ContextVar)              | `dict[str, list[Span]]`，按 `tool_name` 索引 tool span | Tool 调用可嵌套（`task_tool` 触发子流程再调 `bash`），同一时刻可能有多个不同 tool_name 的 recording span。`dict` 按名称区分，`list` 处理同名称多次调用的栈行为。`TOOL_CALL_STARTED` 触发时 push，`TOOL_CALL_FINISHED` 或 `TOOL_CALL_ERROR` 触发时 pop。agent span 关闭时清理未 pop 的残留 |
| llm.call span 对象上的 `_llm_state`            | 该 LLM span 的运行时状态（`LlmSpanState`—计时、流式标记） | LLM span 创建时附着 → `span.end()` 后随 Span 销毁             |
| `ActiveSpanTracker._spans_by_trace` (dict) | 按 trace_id 索引的所有活跃 Span 集合                | `on_start` 添加 → `on_end` 或 flush 移除                       |


## 公开接口

| 模块 | 对外暴露 | 用途 |
|------|---------|------|
| `__init__.py` | `init_observability(config)` | 启动 TracerProvider + 注册全部回调 |
| | `shutdown_observability()` | 注销回调 + flush 残留 span + 关闭 TracerProvider |
| | `TeamObservabilityRail` | DeepAgent rail，给 agent span 补 team 身份 |
| | `maybe_observability_rails()` | team 成员要挂的两个 rail（team 增量 + harness agent span），挂载点只认这一个入口 |
| | `ObservabilityConfig` | 配置：exporter、端点、采样率、脱敏、attribute 上限等 |
| | `attach_to_team_agent(agent)` | 在 TeamAgent 上注册 team 事件监听 |
| | `finalize_team_trace(team_name)` | Runner 结束时按 team 关 trace |
| `span_context.py` | `LlmSpanState` | LLM span 的运行时状态 |
| | `ActiveSpanTracker` | OTel SpanProcessor，索引所有活跃 span |
| `semconv.py` | `GEN_AI_*` / `AT_*` / `LANGFUSE_*` | 标准化属性键常量 |

## Span 生命周期

两层关闭模型：

| 层级 | 触发点 | 管理对象 |
|------|--------|----------|
| **Agent span 关闭** | `AgentObservabilityRail.after_task_iteration` / `after_invoke` | Agent span + 关闭其下所有子 LLM/tool span |
| **Team span 关闭** | `Runner._maybe_finalize_trace()` (finally) | Team span + task span + `ActiveSpanTracker` 按 `trace_id` flush |

具体生命周期：

| Span 类型 | 创建 | 正常关闭 | 异常关闭 |
|-----------|------|----------|----------|
| Team | `Runner._maybe_attach_observability()` | `finalize_trace()` (finally) | `ActiveSpanTracker` (shutdown) |
| Agent（iteration） | `AgentObservabilityRail.before_task_iteration` | `AgentObservabilityRail.after_task_iteration` → `AgentSpanScope.close()` | `AgentSpanScope.close()` + `ActiveSpanTracker` |
| Agent（invoke，单轮 subagent） | `AgentObservabilityRail.before_invoke` | `AgentObservabilityRail.after_invoke` → `AgentSpanScope.close()` | 父 agent span 关闭时清理 |
| LLM | LLM invoke 回调 | `OtelCallbackHandler.on_llm_output()` / `on_llm_invoke_output()` | agent span 关闭时按 parent span_id 清理 + trace 级 flush |
| Tool | `OtelCallbackHandler.on_tool_call_started()` | `OtelCallbackHandler.on_tool_call_finished()` / `on_tool_call_error()` | agent span 关闭时清理 |
| Task | `monitor_handler._open_task_span()` | `monitor_handler._close_task_span()` | `monitor_handler.close_team_spans()` / `close_all_spans()` |

agent span 关闭时，同时关闭其下所有未完成的子 span（tool span 和 llm span）。按 `parent_span_id` 精确关闭，只关属于自己的，不影响其他并发 worker。

关键设计决策：
- **monitor_handler 不关闭 team span**（同前）。
- **`AgentSpanScope` 管 agent span 完整生命周期**：`before_*` 构造 scope 存入 `ctx.extra`，`close()` 统一设 output+status+cascade+end+恢复父 current。`is_outermost` 控制是否级联排空：iteration 或独立 invoke 做，嵌套 subagent 不做（父 scope 负责）。
- **agent 层与 team 层分成两个独立 rail，不用继承**：agent span 的开关、嵌套判据、孤儿排空对单 agent 和 team 成员完全一致，之前只有一份实现放在 team 包里，单 agent 复用它要靠猴补丁抹掉 `agentteam.*` 并伪造一个合成 team_name。现在 agent 层归 `harness/observability/rail.py`，team 层只提供 `AgentSpanDecoration`。可行的前提是 priority（高者先跑，`after_*` 同序）加上 before/after 共享同一个 `ctx`：team rail priority 12 先停放增量，agent rail priority 10 开 span 时套用；输出镜像键随 scope 走到 `close()`，所以 team 不需要在 span 关掉后再补属性。挂载由 `core.observability`（harness 内置）+ `core.team.observability` 成对声明。
- **Subagent 覆盖**：`subagent_elements.py` 在 team 侧 subagent 工厂只注入 agent rail，**不注入 team rail**——subagent 是被派发的工作而不是 member，没有 member id / role / 信箱，给它的 span 挂 `agentteam.*` 等于声称一个它没有的身份（member name 会变成 subagent 类型名）。归属靠结构：subagent span 嵌在派发它的 member span 下，team 身份在父级。`before_invoke` 通过 `enable_task_loop` 区分多轮 member（跳过）和单轮 subagent（开 invoke span）。nesting 判据为结构性 `current_agent_span.is_recording()`——不依赖枚举或 member_name。
- **无有效 parent 时不创建 LLM/tool span**：`_get_parent_context_for_llm_tool()` 在无有效父 span 时返回 `None`，调用方跳过 span 创建，杜绝孤立 span。
- **Team span output 双路径**：`on_agent_invoke_output`（正常）+ `after_task_iteration`（leader 兜底）。
- **事件 span 的 input/output 按语义拆分**（`_event_span_io`）：task 事件 input={task_id}/output=结果；plan_request input=完整 payload/output={plan_id,status}；plan_response input={plan_id}/output={approved,feedback}；member.status_changed input={old_status}/output={new_status}；message 只放路由信息；generic/通知类 payload→input 只放一次不重复。
- **Tool span input 保留原始 (args, kwargs) 结构**：`_serialize_tool_inputs` 原样序列化，仅通过 `_sanitize` 将 Session 对象转为 `"session:<id>"` 字符串。
- **Plan-mode task span 状态**：`TASK_PLAN_REQUEST`/`TASK_PLAN_RESPONSE` 加入 `_TASK_EVENT_TYPES`，从 payload `status` 推导有效状态（claimed/plan_approved）。
- **LLM span output 组装**：非流式和流式共用同一份逻辑（completion、reasoning、usage），消除重复。reasoning 子 span 在 llm.call span 安全关闭后创建，不影响主 span。
- **ORPHAN error log**：级联关闭和 flush 路径上未正常关闭的 span 触发 `team_logger.error("ORPHAN ...")`，不静默 stamp，确保问题可见。

## ContextVar

| ContextVar            | 类型                      | 用途                                                                        |
| --------------------- | ----------------------- | ------------------------------------------------------------------------- |
| `_team_span_ctx`      | `Span\|None`            | Team root span，子 span 的 parent；LLM span 查找时用来获取 trace_id              |
| `_current_agent_span` | `Span\|None`            | 当前 iteration/invoke agent span；LLM span 查找时用来获取 parent_span_id 做精确匹配 |
| `_tool_span_map`      | `dict[str, list[Span]]` | Tool span 按 tool_name 存取                                                  |

跨 member 安全：`before_task_iteration` 检测跨 member 时，清理 `_current_agent_span` 和 `_tool_span_map`。`_team_span_ctx` 在同一 team 内共享。

LLM span state（`LlmSpanState`）附着在 OTel Span 对象上，不受 asyncio task 切换影响。

## 属性规范

### LLM Span (`gen_ai.*` 标准语义)

| 属性 | 来源 | 说明 |
|------|------|------|
| `gen_ai.system` | 固定 `"openjiuwen"` | 系统标识 |
| `gen_ai.operation.name` | 固定 `"chat"` | Langfuse GENERATION 推断必需 |
| `gen_ai.provider.name` | kwargs | 模型提供商 |
| `gen_ai.request.model` | kwargs | 模型名称 |
| `gen_ai.request.temperature` | kwargs | 温度参数 |
| `gen_ai.request.message_count` | `len(messages)` | 当前调用的消息总数 |
| `gen_ai.request.prev_message_count.{agent_id}` | 上一次消息数（存 team span 上） | 跨 iteration delta 追踪键（agent_id = `{team}_{member}`） |
| `gen_ai.prompt.{i}.role` | messages（delta） | 消息角色。langfuse backend 下不写，仅写 `langfuse.*` 版本 |
| `gen_ai.prompt.{i}.content` | messages（delta） | 消息内容。langfuse backend 下不写 |
| `langfuse.gen_ai.prompt.{i}.role` | messages（delta） | langfuse UI prompt 渲染（永远写） |
| `langfuse.gen_ai.prompt.{i}.content` | messages（delta） | langfuse UI prompt 渲染（永远写） |
| `langfuse.observation.input` | delta 消息 JSON | Langfuse UI 输入显示。首次调用/压缩后展示去 system 全量；后续仅新增消息 |
| `gen_ai.completion.0.role` | 固定 `"assistant"` | 响应角色。langfuse backend 下不写 |
| `gen_ai.completion.0.content` | response | 响应内容。langfuse backend 下不写 |
| `langfuse.gen_ai.completion.0.role` | 固定 `"assistant"` | langfuse UI completion 渲染 |
| `langfuse.gen_ai.completion.0.content` | response | langfuse UI completion 渲染 |
| `langfuse.observation.output` | 完整 choices JSON（含 usage） | langfuse UI 输出 |
| `gen_ai.tool.definitions` | tools | 工具定义 JSON |
| `gen_ai.tool_calls` | response.tool_calls | 工具调用 JSON |
| `gen_ai.usage.prompt_tokens` | usage（`input_tokens`） | 输入 token。**langfuse backend 下扣除缓存命中部分**，见下方 Usage 采集 |
| `gen_ai.usage.completion_tokens` | usage（`output_tokens`） | 输出 token。**langfuse backend 下扣除推理部分** |
| `gen_ai.usage.total_tokens` | usage（`total_tokens`） | 总 token |
| `gen_ai.usage.reasoning_tokens` | usage（`reasoning_tokens`） | 推理 token，取业务层提取值，采集层不自行计算 |
| `gen_ai.usage.input_tokens` | usage（`input_tokens`） | provider 原始输入计数，不参与 carve-out |
| `gen_ai.usage.output_tokens` | usage（`output_tokens`） | provider 原始输出计数，不参与 carve-out |
| `gen_ai.usage.reasoning.output_tokens` | usage（`reasoning_tokens`） | 推理 token 的标准 GenAI 键，不参与 carve-out |
| `gen_ai.usage.cache_read.input_tokens` | usage（`cache_read_tokens`） | 缓存命中 token；provider 未返回（字段为 `None`）则不设 |
| `gen_ai.usage.cache_creation.input_tokens` | usage（`cache_write_tokens`） | 缓存写入 token；provider 未返回（字段为 `None`）则不设 |
| `gen_ai.response.time_to_first_token_ms` | `first_chunk_ns - start_ns` | 流式首 chunk 到达耗时（仅流式） |
| `gen_ai.response.finish_reason` | response | 结束原因（每个 chunk 的 response 对象携带） |
| `gen_ai.response.model` | usage（`model_name`） | 实际响应模型 |

### Prompt Delta 机制

基于消息计数的增量展示策略：
- 上一次 LLM 调用的 `message_count` 存在 **team span** 上（键 = `gen_ai.request.prev_message_count.{agent_id}`），因为每个 iteration 会开/关 agent span——存 agent span 上会丢失。
- 首次调用（prev_count == 0）：emit 全部非 system 消息
- 上下文压缩（当前 count < 上一次）：emit 全部非 system 消息
- 后续调用（当前 count > 上一次）：仅 emit `messages[prev_count:]` 的增量消息
- System 消息永远 emit（不受 delta 约束）
- `langfuse.observation.input` 使用相同的 delta 逻辑，首次调用去 system 消息

### `max_attributes` 尾部裁剪

OTel `BoundedAttributes` 使用 FIFO 驱逐（最先驱逐最早写入的 attribute）。为防止 prompt 属性填满 budget 后驱逐顶层的 `gen_ai.system` / `operation.name` 等关键属性：

- `max_attributes = 200`（span 级别全局上限）
- 预留 ~30 个非 prompt 属性槽位（系统属性 + request 参数 + team context + member name + output 阶段 completion/usage/finish_reason）
- 仅写入尾部 N 条非 system delta 消息（`N = (200 - 30) // attrs_per_msg`）
- langfuse backend：`attrs_per_msg = 2`（仅 langfuse.*），`N ≈ 85`
- 标准 backend：`attrs_per_msg = 4`（langfuse.* + gen_ai.*），`N ≈ 42`

### Langfuse Backend Dedup

当 `backend = "langfuse"` 时：
- 标准 `gen_ai.prompt.{i}.*` 和 `gen_ai.completion.0.*` 属性**不写**（避免重复）
- 仅写 `langfuse.gen_ai.prompt.{i}.*` 和 `langfuse.gen_ai.completion.0.*`
- `langfuse.observation.input` / `langfuse.observation.output` 永远写
- `gen_ai.request.*` 不受影响；`gen_ai.usage.*` 的键名不受影响，但 `prompt_tokens` / `completion_tokens` 的**取值**会按下方 carve-out 扣减

### Usage 采集

- 直接从 `usage_metadata` 对象 dump 完整 usage 到 `langfuse.observation.output` 的 JSON 中
- `model_dump()` 整个 usage 对象（不逐字段过滤），确保 `cache_read_tokens` / `reasoning_tokens` 及未来新字段自动流通
- 缓存 token 只认标准字段 `cache_read_tokens` / `cache_write_tokens`（映射到 `gen_ai.usage.cache_read.input_tokens` / `gen_ai.usage.cache_creation.input_tokens`）。legacy 的 `usage.cache_tokens` 与 `gen_ai.usage.cache_tokens` 在 LLM 主路径**不再读也不再写**——`semconv.GEN_AI_USAGE_CACHE_TOKENS` 常量仅剩 codex bridge（`observability/codex/bridge.py`，数据源是 rollout 的 `cached_input_tokens`）使用
- `_record_usage_attrs` 支持 `skip_existing=True`（流式回调中 usage 可能被 chunk 级别的 `_maybe_record_response_attrs` 先写入）

#### Langfuse carve-out：token 子集扣减

缓存命中与推理 token 是 provider 报的 prompt / completion 计数的**子集**，不是额外 token。Langfuse 把每个 `gen_ai.usage.*` 键当作独立的累加类别（按 observation 和 trace 分别求和），于是缓存前缀被重复计一次——长会话里大部分 prompt 都是缓存命中，trace 总量会虚高一半以上。

因此 `backend = "langfuse"` 时，子集从各自的父计数中扣除，使这几个键互斥且加总等于 provider 报的 total：

- `prompt_tokens = input_tokens - cache_read_tokens`（新处理的 prompt）
- `completion_tokens = output_tokens - reasoning_tokens`（可见输出）

子集大于父计数时（provider 把 reasoning 记在 completion 之外）**跳过扣减**，保留原始数字——扣出来的是编造的数，不是测量值。

其余 backend 不做任何扣减：`gen_ai.usage.prompt_tokens` 保持 semconv 语义（全部输入 token）。`gen_ai.usage.input_tokens` / `output_tokens` / `reasoning.output_tokens` 这组 additive 键**永远**写 provider 原值，不继承 carve-out。

### Reasoning Span (`llm.reasoning`，条件创建)

仅当本次调用产出 reasoning 文本时创建，挂在 `llm.call` 下。reasoning 内容取自流式收尾 trigger 传入的 `reasoning_content`（`final_message.reasoning_content`，业务层已拼好完整文本），采集层不自行拼接 chunk。

| 属性 | 来源 | 说明 |
|------|------|------|
| `gen_ai.completion.0.role` | 固定 `"reasoning"` | Langfuse reasoning 渲染 |
| `gen_ai.completion.0.is_reasoning` | 固定 `True` | 标识为推理 |
| `gen_ai.completion.0.content` | reasoning_content | 完整推理文本（脱敏） |
| `langfuse.observation.input` | 固定 `"llm reasoning"` | UI 输入占位 |
| `langfuse.observation.output` | reasoning_content | UI 输出 |
| `gen_ai.usage.reasoning_tokens` | usage | 镜像一份到 reasoning span（llm.call 上也有） |
| `gen_ai.reasoning.duration_ms` | `reasoning_last_ns - reasoning_first_ns` | 推理 chunk 区间耗时；非流式不设 |
| `agentteam.member.name` | 从当前 agent span 读取 | 创建子 span 时写入 |

**计时锚点（关键）**：reasoning span 不用 `with start_as_current_span`（with 体瞬间执行，`__exit__` 立即 `end()` → duration 恒 0）。改为手动 `start_span(start_time=reasoning_start_wall_ns)` + `end(end_time=reasoning_start_wall_ns + dur_ns)`，让 span 自身 start/end 落在真实推理区间，Langfuse UI 显示的 span duration 等于 `duration_ms`。
- `reasoning_first_ns` / `reasoning_last_ns`：单调钟读数（`time.monotonic_ns()`），首个/末个含 `reasoning_content` 的 stream chunk 到达时记于 `on_llm_stream_output`。两者相减得到 reasoning 时长（`dur_ns`）。
- `reasoning_start_wall_ns`：墙上钟读数（`time.time_ns()`），与 `reasoning_first_ns` 同时记，作为 span 的 `start_time`。单调钟差值只能算时长，不能当时间戳（基准与 trace 内其他 span 不一致），所以 span 的绝对时间用墙上钟，时长用单调钟差值，`end_time = start_time + dur_ns`。
- finalize 远晚于末个 reasoning chunk，若不显式传 `start_time`，SDK 默认 start 会超过算出的 end_time，duration 被 clamp 成 0，故必须显式传 `start_time`。
- **固有局限**：reasoning 计时只覆盖"含 reasoning_content 的 chunk 区间"，漏掉首 token 之前 provider 侧的思考时间（流式协议不给 per-token 时间戳，provider 思考发生在 TTFT 期间）。非流式路径无 chunk 级时间，不设 `duration_ms`，duration 留 0。

### Child Span 属性下传

每个 llm.call / tool / reasoning span 在创建时从当前 agent span 读取 `agentteam.member.name` 并写入自身，确保子 span 可追溯到所属 member。

DeepAgent 初始化阶段的 ImageModalityProbe 调用发生在 agent span 创建之前，此时无 agent span 可用。该场景属正常流程，跳过属性下传并记录 warning。

### Tool Span

| 属性 | 说明 |
|------|------|
| `langfuse.observation.type` | `"tool"` |
| `gen_ai.tool.name` | 工具名称 |
| `gen_ai.tool.id` | 工具 ID |
| `gen_ai.tool.input` | 工具输入参数（JSON，脱敏） |
| `gen_ai.tool.output` | 工具输出（JSON，脱敏） |
| `langfuse.observation.input` | 同 tool input |
| `langfuse.observation.output` | 同 tool output |
| `agentteam.member.name` | 从 agent span 下传 |

### Agent Span

| 属性 | 说明 |
|------|------|
| `langfuse.observation.type` | `"agent"` |
| `agentteam.agent.id` | `{team_name}_{member_name}` |
| `agentteam.agent.name` | member_name |
| `agentteam.agent.role` | 成员角色值（`TeamRole.value`，如 `leader` / `teammate` / `human_agent` / `external_cli`） |
| `agentteam.member.id` | member_name |
| `agentteam.member.name` | member_name |
| `agentteam.team.id` | team_name |
| `agentteam.session.id` | session_id |
| `deepagent.task.iteration` | 当前 iteration 序号 |
| `deepagent.task.is_follow_up` | 是否为 follow-up 任务 |
| `langfuse.observation.input` | agent 输入 query（脱敏） |
| `langfuse.observation.output` | agent 输出（脱敏） |

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
    service_name: str = "openjiuwen-agent-teams"
    exporter: Literal["otlp_grpc", "otlp_http", "console", "file"] = "otlp_grpc"
    endpoint: str = "http://localhost:4317"
    sample_rate: float = 1.0
    redact_prompts: bool = False
    redact_completions: bool = False
    attribute_value_max_length: int = 40960          # Langfuse 推荐值
    max_attributes: int = 200                         # Span 级属性上限（FIFO 驱逐）
    backend: Literal["langfuse", "otlp"] = "langfuse" # "langfuse" 时去重标准 gen_ai.prompt/completion
    export_timeout_ms: int = 5000
    # Langfuse 认证
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # file exporter
    traces_dir: str = "./traces"
    file_retention_days: int = 7
```

## 关键机制

### 每个 span 挂在哪个 parent 下

- 有 agent span 时（正常 iteration/invoke）：LLM/tool span 挂在 agent span 下
- 无 agent span 时（ImageModalityProbe 等初始化阶段调用）：LLM span 挂在 team span 下
- 两者都不可用：不创建 span，避免孤立

### 关闭回调如何找到要关的 span

LLM 回调（`OtelCallbackHandler.on_llm_output` 等）的参数中没有 span 引用。`ActiveSpanTracker` 在 trace 内所有 recording 的 `llm.call` span 中，匹配 `span.parent.span_id` 与当前 agent span_id 相等者。

并发 worker 各有一个 agent span，各自的 `llm.call` span 的 parent 不同，不会相互干扰。流式回调跨 asyncio task 导致 agent span ContextVar 不匹配时，降级为取 trace 内最近打开的 recording `llm.call` span。

### 正常关闭 vs 异常清理

正常路径：LLM 回调触发 → 定位 span → 写入 output、usage 等属性 → `span.end()`。关闭后从 `ActiveSpanTracker` 中移除，不累积。

异常路径：agent span 关闭时清理其下残留子 span，输出 ORPHAN error log。team 结束时按 `trace_id` flush。`shutdown_observability` 时清理全部。

### `LlmSpanState` 的存储位置

`LlmSpanState` 是 LLM span 的运行时状态（开始时间、是否流式、首个 chunk 到达时间）。附着在 OTel Span 对象上，不依赖 ContextVar。ContextVar 在流式 generator 的 yield/resume 切换 asyncio task 时会丢失值，Span 是普通 Python 对象，不受此影响。

### LLM span output 组装

非流式和流式两套关闭流程共用同一份 output 组装逻辑（completion、reasoning、usage、close），避免重复。reasoning 子 span 在 llm.call span 安全关闭后条件创建，不影响主 span 生命周期。

### 并发 worker 的 span 归属

同一 team 内的多个 member（leader、teammate、swarmflow worker）共享同一个 trace。
每个 member 运行在独立的 asyncio task 中，各自持有自己的 `_current_agent_span` ContextVar——清理自己的不影响其他 member。

新 member 启动时，`asyncio.create_task` 会复制创建者的 contextvars Context。`before_task_iteration` 检测到继承来的 ContextVar 属于另一个 member 时，清理掉（只清理自己的 task 内的 ContextVar），让新 member 从干净状态开始。

### 跨 team 隔离

不同 team 使用不同 trace。`finalize_trace` 在关 team span 前捕获 trace_id，flush 时只清当前 trace 的 span，不碰其他 team。

### Prompt 属性优化

- **Delta**：同一个 agent 的连续 LLM 调用，消息计数存 team span 上（跨 iteration 保持），后续调用只写新增消息的属性。
- **尾部裁剪**：`max_attributes=200` 上限下，仅写尾部 N 条非 system 消息，保留 ~30 个槽位给系统属性和 output 属性。
- **Langfuse dedup**：`backend="langfuse"` 时跳过标准 `gen_ai.prompt`/`gen_ai.completion`，只写 `langfuse.*` 版本，避免重复。
