# F_112 三方 harness 成员的轨迹观测：协议事件驱动

日期：2026-09-17

## 背景

集群模式下，Claude Code / Codex 外部成员在轨迹 UI 上完全不显示。排查确认三层问题：

1. **写入侧没产出**：team 层的 `ClaudeSpanBridge` / `CodexSpanBridge` 每轮通过 `get_team_span()`
   取 team 根 span 作为父。该 span 只绑在 leader 的 ContextVar，且每次 leader run 结束都会换新；成员
   pump 任务要么找不到，要么拿到已结束的 span，整轮直接跳过。
2. **归属错误**：bridge span 不带 `openjiuwen.execution.subject.*`，入库 subject 默认 `main`，team 视图
   只保留 `team_leader` / `team_member` lane，全部被过滤。
3. **记录形态不对**：模型调用 span 没有 `record_kind=inference`；Claude 把内容写在
   `openjiuwen.span.input/output`（viewer 不读）；tool span 时长≈0；Claude team MCP 工具重复出 span。

根因是分层错位：team 层直接耦合厂商细节——`cli_agent/spawn.py` 替 provider 接 Claude OTel env /
settings 与 Codex rollout 目录 / otel overrides，`cli_agent/codex/observer.py` 解析 Codex 通知
method，`CodexHarness(notification_observer=)` 把原始 SDK 通知漏给宿主，bridge 按鸭子类型分派。

## 决策

**team 层只适配 `HarnessProtocol` 事件；各 harness 的数据差异全部收进 provider。**

```
harness_protocol   ModelRequestEvent + HostCapability.MODEL_REQUEST_OBSERVATION（纯契约）
harness_providers
  ├─ telemetry/otlp_receiver.py   进程级 loopback OTLP 接收器（原 agent_teams/observability/shared_otlp.py）
  ├─ claudecode/observation.py    Claude 请求日志（api_request_body / api_response_body）→ ModelRequestEvent
  ├─ codex/observation.py         Codex rollout trace + raw events → ModelRequestEvent
  └─ trajectory.py                HarnessTrajectoryRecorder：协议事件 → trajectory span（宿主胶水）
agent_teams        ExternalHarnessMemberRuntime 只绑定 recorder 并注入成员 turn 身份；bridge 全部删除
```

### 1. 协议：`ModelRequestEvent`

一次物理模型请求一个事件，请求结束后发出，字段见 `harness_protocol/events.py`：`request_id`、
`status`、`started_at` / `ended_at`、`model` / `provider_name`、`system_instructions`、
`input_messages`（`TurnMessage`，`message_id` 在消息留在对话中时保持稳定）、`input_observed`、
`output_message`、`tool_definitions`（统一为 `{name, description, parameters}`）、`request_parameters`
（GenAI 名字的采样参数）、`response_id`、`time_to_first_chunk`、`finish_reasons`、`usage`（本次请求，按 GenAI 约定
`input_tokens` 为整个 prompt、缓存命中是其中的细分）、`error`、命名空间化 `data`。复用
`TurnMessage` / `ContentBlock`（`text` / `reasoning` / `tool_call{name,arguments}` /
`tool_result`，`data.call_id`），不新建消息模型。

provider 仅在宿主声明 `HostCapability.MODEL_REQUEST_OBSERVATION` 时开启厂商侧通道，并保证：

- 同一 turn 内，请求事件先于它引发的 tool item，也先于 turn 终止事件；
- tool item 的 envelope `causation_ids` 带上引发它的 `request_id`；
- 通道超时或不可用时仍然发出（从 SDK 流降级，`input_observed=False`），不漏报。

tool item 的 COMPLETED data 统一带 `is_error`（Codex 补齐）。`SerializedTurnHarness._emit` 新增
`causation_ids` 与 `timestamp`：被暂缓发出的 tool item 保留真实观测时间。

### 2. provider 内部

**Claude Code**（`claudecode/observation.py`，`ClaudeRequestObserver`）

- 内容来自 Claude Code 的原始 API body 日志：`OTEL_LOG_RAW_API_BODIES=file:<dir>`。inline 模式在
  60 KB 截断，长对话的请求体必然残缺；file 模式无截断，事件带 `body_ref`（绝对路径）。
- 计时来自增强遥测的 `claude_code.llm_request` span（`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`）：它是
  CLI 唯一说出 ttft、attempt、精确请求窗口的地方，按 `request_id` 与响应体日志配对（响应体事件带同一个
  `req_` id）。两路导出间隔相同，所以等齐两者不额外增加延迟；span 缺失时退回按日志时间计窗口。
  不注入 TRACEPARENT，按 source id 认领。
- 进程级单接收器、单 gRPC 端口，所有成员共用；每个 harness 在 `_open_session` 生成 source id 写入
  `OTEL_RESOURCE_ATTRIBUTES`（同时经 `--settings` 下发，压过用户 settings），接收器广播、各实例按
  source id 认领。gRPC 线程回调经 `call_soon_threadsafe` 回到事件循环。`_close_session` 取消订阅
  并删除 body 目录。
- 配对：`api_response_body` 是组装后的完整消息，`id` 即 SDK `AssistantMessage.message_id`；请求体按
  时间取响应之前最近的一条，并要求其历史里包含上一次主对话回复（排除子 agent / 侧路查询交错的请求）。
  历史消息 id 用内容身份哈希（剥离 `cache_control`，tool_use 只认 id），命中过往回复时沿用 `msg_` id。
- **OTel 说得出的事实一律以 OTel 为准**，SDK 消息流只提供 OTel 不记录的东西（工具入参与结果正文，
  OTel 只记大小）。实测各信号的用途：

  | 信号 | 用途 |
  |---|---|
  | `claude_code.api_request_body` / `api_response_body` 日志 | 请求与回复正文、usage、`msg_` id（file 模式无截断） |
  | `claude_code.llm_request` span | 请求窗口、ttft、attempt、speed、finish reason，按 `request_id` 与响应体配对 |
  | `claude_code.api_request` 日志 | 本次成本（`cost_usd_micros`）、reasoning effort、query_source |
  | `claude_code.tool` span | 工具执行窗口 |
  | `claude_code.tool.execution` span / `tool_result` 日志 | 工具成败 |
  | `claude_code.tool_decision` 日志 | 权限决策与来源 |
  | `claude_code.interaction` span | 未用：成员轮次窗口由宿主自己的事件界定 |
  | `assistant_response` / `user_prompt` 日志 | 未用：与响应体、宿主输入重复 |
  | `mcp_server_connection` / `plugin_loaded` / `hook_*` 日志 | 未用：启动与 hook 诊断，轨迹无消费方 |

- 系统提示里第一块是 Claude Code 自己的 `x-anthropic-billing-header`（含每次请求变化的 id）。它是请求
  元数据而非指令，移到 `data.claude-code.billing_header`，否则系统提示每步都像被改写。
- 工具定义的 `input_schema` 归一为 `parameters`；采样参数取自请求体（max_tokens / temperature /
  top_p / top_k / stop_sequences / stream）。
- SSH transport 无法回连 loopback：观测器不 attach，所有请求从 SDK 回复降级报告。

**Codex**（`codex/observation.py`，`CodexRequestObserver`）

- 主数据源是 rollout trace（`CODEX_ROLLOUT_TRACE_ROOT`，reader 从 team 层迁入
  `codex/rollout_trace.py`）：`inference_started` / `inference_completed|failed|cancelled` 按
  `inference_call_id` 配对，带精确窗口、完整请求（`instructions` / `input` / `tools`）与响应。
- 续写请求：Codex 以 `previous_response_id` 链式请求时请求体只带新增输入；完整对话 = 上一响应的
  对话 + 其输出 + 本次输入，由观测器在进程内拼回（链头未知时 `input_observed=False`）。
- tool 归属：响应 `output_items` 里 tool call 的 `call_id` / `id` 对上 SDK tool item id；code mode
  下 SDK item 名为运行时 id（`exec-...`），经 rollout `tool_call_started.tool_call_id` →
  `requester.runtime_cell_id` → `code_cell_started.model_visible_call_id` 关联回模型 call id。
- 工具目录：Codex 不用请求的 `tools` 字段，而是把工具清单作为 `additional_tools` 输入项下发；它是工具
  定义而非对话内容，观测器将其提到 `tool_definitions`，按命名空间摊平成可调用工具，不进 `input_messages`。
  采样参数取 `stream` 与 `reasoning.effort`。
- 降级：`rawResponseItem/completed` + `rawResponse/completed` 在 provider 内部消费（不再有
  `notification_observer`），`wait_s` 内 rollout 未记录该 `response_id` 即按输出侧报告；一旦发现
  rollout 静默（旧版 Codex 不写 rollout），后续 turn 不再等待。
- 删除 Codex native OTel receiver：`run_sampling_request` 只有时间边界没有内容，rollout 已覆盖。

### 3. 宿主记录器：`HarnessTrajectoryRecorder`

`harness_providers/trajectory.py`，与 `HarnessIOAdapter` 同级，不依赖 team：

| 协议事件 | 记录 |
|---|---|
| `TurnLifecycleEvent.STARTED` | **每 turn 一条独立 trace**：根 span `invoke_agent {agent}`，`record_kind=turn`、`openjiuwen.trace.root`、`openjiuwen.agent.mode`、完整 subject 块、`gen_ai.conversation.id`、turn id / number；属性经 `start_span(attributes=...)` 一次写入，started 快照即可按 lane 路由 |
| `ModelRequestEvent` | turn 下 `chat {model}`（事件起止时间），`record_kind=inference`、`openjiuwen.inference.id`、step number、subject request number、`gen_ai.input/output.messages`、采样参数、response id / finish reasons、总时延等；结束前以其为父 `emit_context_window_commit`；`input_observed=False` 不提交窗口 |
| tool `ItemLifecycleEvent` | `execute_tool {name}`；`causation_ids` 命中已记录请求时写 `openjiuwen.inference.id` / step number / `openjiuwen.tool.authoritative` |
| 终止事件 | 补结束未完成 tool（ERROR），写 turn 输出与状态 |

宿主输入所在的那条 user 消息标为 `external_user`（按宿主发出的文本匹配最后一条含它的 user 消息），
其余为 `harness_internal`，这样视图里能区分用户消息与上下文。

一条消息被 provider 拆成多个文本块时（注入的 reminder + 正文），记录器把它们合成一段正文：读者读到的
是一条消息，保留拆分会让任何以文本呈现的视图显示成 JSON 数组；含非文本块（图片、文档）的消息保留分块。

消息结构化 / 脱敏 / 窗口规范化复用 `OtelCallbackHandler`，为此新增公开方法
`record_request_input` / `record_response_output`。`record_turn_identity` 让宿主注入自己的 turn
身份；`record_failure` 承接可靠性失败（无活动 turn 时发零时长失败 turn）。

### 4. team 层

- `ExternalHarnessMemberRuntime`：删除 `MemberSpanBridge` 三个 Protocol、`bind_span_bridge`、
  `_RecordingOutputs`；新增 `bind_trajectory_recorder`。`_on_event` 把每个 envelope 交给 recorder；
  STARTED 前经 `resolve_member_turn` 开成员 turn（持久化编号，见 `agent_teams/harness/turn.py`）并
  `record_turn_identity`；`send` 时 `record_input`；绑定 recorder 时 `_host_context` 声明
  `MODEL_REQUEST_OBSERVATION`；`stop` 时 `close` 兜底。
- `external_cli_spawn` 在 `configure` 后用 `teammate.observability_execution_subject(session_id)`
  构造 recorder（observability 未初始化时为 `None`）。
- `RuntimeReliabilityContext(trajectory_recorder=...)` 取代 `span_bridge`。
- `cli_agent/spawn.py` 不再注入任何 OTel / rollout 配置；`sdk_mcp.py` 去掉 `tool_execution_context`，
  team MCP 工具只由协议 tool item 产生 span。

## 拒绝的方案

- **继续在 team 层修 bridge**（按 session 注册根 span、补 subject 属性）：能止血，但厂商细节仍在 team
  层，每接一个 harness 都要再写一个 bridge；且 Claude 请求体截断、Codex 通知私有钩子的问题不解决。
- **成员 span 挂在 team 根 span 下**：team span 随 leader run 结束，成员常在其后工作；viewer 以 trace
  划分 turn，共用 trace 会把成员多个 turn 合并。每 turn 独立 trace 同时解决两者。
- **请求事件先发、后补输入**：协议事件不可变且只发一次；先发残缺事件再无法修正窗口提交。
- **保留 Claude `llm_request` span 作为计时源**：响应 body 已带 usage，请求 / 响应日志时间即请求窗口；
  多接一路 traces 信号只增加配对复杂度。

## 已知遗留

- 子 agent（Claude `parent_tool_use_id`）的请求不进成员 lane。
- 宿主输入的识别靠文本匹配（provider 不说明哪条消息装着它）；匹配不上时该条仍记为上下文。
- Codex 没有 TTFT、成本与权限决策：rollout 只记录推理的起止与内容。
- 工具项的开始时间仍是观测时间：`claude_code.tool` span 在工具结束后才导出，那时无法再改已发出的
  STARTED（实测两者相差 3ms）。结束时间与成败取自 OTel。
- Codex `thread_resume` 的协议参数没有 raw events 字段，恢复的线程只能依赖 rollout。
- `TurnUsage` 没有缓存写入字段，provider 的 cache-creation token 只留在 `provider_data`，未进 span。
- 关联不上模型 call id 的 Codex tool item 最多等待 `request_observation_wait_s` 后无归属发出。
- 进程内 teammate 的 team 根 span 过期问题（`get_or_create_team_span` 未按 session 注册）不在本次范围。

## 验证

- `tests/unit_tests/harness_protocol/test_protocol.py`：事件编解码 round-trip、校验、REQUIRED 保留。
- `tests/unit_tests/harness_providers/test_trajectory_recorder.py`：独立 trace、subject 块、inference
  与 commit 父子及序号、tool 归属、宿主 turn 身份、失败记录。
- `tests/unit_tests/harness_providers/test_claudecode_observation.py` / `test_codex_observation.py`：
  事件顺序、causation、降级路径、未声明能力时不观测；`telemetry/test_otlp_receiver.py`、
  `test_codex_rollout_trace.py` 迁移自 team 层。
- `tests/unit_tests/agent_teams/external/test_member_runtime.py`：recorder 分派、成员 turn 注入、能力声明。
- 真实 CLI（2026-09-17，Claude Code + claude-agent-sdk 0.2.115；codex-cli 0.154.0）：Claude
  `body_ref` 为绝对路径、响应 `id` 与 `AssistantMessage.message_id` 一致、`generate_session_title`
  侧路请求被主对话判据排除；Codex 第二次请求确为 `previous_response_id` 增量、`exec-...` tool 经
  code cell 关联到发起推理。两者事件顺序均为 请求 → tool → 请求 → terminal。
- 真实 CLI 一轮 + 记录器 + 前端 projector 回放：成员 lane 的系统提示、上下文、工具调用与回答均为可读
  文本，用量（含缓存命中）正确，无诊断告警。
