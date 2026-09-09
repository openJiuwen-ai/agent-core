# DSH External Harness Adapter

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-20 |
| 范围 | `openjiuwen/agent_teams/external/member_runtime.py`、`openjiuwen/agent_teams/external/dsh/` |
| 协议版本 | `4.0`（未修改） |
| 关联 feature | `F_94_external-harness-protocol.md` |
| Refs | 未关联 issue |

## 背景

F_94 已定义 provider-neutral 的 `ExternalHarnessProtocol`，但尚无真实三方 SDK 实现，也没有从该
协议到 AgentTeam 内部 `MemberRuntime` 行为面的通用接线。DeepSeek Harness（DSH）Python SDK
提供可复用 runtime/session、阻塞式 `Session.run()` 和 notification callback，适合作为第一条协议
落地路径，同时也暴露出两边边界并不完全对等：DSH 当前 Python SDK 没有 steer、abort、pause、
resume、checkpoint 或动态 MCP 配置接口。

本 feature 在不改变协议 4.0 的前提下新增 DSH provider adapter 和通用
`ExternalHarnessMemberRuntime`。现有 Claude Code、Codex 及 subprocess runtime 不迁移；
`ExternalCliAgentSpec`、`build_cli_runtime` 和声明式 spawn/registry 也不接线。

## SDK 源码事实

- `DeepSeekHarness` 管理一个可复用 runtime 子进程，`start_session(session_id)` 返回长期 `Session`。
- `Session.run(input, on_notification=...)` 是同步阻塞调用：prompt 获得 durable receipt 后，继续等待
  整个 agent（含子 agent）进入 idle，最后返回 `RunResult`。
- 当前 wire request 只有 initialize、session prompt 和 shutdown；Python SDK 没有对外的 interrupt、
  pause/resume 或运行中 steer API。
- `session.event` 包含 DSH native turn、step、assistant chunk/message、tool call/result；另外还有
  session status 与 subagent started/finished notification。
- DSH 的 native turn 是 provider 内部执行记录，step 表示一次模型调用及其请求的工具；它们不能
  重新定义 OpenJiuwen 面向外部输入的 Turn。
- Python SDK 不提供协议 checkpoint 的恢复 API，也不能在 session 启动时动态接收
  `ExternalHarnessContext.mcp_servers`。
- system prompt 不属于 Python SDK initialize 参数。只有 custom Cordis composition 显式读取某个
  环境变量时，adapter 才能通过该变量传入 `ExternalHarnessContext.system_prompt`。

## 决策

### 1. 增加通用 MemberRuntime adapter

`ExternalHarnessMemberRuntime` 只消费一次 `harness.events()` 持续流，并把公共事件投影到当前内部
行为面：

- `OutputEvent` -> `llm_output` / `llm_reasoning` `OutputSchema`；
- tool `ItemLifecycleEvent` -> `tool_call` / `tool_result`；
- `StateChangedEvent` -> `harness.state` callback；
- `TurnLifecycleEvent` -> legacy `harness.round` callback。

该层负责 AgentTeam 的 runtime 兼容，不反向污染公共协议。内部 callback 中的 `round` 是现有
`StreamController` 兼容名；三方协议仍统一使用 Turn。

当成员正在运行时，`immediate=True` 只在 provider 声明 `STEER` 后发送 STEER；否则显式排为
FOLLOW_UP，不能用 stop/restart 模拟插入。adapter 同时复用 `TeamContextTracker` 的 pending/commit
语义，让外部 Harness 成员能够收到团队身份与名册更新。

DSH 还可在构造 `ExternalHarnessMemberRuntime` 时显式设置
`stop_on_unsupported_force_abort=True`，用“停止整个 Harness cycle”兼容 MemberRuntime hard-cancel。
这不是 provider turn force-abort，也不改变 DSH 的空 capabilities；默认 `False` 时保持严格并抛
`UnsupportedHarnessCapabilityError`。

### 2. 固定 DSH 的外部 Turn 边界

DSH adapter 串行处理已经接受的输入。一个 OpenJiuwen Turn 对应一次 `Session.run()` activity interval：

```text
adapter dispatch -> external STARTED
        |
DSH prompt durable receipt -> notification collection starts
        |
        +-- native turn(s)
        |      +-- native step(s)
        |              +-- model/tool events
        |
whole-agent idle -> external terminal TurnResult
```

因此：

- adapter 接受输入时立即分配 `message_id` 和外部 `turn_id`；
- 前一 Turn 尚未 idle 时，AUTO 被确认成 FOLLOW_UP 并排队；
- adapter 为每个输入只发布一对 external STARTED/terminal；
- follow-up 队列未耗尽时 Harness 保持 RUNNING，避免团队调度器把 Turn 间隙误判为整条链已结算；
- DSH native turn 保留为 namespaced `ProviderEvent`；
- DSH native step 映射为 `item_type="iteration"` 的 `ItemLifecycleEvent`；
- text/reasoning chunk 映射为带稳定 output ID 的 DELTA；assistant message 提供 FINAL 与终态消息；
- tool call/result 映射为 tool item 生命周期，usage 映射为 `UsageUpdatedEvent`；
- 未识别的 DSH notification 保留为 `ProviderEvent`。

`Session.run()` 在 whole-agent idle 时若没有任何 native `turn/end`，adapter 发布 FAILED +
`DSH_MISSING_TURN_END`，不能把缺失 terminal 当作空成功。`turn/end` error 中的原始 message 不进入
terminal 或 namespaced ProviderEvent，只保留稳定 code/status/requestId/retryable 字段。

notification callback 位于 SDK worker thread。adapter 将它桥回所属 asyncio loop，并等待事件进入
有界阻塞队列，从而让协议的 backpressure 约束真实作用到 SDK 生产侧。

该保证只覆盖 adapter 公开的 protocol event buffer。DSH SDK 内部 subscription queue 当前仍无界，
adapter 无法在不修改上游 SDK 的情况下约束其积压；这是首版保留的上游限制。

有界 REQUIRED event 与 BLOCK 策略也适用于 stop 期间产生的 terminal/state event。宿主必须先启动
唯一 continuous consumer，并在调用 `stop()` 时继续消费到 EOF；如果无 consumer 且缓冲已满，
producer 与 stop 等待容量是预期背压，不得通过丢事件、提前 close 或另建无界 replay queue 绕过。
`ExternalHarnessMemberRuntime` 的内部 event pump 满足这个顺序。

### 3. 能力声明必须保守

首版 `DshHarness.card.capabilities` 为空。以下方法或配置不得静默成功：

- STEER、graceful/force abort、pause/resume；
- checkpoint export/restore 与跨 runtime session 恢复；
- 从 `ExternalHarnessContext.mcp_servers` 动态安装 MCP；
- 未配置 `system_prompt_env_var` 时传入非空 system prompt。

`export_checkpoint()` 返回 `None`；`REQUIRE_RESUME` 或非空 checkpoint 在 start 前失败，
`RESUME_IF_AVAILABLE` 在没有 checkpoint 时只能启动新 session。DSH 可以通过 custom Cordis 静态
装配 MCP，但这不等于实现协议的动态 MCP capability。

### 4. DSH SDK 保持 optional dependency

公共包和 provider/config 的导入不能要求安装 `deepseek-harness-sdk`。只有 `DshHarness.start()` 才
lazy import `deepseek_harness`；缺依赖时返回明确的 `ExternalHarnessError`。SDK 的 config/client、
凭据与可能含 stderr 的异常文本不得进入公共 event、checkpoint 或日志。

### 5. 首版只提供程序化装配

当前可用链路为：

```text
DshHarnessProvider.create(config)
        -> DshHarness
        -> ExternalHarnessMemberRuntime(harness, context)
        -> MemberRuntime 行为面
```

本 feature 不把 `dsh` 注册为 `ExternalCliAgentSpec.cli_agent`，也不修改现有 spawn registry。因此
不能仅靠 TeamAgentSpec/YAML 声明自动拉起 DSH 成员；调用方需显式构造 provider、harness、context
和 runtime。

## 拒绝的方案

- **直接让 DSH `Session.run()` 成为 MemberRuntime**：会把同步 callback、provider native turn 和
  内部 stream schema 泄漏到团队层，并绕过协议的事件、关联与能力协商。
- **并发调用多个 `Session.run()` 以模拟 steer**：whole-agent idle 的返回边界无法可靠归因到多个
  并发输入，会破坏一个输入对应一个外部 Turn 的不变量。
- **把 DSH native turn 当成 OpenJiuwen Turn**：一次外部输入可能触发多个 native turn；这样会让
  receipt 与 terminal result 失去一一对应关系。
- **只保存 DSH session id 作为 checkpoint**：当前 Python SDK 没有通过该信封恢复 provider 状态的
  完整路径，声明 checkpoint/persistent-session capability 会制造不可兑现的恢复承诺。
- **默认注入 MCP 或 system prompt**：DSH 的这两项能力由 Cordis composition 决定；adapter 不能在
  SDK 未提供动态入口时假定 bundled runtime 会消费它们。

## 验证范围

- DSH SDK 未安装时公共 import 成功，start 给出可诊断错误；
- config 校验与凭据/环境变量不可泄漏；
- 多个输入被接受后串行形成完整 Turn，AUTO/FOLLOW_UP receipt 正确；
- notification 到 output/item/usage/provider event 的映射与稳定关联；
- whole-agent idle 后才发布 external terminal，stop 时已接受 Turn 仍获得唯一 terminal；
- 持续流和单 Turn 流共享单消费者、严格 sequence、有界阻塞背压并可关闭；
- 明确验证有界保证止于公开 protocol buffer，不把上游 SDK 无界 subscription queue 误报为有界；
- DSH 未支持能力明确失败，Card capabilities 为空；
- `ExternalHarnessMemberRuntime` 的 output/state/legacy round 投影与团队上下文提交语义。

## 已知遗留

- 把 provider discovery、声明式 config 和 member spawn 接到 `TeamAgentSpec`；
- DSH SDK 提供正式取消、恢复或动态 MCP 接口后再逐项增加 capability；
- 评估 custom Cordis 配置的可移植 system prompt/MCP 模板；
- 迁移 Claude Code 和 Codex backend 到同一协议与通用 MemberRuntime adapter。
