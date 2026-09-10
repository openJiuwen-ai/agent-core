# S_24 三方 Agent Harness 接入协议

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/external/protocol` |
| 协议版本 | `4.0` |
| 最近一次修订日期 | 2026-08-20 |
| 关联 feature | `F_94_external-harness-protocol.md` |

## 范围与边界

本 spec 定义三方 Python Agent Harness 加入 OpenJiuwen team 所需实现的
provider-neutral 行为协议。它位于厂商 Harness 与通用 `ExternalHarnessMemberRuntime` 之间，
不定义当前 Claude Code、Codex 或 subprocess backend 的迁移方式，也不改变现有
spawn/config schema。

`ExternalHarnessProtocol` 是完整、multi-turn、并发安全的 Harness 契约，不是单 model call、
单 turn handle 或厂商通知流。第三方可以直接实现该协议；框架未来也可提供 managed base，
把更低层的 turn driver 提升成该协议，但低层 driver 不属于本公共 SPI。

## 统一术语

```text
Session
└── Turn          一次外部输入 -> 一次稳定外部输出
    └── Iteration 一次 Agent Loop 控制循环
        └── Step  一次可观测的原子执行动作
```

`Round` 只用于 multi-agent 协作或协议阶段，一个 Round 可以包含多个 Agent 的 Turn。单 Agent
Harness 边界必须使用 `turn_id`、`TurnLifecycleEvent`、`TurnEventKind` 和 `turn_events()`；不得为旧的
Round 误用提供公共别名。

## 不变量

1. 一个 Harness 实例只代表一个 team member 和一个 provider session。
2. 所有 public command 都可被不同协程并发调用；实现内部序列化状态转换。
3. `start` 开启一个 cycle 并结算到 IDLE；`stop` 幂等、关闭 events、结算到 TERMINATED。
4. `events()` 与 `turn_events()` 是同一逻辑单消费者流的持续/单 Turn 视图，不能并发消费；
   信封 `sequence` 严格递增。
5. 每个 Turn 有且只有一个 STARTED 和一个 terminal event；PAUSED/RESUMED 是同一 Turn 内的非终态
   转换，terminal event 必须携带状态匹配的 `TurnResult`。
6. `send` 只确认接受，不等待执行完成，并在 receipt 中返回该输入关联的 `turn_id`。
7. Harness 可选行为以 `ExternalHarnessCard.capabilities` 声明；Card 同时声明 compatible protocol
   versions 和 required/optional `HostCapability`，start 前必须完成协商，缺必需能力不得降级。
8. events 是观测面；interactions 是 SDK 请求/响应控制面；hooks 是生命周期策略控制面。
9. checkpoint 由 provider 解释，必须有版本、可 JSON 序列化、绑定 `member_agent_id`、携带幂等 ID 和
   单调 sequence，且不得包含凭据。
10. protocol 包不依赖任何可选厂商 SDK。
11. 跨协议 JSON 值构造时递归校验、复制和冻结；禁止 NaN/Infinity、任意 Python object 和可变别名。
12. event buffer 有界，retention 由 payload 推导；REQUIRED event 永不丢弃。

## 接口契约

### ExternalHarnessProvider

Provider 暴露静态 Card，并通过 `create(config)` 校验 provider-owned 配置、返回未启动 Harness。
构造期不连接网络、不启动进程、不绑定 event loop；运行资源在 `start` 创建。

### ExternalHarnessProtocol

| 成员 | 语义 |
|---|---|
| `card` | provider identity、implementation/protocol compatibility、harness/host capabilities |
| `state` | 当前 `HarnessState` |
| `session_id` | provider-native conversation/thread/session id |
| `start(context)` | 绑定成员身份、宿主服务和恢复检查点并启动 cycle |
| `stop()` | 终止运行、释放资源、关闭 event stream；幂等 |
| `event_buffer_config` | 有界 capacity 与 overflow policy |
| `events()` | cycle-long ordered `HarnessEventCursor`；单消费者、可 `aclose()` |
| `turn_events(turn_id=None)` | 指定下一 Turn 的有限 cursor；包含 STARTED 和 terminal event |
| `send(input, mode)` | 接受 AUTO/STEER/FOLLOW_UP 命令并返回 receipt |
| `abort(mode)` | graceful/force abort；能力 gated |
| `pause()` / `resume()` | warm/cold paused-turn continuation；能力 gated |
| `export_checkpoint()` | 返回最新 `HarnessCheckpoint` 快照；不替代主动保存 |

### Delivery

- AUTO：IDLE 时开启新 Turn；RUNNING 时排到 follow-up。
- STEER：注入 active Turn，要求 STEER capability；receipt 返回 active `turn_id`。
- FOLLOW_UP：接受时分配新 `turn_id`，在 active Turn terminal 后开启该后继 Turn。

## 三个独立通道

### Observation：HarnessEvent

`events()` 返回统一信封 `HarnessEvent`。`team_session_id`、`member_agent_id`、`sequence`、`timestamp`、
provider `session_id`、`turn_id`、`item_id`、`correlation_id` 和 `causation_ids` 位于信封；载荷不重复
这些公共字段。`correlation_id` 聚合同一逻辑 trace；`causation_ids` 列出实际触发事件的消息/request，
支持同一 Turn 被多次 STEER。载荷包括：

- `OutputEvent`：稳定 `output_id` + content index，TEXT/STRUCTURED 表示，ANSWER/REASONING/SYSTEM
  channel，以及 DELTA/SNAPSHOT/FINAL operation；
- `ItemLifecycleEvent`：工具调用、命令、文件变更等 provider item 的生命周期；
- `UsageUpdatedEvent`：标准化 token usage，显式区分 DELTA/CUMULATIVE；
- `StateChangedEvent` 和 `TurnLifecycleEvent`；
- `HookObservedEvent` 和 `DiagnosticEvent`；
- `ProviderEvent`：带 provider、event type、schema version 的 JSON 扩展。

`turn_events(turn_id)` 是对同一 observation channel 的串行有限封装：指定 ID 用于校验下一个尚未
消费的 Turn，不得把它实现为丢弃中间 Turn 的乱序选择器；无 ID 时选择下一个 STARTED。从 STARTED
（含）开始按全局顺序产出，到同一 Turn 的
FINISHED/ABORTED/FAILED（含）立即结束。PAUSED/RESUMED 保持相同 `turn_id`，不得结束有限流。
重复调用可消费连续 Turn；`events()` 与 `turn_events()` 不能同时处于消费状态。底层行为与 Claude
SDK 的 `receive_messages()`/`receive_response()` 相同，不要求实现建立第二份多播队列。
实现检测到第二个 active iterator 时必须抛 `ExternalHarnessStateError`，不能让两个 consumer 竞争
同一 queue。
cursor 正常 EOF 或显式 `aclose()` 都必须幂等释放 consumer lease。

如果 cycle 在找到下一个 Turn 前正常关闭，`turn_events()` 可以空结束；如果已经产出 STARTED 却未
产出对应 terminal event 就关闭，属于 `ExternalHarnessProtocolError`。

公共事件不依赖 `OutputSchema`，避免三方 SDK 消息在协议入口被过早压缩。现有
`ExternalHarnessMemberRuntime` 负责把 `HarnessEvent` 投影成内部 stream schema；该兼容投影不改变
协议事件仍为信息完整、可供其它 host 消费的权威表示。

Observation queue 必须使用正 capacity。`event_retention(payload)` 是 retention 的唯一来源：Turn/State/
Item lifecycle、delta、final、warning/error、provider/unknown event 是 REQUIRED；snapshot output 和
cumulative usage 是 COALESCIBLE；debug/info diagnostic 与 hook observation 是 BEST_EFFORT。队列满时
只能按 policy 合并/丢弃允许的类别；没有安全候选时阻塞 producer，不得丢 required event。

跨进程/第三方 wire 使用 `harness_event_to_dict` / `harness_event_from_dict`。信封包含稳定
`event_type + schema_version` discriminator；未知 type/version 解码为 `UnknownEvent` 并可无损重编码。

`TurnLifecycleEvent.result` 不允许 `Any`。terminal event 使用 `TurnResult` 表达 status、有序
message/content block、final/structured projection、termination/error、usage、精确货币微单位 cost、
timing 和 provider extension data。`messages` 是标准化完整输出；projection 只为简单消费者提供便利。

### Interaction：HarnessInteractionHandler

Provider SDK 在 active Turn 内发起且必须等待回答的请求，通过
`ExternalHarnessContext.interactions` 处理：

- `ToolApprovalRequest` / `ToolApprovalResponse`；
- `UserInputRequest` / `UserInputResponse`；
- `McpElicitationRequest` / `McpElicitationResponse`；
- `DynamicToolCallRequest` / `DynamicToolCallResponse`；
- 带 provider、request type 和 schema version 的 namespaced
  `ProviderInteractionRequest` / `ProviderInteractionResponse`。

请求可携带 Unix seconds 的 `deadline_at`。响应必须保持相同 `request_id`，且 response 类型必须与
request 类型配对；adapter 使用 `validate_interaction_response` 做统一校验。Provider 撤销请求时调用
幂等 `cancel(request_id, reason=...)`；Turn abort 和 Harness stop 返回前必须取消该范围内全部 pending
request。

Interaction host 能力按 `TOOL_APPROVAL`、`USER_INPUT`、`MCP_ELICITATION`、`DYNAMIC_TOOL_CALL` 和
`PROVIDER_INTERACTION` 分别协商，不使用一个粗粒度 HOST_INTERACTIONS 代替。Card 的 required 能力
缺失时 start fail-fast；optional 能力缺失时，具体请求必须安全 decline/fail，不能默认授权。
这些请求不能发到 events 后等待 event consumer 回写，否则会造成死锁、审批丢失和多消费者竞态。

### Hooks：HarnessHookDispatcher

before-prompt、before-tool、after-tool 与 on-stop 是 harness 生命周期上的宿主策略扩展。
Harness await hook 并使用返回值改写/拒绝执行；`HookObservedEvent` 只用于日志、trace 和 UI。

`ToolDecision` 的 REWRITE 必须携带替换参数；ASK 映射到 tool-approval interaction；
PROVIDER_POLICY 交给已配置的 provider 原生权限策略。Hook 与 SDK interaction 可组合：例如 SDK
原生 approval request 先映射到 interaction handler；
adapter 若还需要执行 OpenJiuwen 的统一工具策略，可在实际执行前调用 before-tool hook。两者不互相替代。

## Tools 与 MCP

支持两条 provider-neutral 路径：

- native SDK tool：`ExternalToolGateway.definitions/invoke`；
- MCP：`McpServerConfig` 描述 stdio、HTTP 或 in-process server；command/url/instance 恰好一个，
  HTTP headers 与进程 env 分开表达。

Provider adapter 负责把通用结构转换成自己的 SDK options；不得把厂商配置字段加入公共模型。
动态工具执行请求属于 active SDK interaction，和 host 预先暴露的 tool gateway 是不同方向。

## Checkpoint 与恢复

`HarnessCheckpoint` 是宿主可持久化、provider 才可解释的完整信封：

- `provider` 与 provider-owned `schema_version`；
- `member_agent_id` 和 `team_session_id`，防止跨成员或跨 team session 恢复；
- `checkpoint_id` 幂等标识与 scope 内单调 `sequence`；
- 可选 `session_id`、`revision`；
- JSON-safe `data`。

Checkpoint `data` 最大 4 MiB；超限、非有限数字或非 JSON 对象在进入 sink 前失败。

## ID 与时间作用域

- `message_id`：member + team session 内唯一；`turn_id`：member + team session 内唯一；
- `item_id` / `call_id`：Turn 内唯一；`request_id`：Harness start/stop cycle 内唯一；
- `checkpoint_id`：provider/member/team session 内唯一，sequence 在同 scope 单调递增；
- event `sequence`：一个 start/stop cycle 内严格递增；
- event、deadline、started/completed timestamp 使用 UTC Unix seconds 且必须有限；
- `duration_ms` 是 monotonic clock 计算的非负 elapsed time，不由 wall clock 相减替代。

Host 在 `start` 时通过 `context.checkpoint` 提供检查点；`REQUIRE_RESUME` 缺失、provider 不匹配、
成员不匹配或版本不可读取时必须失败。

Harness 在获得或改变可恢复 provider 状态后，通过 `context.checkpoint_sink.save(...)` 主动保存，
典型时机包括 session 激活、turn 完成、重要状态变化、定期刷新和 provider 主动通知。
相同 `checkpoint_id` 的 retry 必须返回原 receipt；不同 ID 的旧 sequence 或失败的
`expected_storage_revision` compare-and-set 必须抛 `CheckpointConflictError`，不得覆盖新状态。
`save` 返回 `CheckpointSaveReceipt` 表示宿主持久化完成。`export_checkpoint()` 保留为按需快照和停机
兜底，但不是唯一保存路径。

## 与其它 spec 的关系

- `S_18_harness-interaction-contract.md`：现有 HarnessProtocol/MemberRuntime 及 team 状态映射。
  本协议通过 `external/member_runtime.py` 的通用 adapter 接到 MemberRuntime，不替代当前内部 seam。
- `S_05_member-spawn-and-stream.md`：成员 spawn 和 stream；本次不修改该链路。
- `S_14_monitor-and-observability.md`：events 的 telemetry 消费者可接入该观测体系。
- `S_08_team-tools-contract.md`：ExternalToolGateway/MCP 暴露的 team tools 仍受其角色和权限约束。
