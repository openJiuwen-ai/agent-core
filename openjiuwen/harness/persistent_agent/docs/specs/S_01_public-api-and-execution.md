# S_01 Persistent Agent 调用与执行规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | PA-01 契约、PA-02 DeepAgent 构造、PA-03 实例内 Session 隔离已按各自边界验收；PA-04 已按架构设计收敛执行上下文与宿主端口；PA-05 执行、恢复与控制入口已实现，会话状态记录（PA-10）与交互适配（PA-09）接入前正式入口明确拒绝 |
| 关联模块 | 外层 `persistent_agent/__init__.py` 与内部 `persistent_agent/persistent_agent/{factory.py,ports.py,schema/,execution/}` |
| 最近一次修订日期 | 2026-10-09 |
| 关联 feature | [F_01 Spec 与 DeepAgent 装配](../features/F_01_spec-and-deep-agent-construction.md)、[F_02 执行协议](../features/F_02_execution-protocol.md)、[F_03 执行绑定](../features/F_03_invocation-binding-and-isolation.md)、[F_04 契约收敛](../features/F_04_contract-convergence.md)、[F_05 执行、恢复与控制入口](../features/F_05_execution-entries.md)、[F_07 群消息参与判断](../features/F_07_group-participation.md)；开发计划 PA-01～PA-03、PA-05～PA-07、PA-09、PA-04 |
| 设计依据 | [Persistent Agent 详细架构设计](../designs/persistent-agent-architecture.md) §4、§5、§13、§14；旧版详细架构 §4～7 仅供参考（见 Git 历史） |

当前已实现并独立验证 PA-01 的契约层、PA-02 的构造入口及 PA-03 的实例内调用绑定 / 单轮流生命周期，PA-04 已把执行上下文和宿主端口与架构设计对齐，PA-05 已实现执行、恢复与控制入口及事件映射。下文宿主后端、窗口存储、上下文装配和续做仍是目标协议，不代表已经可运行。“范围 / 边界”之后尚未修订的段落仍沿用旧版架构的表述，与架构设计冲突时以架构设计为准，随对应特性修订。实际构造接口如下：

```python
from openjiuwen.harness.persistent_agent import (
    PersistentAgentSpec, PersistentBuildContext, ExecutionContext,
    PersistentPorts, create_persistent_agent,
    execute_persistent_request, resume_persistent_request, abort_persistent_request,
    RequestInput, DirectMessageSource, ConfirmReply, PermissionResponse, AnswerResponse,
)
```

`PersistentAgentSpec` 继承 `DeepAgentSpec`，JSON 字段平铺，拒绝未知字段与未实现能力。当前仅有 `schema_version=1` 和 `enabled_features={construction}` 两个专属字段：前者拒绝未知 Spec 结构版本，后者与宿主调用的能力快照比对；未实现的运行时策略不预先放进 Spec。

完整配置经 JSON 往返生成副本，规范化 JSON 的 SHA-256 为 `spec.fingerprint()`，覆盖父类及专属字段。Session、ports、锁和 owner 不在 Spec 中。上下文须提供真实 `openjiuwen.core.single_agent.Session`，与 immutable ExecutionContext 的 Agent / Session / 原配置指纹及能力快照匹配；配置版本和主体由宿主固定。ExecutionContext 包含 namespace_id、agent_id、session_id、request_id、execution_id、`source_ref`、config_version、config_fingerprint、principal_ref、startup_scope、source_scope、policy_revision、capability_snapshot 及可选 recovery_ref。架构设计的前提是同进程运行、同一 Session 只有一个执行者，因此不再携带执行代次和执行权句柄。`recovery_ref` 指向同一 Request 中本次要接续的更早 Execution，崩溃续做和非确认暂停后继续都使用它（架构设计 §11.2），不能等于本次 `execution_id`；新的 Request 不带它。

PersistentPorts 已定义异步方法及类型化返回值；工厂仍仅验证形状，不调用端口，也不读写窗口存储后端。不提供假成功端口。`AgentEvent`、`ModelUsage`、群消息与预算结果值对象经外层包懒导出，内部实现位于 `persistent_agent/schema/{protocol,events,context,port_results}.py`。

| 依赖 | 方法 / 类型 | 要点 |
| --- | --- | --- |
| `commit`（必需） | `record(execution, event) -> None` | 所有事件统一 await record；普通事件入队后返回，失败只记诊断，日志缺失不阻塞后续关键记账；`tool.intent`、`execution.paused`、`execution.final` 等自身落盘后返回，失败停止当前执行；final 还须确认 output_refs 所引用的候选输出已可靠保存（架构设计 §9.1） |
| | `read_execution_log(execution, execution_id) -> tuple[AgentEvent, ...]` | 按 local_seq 返回同一 Session、同一 Request 下该次执行已保存的事件；允许缺口，非空不保证完整；失败抛异常 |
| `budget`（必需） | `admit` → `BudgetAdmission`；`report` → `BudgetReport` | 准入只有 admitted / denied，准入须含 reservation_ref；回报只有 confirmed / rejected，确认须含 receipt_ref；调用失败抛异常 |
| `window_backend`（必需） | `BaseKVStore` 实例 | 会话状态记录与窗口归档的存储后端；构造时只检查类型。持久化、命名空间隔离以及 Agent 工具不可访问由服务端保证 |
| `group_chat`（可选） | `recent_messages(execution, cursor, limit)`、`search_messages(execution, GroupMessageQuery)` → `GroupMessagesResult` | 服务端按执行上下文中的 Session 定位群；结果区分 found / empty / incomplete，incomplete 不能当作“没有” |
| `knowledge`（可选） | `resolve` / `read` → `MaterialReadResult` | 命中携带稳定来源 / 范围 / 版本 / 内容引用；empty 要求完整查询，unknown 与 partial / forbidden / unavailable 分开。搜索和授权读取实现待 PA-13 |

所有事件和端口结果继承 `InvocationRef`，绑定 namespace / Agent / Session / Request / Execution / source_ref。DTO 使用 frozen Pydantic 模型并拒绝额外字段；字段校验为 ValidationError，纯归属 / 提案匹配错误映射现有 DEEPAGENT_* 错误。不用零计数表示未知用量。`PersistentPorts.validate()` 在构造时要求记账、预算端口具备异步接口，`window_backend` 为 `BaseKVStore` 实例；可选端口若提供则检查方法形状。该检查不调用端口，不能证明其服务可用、数据持久或授权正确。

类型注解和 DTO 不能证明宿主已经履行端口保证。后续执行边界须先 `ResultType.model_validate(response)`（包括已有实例），再调用匹配方法并显式处理 status。不能通过 `model_copy(update=...)` 或 `model_construct` 绕过校验后直接消费。事件映射与记账调用由 PA-05 接入。

每个上下文自动分配随机唯一 tool_owner_id，不允许外部指定。一次构造后（包括已开始后失败）不能复用，derive 复制 extras 容器但共享同一次调用的绑定。`context.validate_binding(agent)` 校验原对象与 Session / 完整 Execution / 端口 / owner，不能用新的上下文重新绑定。

PA-03 的内部 `execution/binding.py` 已提供 invoke_bound、stream_bound、abort_bound、close_bound。invoke / stream 在进入原生 API 前校验绑定，并固定传入原构造 Session；无授权、预算或提交端口调用，不返回 AgentEvent。Persistent 构造通过 parts.config.stream_task_lifecycle 注入独立 NativeStreamTaskLifecycle，单轮流的生产者经原生可选接缝登记，取消 / 提前关闭时等待生产者和原生 Session 收尾退出。普通 DeepAgent 默认句柄为 None，个人版保持旧路径。它们不是外层公开 API，也不是可靠执行入口；不能把内部 native result 当成业务完成或送达。PA-05 的执行入口组合这些接缝及事件协议。

构造绑定自动保存调用私有 InvocationState，持有原 live config / model / AbilityManager 身份及关键声明值快照。关键声明包括 Card ID、owner、系统提示词、cwd / project_root、循环及迭代配置、工作空间自动创建开关、超时、Skills 和工具 Card 身份。替换或漂移在下一调用前拒绝，不重新解释为新的配置版本。模型 / Rail 等可信句柄的内部变化不是完整防篡改检查；不得在固定配置调用中直接热重配、加载插件或绕过接缝调用原生 API。

内部 invocation_scope 原子占用本实例，重叠调用立即失败，不等待宿主业务队列；不同 Session 使用不同构造实例可并行。首次使用固定事件循环，跨循环重用拒绝。活动占用保持到普通调用或原生流生产者收尾退出，finally 使用原绑定释放；调用返回 / 每个流片段交接前复核绑定和停止标记，停止后的迟到结果不当作正常成功。流消费者提前退出须显式 aclose / aclosing；重复取消不能打断后台生产者的取消 checkpoint 和 Session.commit / close_stream。后台不合作时继续保留占用，不把等待超时或消费者停止视为已收敛。

abort_bound 须由独立控制任务发起，只设置原绑定停止标记、取消活动任务并调用原生 abort；停止后不重用本绑定。控制尚在进行或 native call 未退出时 close_bound 拒绝；空闲关闭仅清理捕获的 owner 工具和原生临时资源，不调用 Session.post_run、不清空持久状态、不删除文件。关闭不证明任意外部工具进程或副作用已收敛。服务端不直接调用 `abort_bound`：停止与取消经 `abort_persistent_request` 发起（架构设计 §10.4），由它在实例构造后、关闭前复用 `abort_bound`；工具收敛留后续阶段。原生 DeepAgent 方法不加全局检查，个人版保持原行为。

工厂仍通过 DeepAgentSpec.build 的 parts / Config 路径返回原生 DeepAgent，不新增运行类。构造不调用模型或宿主端口，不投递消息；使用 WorkspaceSpec 时宿主须预建根目录并设置 auto_create_workspace=False。自定义 provider 是可信装配扩展，须遵守构造期无外部业务副作用，并为有状态工具 / Rails 返回独立实例。Persistent 的顶层 tools 拒绝 stateful ToolCard 引用（否则仍指向全局同一实例），应使用 BuiltinToolSpec provider 构造；stateless ToolCard 保留共享 ID。子代理在惰性执行时的全链路隔离仍待后续验证。部分注册失败只回收本次 owner 的 stateful 工具，保留共享工具及其他调用；失败不关闭宿主 Session。形状 / 归属错误映射 DEEPAGENT_CONTEXT_PARAM_ERROR，配置或装配错误映射 DEEPAGENT_CONFIG_PARAM_ERROR，Spec 字段校验沿用 Pydantic。

PA-05 的执行、恢复与控制入口（`execution/entries.py`，详见 [F_05](../features/F_05_execution-entries.md)）：

- **输入**：`RequestInput` 的 `kind` 为 `group_message / direct_message / scheduled`，`source` 分别是 `GroupMessage`、`DirectMessageSource` 和空；`continue_instruction` 只能随 `recovery_ref` 出现。`ConfirmReply` 含 1～64 项、`tool_call_id` 不重复的 `PermissionResponse`（approve / approve_for_session / reject）或 `AnswerResponse`（answered / timed_out，超时不带回答）。
- **前置拒绝**：在任何事件和构造之前，以下情况抛校验拒绝（`ValidationError` 类）：Spec 未显式关闭 task-loop 或开启了任何子代理开关；Session 未注入 `NoopCheckpointer`；群消息未注入 `GroupChatPort`；带 `recovery_ref`（PA-18 前）；会话状态记录（PA-10）或恢复时的交互适配（PA-09）接入点不可用。构造后还核对装配结果中 task-loop 和子代理确实未开启。已注入群端口时，群消息先做参与判断（[F_07](../features/F_07_group-participation.md)）：忽略和判断失败在构造 DeepAgent 与加载窗口之前结束。
- **编排**：生成器在内部执行任务中运行原生流，自身只转交已记账的事件；构造上下文仍只能使用一次。执行时依次记 `execution.started`、加载会话状态记录、组装窗口（PA-06 接入点）、运行原生流、交出 `output.proposed`、保存会话状态记录、记结束事件，最后关闭实例；确认恢复不组装窗口。窗口组装、群消息读取与结束信息的接入约定见 [S_03](S_03_context-and-windows.md) 与 [F_05](../features/F_05_execution-entries.md) §7，窗口组装和群消息读取（PA-23）未接入时同样明确拒绝。会话状态记录加载或保存失败、关键事件记账失败、实例清理失败都抛运行异常（`ExecutionError` 类）；保存失败时不记结束事件。最终答案超出事件容量（字符数或完整事件字节数，见 [S_02](S_02_events-and-result-handoff.md)）时改判 `paused(error)`，完整答案放进交给会话状态记录的结束信息（`ExecutionEnding.pending_answer`）保存；只有容量超限改判暂停，字段错误和保存失败仍按上述规则抛异常。致命错误登记后，尚未开始的模型调用和工具调用都被跳过，入口抛出最初的致命异常。服务端提前关闭事件流时，入口取消内部任务并等待原生流和清理结束，不产生结束事件。
- **恢复**：从会话状态记录取得等待现场，核对所属 Request、交互 ID、回复类型与问题文本后，按交互 ID 组装原生 `InteractiveInput`；回复无效时抛校验拒绝，不记账、不保存。恢复不记 `execution.started`，回复目标取自等待现场。
- **控制**：`abort_persistent_request(context, *, reason)` 把第一个 cancel / stop 记在构造上下文上，之后到达的原因忽略；实例构造后、关闭前复用 `abort_bound`。它不发事件，结局由执行入口给出：cancel 为 `final(cancelled)`，stop 为 `paused(stopped)`。实例清理会等待正在进行的原生停止返回。执行入口在原生调用退出、确定执行结果后，同步锁定本次 Execution 的结局。锁定前接受的控制参与结局判定；锁定后不再接受活动执行的停止或取消，不修改控制原因，也不调用 `abort_bound`。结局锁定不代表持久化成功，状态保存和结束记账仍按原失败规则处理。执行入口因异常退出、进入清理之前，同样同步关闭控制接收：保留已有控制原因和原异常，不产生结束事件。

`enabled_features` 暂不增加 `execution`，M1 集成接入 PA-06、PA-08、PA-09、PA-10 并验证后再声明。其余本规约中的执行接口和状态为后续目标。

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
2. 同 Session 同时只有一个活动 Execution，由 AgentServer 保证（架构设计 §13 第 1 条）；本模块不做执行权检查或写入围栏。
3. Request 固定启动时的配置版本、指纹和权限上限；恢复沿用原版本。当前撤权立即收紧可用能力，新增授权不扩张原 Request 的上限。
4. Agent、Session、Request、Execution、Window、Checkpoint 和 Operation 使用各自身份。新恢复 Execution 仍属于原 Request，不把新消息当作旧请求的续写。
5. `Runner.resource_mgr` 是进程级共享状态；各活动实例须有独立 `tool_owner_id`，并只清理本实例资源。Rail、工具包装器和带凭证客户端的可变状态不跨调用共享。
6. 一次 Execution 内配置与授权快照固定，撤权在下一个 Execution 生效（架构设计 §7.1）；停止与取消经 `abort_persistent_request` 发起、由入口内部的 `abort_bound` 收敛，已发出的外部效果由续做规则查证。
7. 运行主体保持 DeepAgent，不新增 PersistentAgent 类、DeepAgent 子类或 Agent 包装门面。专用能力通过 Spec、Rails、工具和可靠交接适配组合。

## 接口契约与错误语义

拟议的最小逻辑表面为（执行与恢复入口见架构设计 §5.1）：

```text
create_persistent_agent(spec: PersistentAgentSpec, *, context: PersistentBuildContext)
    -> DeepAgent
execute_persistent_request(spec, context, inputs: RequestInput) -> AsyncIterator[AgentEvent]
resume_persistent_request(spec, context, confirm_reply: ConfirmReply) -> AsyncIterator[AgentEvent]
abort_persistent_request(context, *, reason: "cancel" | "stop") -> None
```

构造不启动任务，返回原生 DeepAgent。配置类 PersistentAgentSpec 继承已有 [DeepAgentSpec](../../../schema/deep_agent_spec.py)，直接沿用通用字段，只新增 Persistent 专属策略 / 技术限制及能力描述；不包含 deep_agent 子对象，不要求第二份 Spec。运行句柄经 PersistentBuildContext 注入，不进入序列化配置。

仅配置类继承，运行类不继承。PersistentAgentSpec.build 委托 create_persistent_agent，在副本上调用 DeepAgentSpec.build(snapshot, context=working) 复用父类 parts / Config 装配，最终返回 DeepAgent。父类 Spec.build 与 create_deep_agent 共用装配逻辑，并非直接调用关系。不在本模块直接 new Agent 或构造模板后复制；子类的扁平序列化、校验及指纹覆盖专属字段。

两个入口是薄适配函数，不是新 Agent 方法：内部组合构造、绑定接缝和事件映射，消费 DeepAgent 公开 stream 的输出并映射 S_02 事件，不另建模型循环。原生 API 保留，但原生流结束不承担业务终态确认。inputs 是宿主已持久化的 Request 内容值；崩溃续做和非确认暂停后继续仍走 `execute_persistent_request`，由 `context.execution.recovery_ref` 区分；确认恢复走 `resume_persistent_request`。三个入口及输入值对象经外层包懒导出。

Spec、构造上下文、能力检查和验收详见 [F_01](../features/F_01_spec-and-deep-agent-construction.md)。构造绑定固定一个 Session / Execution / 配置，不接受另一 context 重绑；通用 Spec 的 owner 传递接缝已在工具注册前接入，未设置 owner 的旧消费者保持原行为。内部 native-call 接缝已使用绑定校验，正式执行入口在其上组合事件映射（PA-05）。

| `ExecutionContext` 逻辑部分 | 必须携带或可验证的内容 |
| --- | --- |
| 归属 | `namespace_id, agent_id, session_id, request_id, execution_id` 与可信来源、Session 元数据一致 |
| 配置 | `config_version, config_fingerprint, capability_snapshot` 与运行对象完整匹配 |
| 授权 | `principal_ref, startup_scope, source_scope, policy_revision`；秘密不进入模型或快照 |
| 续做 | `recovery_ref` 指向同一 Request 中待接续的 Execution；不能靠新输入猜测 |

端口及职责（架构设计 §14）：

| 端口 | 作用 |
| --- | --- |
| `CommitPort` | 按 Execution 有序记账，并读取同一 Session、同一 Request 的执行日志 |
| `BudgetPort` | 按稳定 `model_call_id` 申请模型准入并报告实际用量；账户与结算归宿主 |
| 窗口存储后端（`BaseKVStore`） | 由内部组件 `WindowStore` 保存会话状态记录和窗口归档（PA-10、PA-11） |
| `GroupChatPort` / `KnowledgePort` | 读取本 Session 所在群的群聊记录，以及当前获授权的版本化知识 |
| `Workspace` 与文件工具 | 限定办公空间读写；详见 S_04 |

同进程调用失败直接抛异常，不设计“结果未知”状态；群聊、知识和归档检索仍须区分“确定没有”与“没查完或失败”，不能把后者当作空结果。必要端口或原配置不可用时在模型前拒绝。逻辑错误名 `INVALID_SCOPE`、`CONFIG_UNAVAILABLE`、`STALE_EXECUTION`、`BUDGET_EXHAUSTED` 等尚非项目 `StatusCode`，实现时映射现有错误体系。

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

阶段是本模块内部状态，不新增服务端 `ExecutionStatus`。只有宿主确认 `completed / ignored / cancelled / failed` 后，同 Session 下一普通 Request 才可推进。停止不等于取消；等待审批、取消和明确关联的补充走已鉴权控制路径，普通新消息继续排队。审批绑定交互、请求、执行、目标、参数摘要和有效期，重复消费返回已知结果，错配拒绝。

群公开消息的拟议判断为 `ignore | respond | intervene`，记录有限说明、目标、来源与证据版本；`@` 和回复关系只是证据。判断失败或预算不足不能写成 `ignore`。已受理私聊及定时任务记录直接任务策略的 `respond`；已提交决策在恢复时复用。`ignore` 不创建正式窗口或任务投影；参与决策确认后，再确认 `execution.started` 才进入 Loop。模型调用先经预算端口准入，真实重试使用新 `model_call_id`，未知用量由宿主核对。

## 与其它 spec 的关系

- [S_02](S_02_events-and-result-handoff.md) 定义阶段事件的提交确认与输出交接。
- [S_03](S_03_context-and-windows.md) 定义阶段化模型输入及窗口指针。
- [S_05](S_05_checkpoint-and-recovery.md) 定义持久化等待、控制续接与安全恢复。
- 通用 DeepAgent 行为仍以 [Harness 架构规约](../../../docs/specs/S_02_deep-agent-architecture.md) 为准。
