# S_01 Persistent Agent 调用与执行规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | PA-01 契约、PA-02 DeepAgent 构造、PA-03 实例内 Session 隔离已按各自边界验收；真实运行时闭环待接入 |
| 关联模块 | 外层 `persistent_agent/__init__.py` 与内部 `persistent_agent/persistent_agent/{factory.py,ports.py,schema/,execution/}` |
| 最近一次修订日期 | 2026-09-29 |
| 关联 feature | [F_01 Spec 与 DeepAgent 装配](../features/F_01_spec-and-deep-agent-construction.md)、[F_02 执行协议](../features/F_02_execution-protocol.md)、[F_03 执行绑定](../features/F_03_invocation-binding-and-isolation.md)；开发计划 PA-01～PA-06、PA-08 |
| 设计依据 | [详细架构设计](../designs/persistent-agent-architecture.md) §4～7 |

当前已实现并独立验证 PA-01 的契约层、PA-02 的构造入口及 PA-03 的实例内调用绑定 / 单轮流生命周期。下文事件生产、宿主后端、真实授权围栏、窗口存储、上下文装配和恢复仍是目标协议，不代表已经可运行。实际构造接口如下：

```python
from openjiuwen.harness.persistent_agent import (
    PersistentAgentSpec, PersistentBuildContext, ExecutionContext,
    PersistentPorts, create_persistent_agent,
)
```

`PersistentAgentSpec` 继承 `DeepAgentSpec`，JSON 字段平铺，拒绝未知字段与未实现能力。当前仅有 `schema_version=1` 和 `enabled_features={construction}` 两个专属字段：前者拒绝未知 Spec 结构版本，后者与宿主调用的能力快照比对；未实现的运行时策略不预先放进 Spec。

完整配置经 JSON 往返生成副本，规范化 JSON 的 SHA-256 为 `spec.fingerprint()`，覆盖父类及专属字段。Session、ports、锁和 owner 不在 Spec 中。上下文须提供真实 `openjiuwen.core.single_agent.Session`，与 immutable ExecutionContext 的 Agent / Session / 原配置指纹及能力快照匹配；配置版本和主体由宿主固定。ExecutionContext 包含 namespace_id、agent_id、session_id、request_id、execution_id、`source_ref`、config_version、config_fingerprint、principal_ref、`authority_handle`、startup_scope、source_scope、policy_revision、epoch、capability_snapshot 及可选 recovery_ref；这些声明不是有效租约证明。`authority_handle` 是宿主提供给授权端口的 opaque handle，不进入 AgentEvent；构造时只验证存在，真实有效性由运行时授权检查确认。

PersistentPorts 已定义异步方法及类型化返回值；工厂仍仅验证方法形状，不调用端口。不提供假成功端口。`AgentEvent`、`ModelUsage` 和下表结果值对象经外层包懒导出，内部实现位于 `persistent_agent/schema/{protocol,events,port_results}.py`。

| 异步方法 | 返回值 / 入参要点 |
| --- | --- |
| `authority.check(execution)` | `AuthorityResult`：allowed / denied / unknown；允许范围不得超出 startup_scope |
| `commit.submit(execution, event)` | `AgentEvent` → `CommitResult`；确认必须含宿主序号与提案摘要 |
| `commit.query(execution, event_id)` | `CommitResult`；完整查询及水位支撑 not_found |
| `control.poll(execution)` | `ControlResult`：available / empty / unknown；ControlCommand 绑定调用和 control_id |
| `budget.admit(execution, model_call_id)` | `BudgetAdmission`：admitted / denied / unknown；准入须含 reservation_ref |
| `budget.report(execution, model_call_id, usage)` | `ModelUsage` → `BudgetReport`：confirmed / rejected / unknown；确认须含 receipt_ref |
| `context_store.read_head/read_window/save_window/advance_head/create_and_activate_window/query_operation` | 分别返回 `ContextHeadResult`、`WindowReadResult` 或 `WindowMutationResult`；窗口版本、条件推进和未知操作核对的值对象已定义。ContextStore 按启用能力提供，尚无生产存储后端 |
| `history.search/read`、`knowledge.resolve/read`、`group_material.search/read` | `MaterialQuery` → `MaterialReadResult`；命中携带稳定来源 / 范围 / 版本 / 内容引用，empty 要求完整查询，unknown 与 partial / forbidden / unavailable 分开。端口与 DTO 已定义，搜索和授权读取实现待接入 |

所有事件和端口结果继承 `InvocationRef`，绑定 namespace / Agent / Session / Request / Execution / source_ref / epoch。DTO 使用 frozen Pydantic 模型并拒绝额外字段；字段校验为 ValidationError，纯归属 / 提案匹配错误映射现有 DEEPAGENT_* 错误。不用零计数表示未知用量。`PersistentPorts.validate()` 在构造时要求四个基础端口具备异步接口；可选窗口 / 资料端口若提供则检查方法形状。该检查不调用端口，不能证明其服务可用或授权正确。

类型注解和 DTO 不能证明宿主已经履行端口保证。后续执行边界须先 `ResultType.model_validate(response)`（包括已有实例），再调用匹配方法并显式处理 status；匹配通过不等于 confirmed。不能通过 `model_copy(update=...)` 或 `model_construct` 绕过校验后直接消费。实际超时转换、围栏写入、审批核验与幂等提交仍待 PA-04 / 宿主接入。

每个上下文自动分配随机唯一 tool_owner_id，不允许外部指定。一次构造后（包括已开始后失败）不能复用，derive 复制 extras 容器但共享同一次调用的绑定。`context.validate_binding(agent)` 校验原对象与 Session / 完整 Execution / 端口 / owner，不能用新的上下文重新绑定。

PA-03 的内部 `execution/binding.py` 已提供 invoke_bound、stream_bound、abort_bound、close_bound。invoke / stream 在进入原生 API 前校验绑定，并固定传入原构造 Session；无授权、预算或提交端口调用，不返回 AgentEvent。Persistent 构造通过 parts.config.stream_task_lifecycle 注入独立 NativeStreamTaskLifecycle，单轮流的生产者经原生可选接缝登记，取消 / 提前关闭时等待生产者和原生 Session 收尾退出。普通 DeepAgent 默认句柄为 None，个人版保持旧路径。它们不是外层公开 API，也不是可靠执行入口；不能把内部 native result 当成业务完成或送达。第四步 execute_persistent_request 将组合这些接缝及事件协议。

构造绑定自动保存调用私有 InvocationState，持有原 live config / model / AbilityManager 身份及关键声明值快照。关键声明包括 Card ID、owner、系统提示词、cwd / project_root、循环及迭代配置、工作空间自动创建开关、超时、Skills 和工具 Card 身份。替换或漂移在下一调用前拒绝，不重新解释为新的配置版本。模型 / Rail 等可信句柄的内部变化不是完整防篡改检查；不得在固定配置调用中直接热重配、加载插件或绕过接缝调用原生 API。

内部 invocation_scope 原子占用本实例，重叠调用立即失败，不等待宿主业务队列；不同 Session 使用不同构造实例可并行。首次使用固定事件循环，跨循环重用拒绝。活动占用保持到普通调用或原生流生产者收尾退出，finally 使用原绑定释放；调用返回 / 每个流片段交接前复核绑定和停止标记，停止后的迟到结果不当作正常成功。流消费者提前退出须显式 aclose / aclosing；重复取消不能打断后台生产者的取消 checkpoint 和 Session.commit / close_stream。后台不合作时继续保留占用，不把等待超时或消费者停止视为已收敛。

abort_bound 须由独立控制任务发起，只设置原绑定停止标记、取消活动任务并调用原生 abort；停止后不重用本绑定。控制尚在进行或 native call 未退出时 close_bound 拒绝；空闲关闭仅清理捕获的 owner 工具和原生临时资源，不调用 Session.post_run、不清空持久状态、不删除文件。关闭不证明任意外部工具进程或副作用已收敛，ControlPort 鉴权和工具收敛留后续阶段。原生 DeepAgent 方法不加全局检查，个人版保持原行为；完整可靠执行仍待 PA-04。

工厂仍通过 DeepAgentSpec.build 的 parts / Config 路径返回原生 DeepAgent，不新增运行类。构造不调用模型或宿主端口，不投递消息；使用 WorkspaceSpec 时宿主须预建根目录并设置 auto_create_workspace=False。自定义 provider 是可信装配扩展，须遵守构造期无外部业务副作用，并为有状态工具 / Rails 返回独立实例。Persistent 的顶层 tools 拒绝 stateful ToolCard 引用（否则仍指向全局同一实例），应使用 BuiltinToolSpec provider 构造；stateless ToolCard 保留共享 ID。子代理在惰性执行时的全链路隔离仍待后续验证。部分注册失败只回收本次 owner 的 stateful 工具，保留共享工具及其他调用；失败不关闭宿主 Session。形状 / 归属错误映射 DEEPAGENT_CONTEXT_PARAM_ERROR，配置或装配错误映射 DEEPAGENT_CONFIG_PARAM_ERROR，Spec 字段校验沿用 Pydantic。

`execute_persistent_request` 尚未实现或导出。其余本规约中的执行接口和状态为后续目标。

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
7. 运行主体保持 DeepAgent，不新增 PersistentAgent 类、DeepAgent 子类或 Agent 包装门面。专用能力通过 Spec、Rails、工具和可靠交接适配组合。

## 接口契约与错误语义

拟议的最小逻辑表面为：

```text
create_persistent_agent(spec: PersistentAgentSpec, *, context: PersistentBuildContext)
    -> DeepAgent
execute_persistent_request(agent: DeepAgent, input, *, context: PersistentBuildContext)
    -> AsyncIterator[AgentEvent]
```

构造不启动任务，返回原生 DeepAgent。配置类 PersistentAgentSpec 继承已有 [DeepAgentSpec](../../../schema/deep_agent_spec.py)，直接沿用通用字段，只新增 Persistent 专属策略 / 技术限制及能力描述；不包含 deep_agent 子对象，不要求第二份 Spec。运行句柄经 PersistentBuildContext 注入，不进入序列化配置。

仅配置类继承，运行类不继承。PersistentAgentSpec.build 委托 create_persistent_agent，在副本上调用 DeepAgentSpec.build(snapshot, context=working) 复用父类 parts / Config 装配，最终返回 DeepAgent。父类 Spec.build 与 create_deep_agent 共用装配逻辑，并非直接调用关系。不在本模块直接 new Agent 或构造模板后复制；子类的扁平序列化、校验及指纹覆盖专属字段。

execute_persistent_request 是薄适配函数，不是新 Agent 方法：验证调用绑定、组织判断 / 恢复，消费 DeepAgent 公开 stream/invoke 的输出并映射 S_02 事件，不另建模型循环。原生 API 保留，但原生流结束不承担业务终态确认。input 是宿主已持久化 Request 内容值；恢复仍走该适配，由 context.execution.recovery 区分。公开导出待实现时审定。

Spec、构造上下文、能力检查和验收详见 [F_01](../features/F_01_spec-and-deep-agent-construction.md)。构造绑定固定一个 Session / Execution / 配置，不接受另一 context 重绑；通用 Spec 的 owner 传递接缝已在工具注册前接入，未设置 owner 的旧消费者保持原行为。内部 native-call 接缝已使用绑定校验，正式事件执行入口仍待 PA-04。

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
