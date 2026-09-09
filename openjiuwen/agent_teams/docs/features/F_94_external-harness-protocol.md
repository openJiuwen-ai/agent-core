# Third-party Agent Harness Protocol

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-20 |
| 范围 | `openjiuwen/agent_teams/external/protocol`、`docs/dev/agent_teams/external_harness_integration.md` |
| 协议版本 | `4.0` |
| Refs | 未关联 issue |

## 背景

team 已经通过 Claude Agent SDK、Codex Python SDK 和 subprocess CLI adapter 接入外部成员，
并由 `CliRuntimeBase` 映射到内部 `MemberRuntime`。但 backend 构建、provider session、MCP 注入、
事件转换和 checkpoint 仍直接编织在具体实现与 spawn 分支中，三方 Python 项目缺少稳定公共契约。

第一版协议确认了正确的边界：公共接口应位于 `ProviderMemberRuntime` 等价的完整 Harness 行为层，
而不是降为一次 `receive_response()` 或单 turn handle。随后对仓库锁定版本 Claude Agent SDK
`0.2.115` 和 Codex Python SDK `0.144.4` 的源码进行检视，发现多项阻断真实迁移的 P0 缺口。

## SDK 源码结论

- Claude client 是双向会话：`query()` 发输入，`receive_response()` 只读取到一个 `ResultMessage`；
  control protocol 还会主动发起 `can_use_tool`、hook callback、MCP message，并支持 cancel。
- Claude hook callback 与 hook event 是两条机制：前者等待结果并影响执行，后者只是观测消息。
- Codex thread/turn/handle 管理长期 thread 和 turn stream；底层 server request router 会等待 approval
  handler。该请求/响应语义不能靠 notification/event stream 表达。
- 两个 SDK 都保留比文本 chunk 更丰富的 item、usage、terminal result、session/thread identity 和
  provider extension。入口即转换成内部 `OutputSchema` 会不可逆丢失信息。
- 可恢复 session/thread identity 可能在 start 后或 turn 中才出现。只在 stop 时 pull checkpoint，
  进程异常退出会丢失最后可恢复点。

## 决策

1. **保留高层 Harness 边界**：`ExternalHarnessProtocol` 继续负责 start/stop、send、
   abort/pause/resume、cycle-long events 和 checkpoint；不公开厂商单 turn 驱动接口。
2. **新增独立 interaction control plane**：`HarnessInteractionHandler` 处理 tool approval、user input、
   MCP elicitation、dynamic tool call 和 provider extension。它由 host 注入 context，request/response
   以 `request_id` 关联，并支持 cancel。
3. **明确 events / interactions / hooks 三分**：events 单向观测；interactions 对应 provider SDK
   主动请求；hooks 对应 OpenJiuwen 生命周期策略。任何需要阻断 provider 的操作都不能依赖 events。
4. **事件改为信封加 provider-neutral payload**：公共顺序与关联字段在 `HarnessEvent`；共享载荷表达
   output、item、usage、state、turn、hook 和 diagnostics；`ProviderEvent` 保留 namespaced JSON 扩展。
5. **terminal result 结构化**：`TurnResult` 代替 `Any`，统一 status、output、usage、error、cost、
   timing，同时用 `provider_data` 保留厂商字段。
6. **不在协议层依赖 OutputSchema**：三方实现先做无损、provider-neutral 映射；未来
   MemberRuntime adapter 再转换为当前内部 stream schema。
7. **checkpoint 使用版本化信封和 push/pull 双通道**：`HarnessCheckpoint` 绑定 provider、版本和 member；
   `HarnessCheckpointSink` 在 session/turn 状态变化时主动持久化；`export_checkpoint()` 保留按需快照。
8. **协议 major 升级到 2.0**：事件形状、terminal result 和 checkpoint 类型均为破坏性语义变化。
9. **本次不迁移现有 backend**：Claude/Codex runtime、registry、MemberRuntime adapter 和声明式配置
   接线仍留给后续变更。
10. **提供持续流和单 Turn 流两种视图**：`events()` 跨 Turn 持续到 stop；`turn_events()` 参考
    Claude SDK，从下一个 Turn STARTED 产出到 terminal event（含）后结束。两者共享同一单消费者流，
    不引入隐式多播。
11. **统一执行层术语并升级到 3.0**：固定 `Session > Turn > Iteration > Step`，Round 只属于
    multi-agent 协作阶段。删除单 Agent 边界上的 Round 命名，统一为 `turn_id`、
    `TurnLifecycleEvent`、`TurnEventKind` 和 `turn_events()`。
12. **闭合 Turn 暂停语义并升级到 4.0**：PAUSED/RESUMED 是同一 Turn 内的非终态转换；只有
    FINISHED/ABORTED/FAILED 终结 Turn，有限流跨暂停继续消费。
13. **输入在接受时关联 Turn**：`SendReceipt` 返回 `turn_id`；queued input 预分配后继 Turn ID，
    STEER 关联当前 Turn，`turn_events(turn_id)` 可精确消费已接受输入。
14. **Interaction 增加可靠性约束**：request 可声明 deadline，request/response 类型必须配对；abort
    和 stop 返回前按明确 reason 取消全部 pending interaction。
15. **Checkpoint 防止乱序覆盖**：增加 checkpoint 幂等 ID、scope 内单调 sequence、可选 CAS 和持久化
    receipt；stale write 明确失败。
16. **输出流可无损重建**：`OutputEvent` 使用稳定 output ID、content index、answer/reasoning/system
    channel 和 DELTA/SNAPSHOT/FINAL operation；usage 同样显式区分 delta/cumulative。
17. **TurnResult 保留完整消息**：有序 message/content block 是标准化终态输出，`final_output` 仅作便捷
    投影；interrupt 使用结构化 termination，cost 使用货币微单位避免 float 精度损失。
18. **细分 Host capability**：Card 声明兼容协议版本以及 required/optional `HostCapability`；start 在创建
    provider 工作前 fail-fast，不再用单一 HOST_INTERACTIONS 掩盖 approval/input/elicitation 差异。
19. **闭合 Hook 决策**：REWRITE 显式携带新参数；ASK 进入 tool-approval interaction；
    PROVIDER_POLICY 明确交给 provider 原生策略，不保留含义不清的 DEFER。
20. **严格校验 MCP transport target**：command、url、instance 恰好一个；HTTP headers 独立承载，
    transport 不接受互相矛盾的 target。
21. **冻结协议 JSON 边界**：输入、事件、结果、interaction、hook、tool 和 checkpoint 的 JSON 数据在
    构造时递归校验/复制/冻结；拒绝 NaN、Infinity、任意 Python object 和可变别名。
22. **事件具备明确 scope 与因果关系**：信封携带 team session/member；correlation 表示逻辑 trace，
    causation IDs 表示实际触发消息/请求；所有 ID 的作用域在 spec 中固定。
23. **背压有界且不可丢终态**：Harness 暴露 `EventBufferConfig`；retention 由 payload 推导，required
    事件只能阻塞生产者，snapshot/cumulative 可合并，低级诊断/hook observation 才可 best-effort drop。
24. **事件 cursor 可显式关闭**：`HarnessEventCursor.aclose()` 幂等释放单消费者 lease，替代无法由类型
    保证 close 的普通 `AsyncIterator`。
25. **增加官方 wire codec**：event envelope 使用稳定 `event_type + schema_version` JSON shape，已知
    事件强类型恢复，未知事件无损保留；checkpoint JSON 大小限制为 4 MiB。

## 拒绝的方案

- **直接把现有 MemberRuntime 作为三方协议**：它包含 native TeamHarness 专属 rail、memory、
  workspace 和 sys-operation 语义，外部实现只能堆 no-op。
- **以 Session/TurnHandle 作为唯一公共协议**：会让已有完整多轮 Harness 的 SDK 重复实现 team
  状态机。低层 driver 可以是将来的内部 convenience SPI，但不替代公共行为协议。
- **只保留单轮流、替代持续 `events()`**：拒绝。前者适合串行单轮调用；team runtime
  仍需要跨 Turn、follow-up 直到 stop 的成员事件总序。
- **把 provider request 或 hook 做成普通 event**：安全决策和 SDK server request 必须被 await；
  单向异步事件无法可靠阻断。
- **只保留 `export_checkpoint()`**：异常退出前未必能 pull，无法满足长期 session 的恢复可靠性。

## 验证范围

- 公共导出、runtime structural check 和不可变模型；
- interaction request/response 关联与 cancel；
- 版本化 checkpoint 和主动 sink；
- event envelope、provider extension 和 terminal result 不变量；
- `events()` 持续流与 `turn_events()` terminal-inclusive 单 Turn 流语义；
- import 不触发 Claude/Codex optional dependency；
- 第三方开发文档包含实现骨架、三平面映射和契约测试清单。

## 已知遗留

- 实现 `ExternalHarnessProtocol -> MemberRuntime` adapter。
- 增加 provider registry / Python entry point discovery。
- 把通用配置接入 `TeamAgentSpec`，并保留 `ExternalCliAgentSpec` 兼容映射。
- 迁移 Claude Code 和 Codex backend，补齐 server-request handler、event/result 映射和 checkpoint sink。
- 迁移时确认 Codex 高层 async client 如何注入自定义 approval handler；必要时只在 provider adapter
  内封装其低层 server API，不能把低层类型泄漏到公共协议。
- 提供可复用的第三方 behavioral contract test kit。
