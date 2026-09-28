# S_01 Persistent Agent 调用与执行规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | 拟议契约；尚未实现 |
| 关联模块 | `openjiuwen/harness/persistent_agent/{__init__.py,factory.py,agent.py,execution/,ports.py,schema/}`（拟议路径） |
| 最近一次修订日期 | 2026-09-25 |
| 关联 feature | N/A；开发计划 PA-01～PA-06、PA-08 |
| 设计依据 | [详细架构设计](../designs/persistent-agent-architecture.md) §4～7 |

当前 `persistent_agent/` 只有文档。本规约整理目标设计供实现和评审使用；下述类型、端口和方法尚不可导入，字段形状与错误码须在实现时审定并同步修订。

## 范围 / 边界

本规约定义调用身份、运行实例归属、宿主端口、执行阶段及参与决策。业务 Agent 的发布、Session 定位与排队、执行权授予和模型预算账户由 AgentServer 负责；本模块只执行宿主指定的 Request。事件提交与结果交接见 [S_02](S_02_events-and-result-handoff.md)，上下文见 [S_03](S_03_context-and-windows.md)，恢复见 [S_05](S_05_checkpoint-and-recovery.md)。

## 现有实现的复用边界

Persistent Agent 只添加业务 Session 归属、宿主端口和可靠交接适配；已有的模型与工具循环不得再实现一套。实现前先验证下表能力在当前配置中的实际行为，缺口落适配层；只有公共 Harness 契约确实不足时才修改其原模块并同步原规约。

| 现有实现 | 直接复用 | Persistent Agent 仍需补齐 |
| --- | --- | --- |
| [`create_deep_agent`](../../../factory.py) / [`DeepAgentConfig`](../../../schema/config.py) | 构造模型、工具、Rail 与任务循环；通过 `tool_owner_id` 区分有状态工具归属 | 每活动调用创建独立可变实例，校验业务 Session 与原配置快照；不直接装配 DeepAgent 私有字段 |
| [`DeepAgent.invoke/stream`](../../../deep_agent.py) 与 [`task_loop/`](../../../task_loop/) | 单轮、任务循环、模型调用、工具执行和原生流式输出 | 将输出映射到 S_02 的稳定业务事件；宿主负责普通 Request FIFO，不把 Harness follow-up/steer 当作服务端队列 |
| `DeepAgent.follow_up/steer/abort` 与 TaskLoop 交互能力 | 活动调用内的控制和停止入口 | 宿主控制的鉴权、持久化、去重、交互目标绑定和跨进程恢复 |
| [`DeepAgent.save_state/load_state`](../../../deep_agent.py) | 当前 Session 中可序列化的 DeepAgent 运行状态 | 已提交 Checkpoint、跨 Store 确认、事件尾部与未决操作核对；不能把 `save_state` 等同于业务完成 |
| Harness Rail、工具权限和 Workspace | 通用模型/工具事件、执行安全与工作目录基础 | 当前主体和 Session 授权交集、执行权围栏及跨调用资源清理范围 |

该复用关系是设计要求，不表示现有接口已经满足本规约的可靠性验收。尤其 [`DeepAgent`](../../../deep_agent.py) 持有 `_bound_session_id` 与 `_loop_session` 等可变状态，同一活动实例不能交给第二个业务 Session。

## 不变量

1. 一个活动运行实例只处理一个指定 Session；同一业务 Agent 的并行 Session 使用不同实例。不同 Session 的消息、待办、交互、工具上下文、取消信号和清理范围隔离。
2. 同 Session 同时只有一个有效写入者。模型前的执行权读检查不能代替 Session 事实及 ContextStore 活动指针写入边界的原子围栏。
3. Request 固定启动时的配置版本、指纹和权限上限；恢复沿用原版本。当前撤权立即收紧可用能力，新增授权不扩张原 Request 的上限。
4. Agent、Session、Request、Execution、Window、Checkpoint 和 Operation 使用各自身份。新恢复 Execution 仍属于原 Request，不把新消息当作旧请求的续写。
5. `Runner.resource_mgr` 是进程级共享状态；各活动实例须有独立 `tool_owner_id`，并只清理本实例资源。Rail、工具包装器和带凭证客户端的可变状态不跨调用共享。
6. 模型调用、工具执行、提交和换窗前均核验当前控制与授权。执行权失效后不能产生新的权威状态；已发出的外部效果仍须核对。

## 接口契约与错误语义

拟议的最小逻辑表面为：

```text
create_persistent_agent(config, ports, deep_agent_factory) -> PersistentAgent
PersistentAgent.execute(input, session_id, execution_context)
    -> AsyncIterator[AgentEvent]
```

构造只组装只读定义与调用所需依赖，不启动任务；内部 DeepAgent 仍经 [`create_deep_agent`](../../../docs/specs/S_01_public-api-and-construction.md) 构造。`input` 是宿主已持久化 Request 的内容值，不是新建 Request 的命令；恢复仍用 `execute`，由 `execution_context.recovery` 区分。公开导出待实现时审定。

| `ExecutionContext` 逻辑部分 | 必须携带或可验证的内容 |
| --- | --- |
| 归属 | `namespace_id, agent_id, session_id, request_id, execution_id` 与可信来源、Session 元数据一致 |
| 配置 | `config_version, config_fingerprint, capability_snapshot` 与运行对象完整匹配 |
| 授权 | `principal_ref, startup_scope, source_scope, policy_revision`；秘密不进入模型或快照 |
| 执行权 | `epoch, authority_handle`；安全点读检查，受控写入做原子围栏 |
| 恢复 | 原 Request、前次 Execution、恢复命令与已提交引用；不能靠新输入猜测 |

拟议端口及职责：

| 端口 | 作用 |
| --- | --- |
| `ExecutionAuthority` | 检查当前执行权，并为受控写入提供围栏 |
| `CommitPort` / `ControlPort` | 查询统一事件的提交结果；接收已鉴权、持久化、去重的控制 |
| `BudgetPort` | 按稳定 `model_call_id` 申请模型准入并报告实际用量；账户与结算归宿主 |
| `ContextStore` / `HistoryPort` | 读写窗口与资料；按当前 Session 读取已提交事件和 Checkpoint |
| `KnowledgePort` / `GroupMaterialPort` | 读取当前获授权的版本化知识和群公开资料 |
| `Workspace` 与文件工具 | 限定办公空间读写，提供可核对的结果；详见 S_04 |

端口结果必须区分已应用或已提交、明确拒绝或冲突、结果未知，以及有完整查询水位支撑的 `not_found`。超时不能转成空列表、零用量、无终态或“操作未执行”。必要端口或原配置不可用时在模型前拒绝或暂停；缺少围栏、查询或操作核对能力时不能静默开放对应写能力。逻辑错误名 `INVALID_SCOPE`、`CONFIG_UNAVAILABLE`、`STALE_EXECUTION`、`BUDGET_EXHAUSTED` 等尚非项目 `StatusCode`，实现时映射现有错误体系。

## 状态及数据生命周期

| 阶段 | 进入条件和可观察行为 |
| --- | --- |
| Preparing | 校验来源、配置、权限、执行权和端口；创建本调用私有状态 |
| Deciding | 群公开消息做只读结构化判断；私聊和定时任务走直接任务策略 |
| Recovering | 核对原 Request 已提交事实、窗口及未决操作，决定继续或暂停 |
| Running | `participation.decided` 与 `execution.started` 已确认后进入 DeepAgent Loop |
| Waiting | 等待条件与恢复依据已提交；短等待可继续原 Execution |
| Suspended | 可恢复中断或待核对；可释放计算资源，原 Request 仍占处理位置 |
| Finalizing | 收拢在途效果，提交终态及必要结果；禁止新业务副作用 |
| Closed | 宿主确认终态；仅清理本调用资源 |

阶段是本模块内部状态，不新增服务端 `ExecutionStatus`。只有宿主确认 `completed / ignored / cancelled / failed-final` 后，同 Session 下一普通 Request 才可推进。停止不等于取消；等待审批、取消和明确关联的补充走已鉴权控制路径，普通新消息继续排队。审批绑定交互、请求、执行、目标、参数摘要和有效期，重复消费返回已知结果，错配拒绝。

群公开消息的拟议判断为 `ignore | respond | intervene`，记录有限说明、目标、来源与证据版本；`@` 和回复关系只是证据。判断失败或预算不足不能写成 `ignore`。已受理私聊及定时任务记录直接任务策略的 `respond`；已提交决策在恢复时复用。`ignore` 不创建正式窗口或任务投影；参与决策确认后，再确认 `execution.started` 才进入 Loop。模型调用先经预算端口准入，真实重试使用新 `model_call_id`，未知用量由宿主核对。

## 与其它 spec 的关系

- [S_02](S_02_events-and-result-handoff.md) 定义阶段事件的提交确认与输出交接。
- [S_03](S_03_context-and-windows.md) 定义阶段化模型输入及窗口指针。
- [S_05](S_05_checkpoint-and-recovery.md) 定义持久化等待、控制续接与安全恢复。
- 通用 DeepAgent 行为仍以 [Harness 架构规约](../../../docs/specs/S_02_deep-agent-architecture.md) 为准。
