# S_07 Persistent Agent 构造与调用绑定规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_07 / construction-and-binding |
| 关联模块 | [factory.py](../../factory.py)、[Spec](../../schema/spec.py)、[构造上下文](../../schema/build_context.py)、[执行上下文](../../schema/execution.py)、[绑定接缝](../../execution/binding.py) |
| 最近一次修订日期 | 2026-10-19 |
| 关联 feature | [F_01](../features/F_01_spec-and-deep-agent-construction.md)、[F_02](../features/F_02_execution-protocol.md)、[F_03](../features/F_03_invocation-binding-and-isolation.md)、[F_04](../features/F_04_contract-convergence.md)、[F_14](../features/F_14_workspace-file-read.md) ；[F_20](../features/F_20_continuation.md)（PA-18 联动修订） |
| 设计依据 | [架构设计：构造与隔离](../designs/persistent-agent-architecture.md#41-构造与实例隔离已实现)、[宿主保证](../designs/persistent-agent-architecture.md#34-agentserver-必须提供的保证) |

本规约维护配置、调用身份、依赖形状、原生 DeepAgent 构造和实例内绑定；这些内容从 S_01 分离后由本文唯一维护。

## 1. 范围、术语与适用条件

- 本规约负责 `PersistentAgentSpec`、`ExecutionContext`、`PersistentBuildContext`、`PersistentPorts` 集合及内部绑定接缝。构造和绑定组件已实现。
- 正式业务执行、预算和结局由 [S_01](S_01_public-api-and-execution.md) 定义；端口的业务方法由各主责规约定义，本文只定义集合与构造校验。
- 运行主体仍为 DeepAgent，不新增 PersistentAgent 运行类、DeepAgent 子类或包装门面。复用 [Harness 构造规约](../../../docs/specs/S_01_public-api-and-construction.md) 的 Spec → parts → Config 路径；本模块不直接复制或改写私有装配字段。
- 业务 Session 标识与运行时 Session 对象不同。正式执行要求的空 Session、NoopCheckpointer 及调度前提见 S_01；低层构造成功不能证明这些执行条件已满足。

## 2. 数据结构与事实归属

| 对象 | 事实与作用域 | 权威 / 持有者 | 修改入口 |
| --- | --- | --- | --- |
| `PersistentAgentSpec` | 可序列化的 Agent 配置；不含 Session、端口、锁、owner 或凭证句柄 | 宿主发布的原 Request 配置 | 发布新配置；固定调用不热重配 |
| `ExecutionContext` | 单次执行的不可变归属、配置和授权快照 | 宿主提供的 frozen dataclass | 宿主为每次 Execution 创建新值 |
| `PersistentBuildContext` | 一次构造的真实 Session、端口及工具 owner | 本调用运行时 | 构造校验、claim 和绑定校验 |
| `ConstructionBinding` / `InvocationState` | 原对象身份、关键声明值、占用、停止及清理状态 | 本实例私有状态 | 内部绑定接缝 |
| `InvocationRef` / `ProtocolModel` | 事件及宿主响应共用的归属和 DTO 约束 | [protocol.py](../../schema/protocol.py) | 构造、重新校验及归属匹配 |

Spec 继承 DeepAgentSpec，JSON 字段平铺、拒绝额外字段。`schema_version=1`；`enabled_features` 允许 `construction`、`workspace_read`、`workspace_write`、`window_management`，默认仅含 `construction`，集合不能为空。办公空间能力及 share_file_max_bytes 的依赖和行为由 [S_04](S_04_tools-and-workspace.md) 定义；不支持 `execution` 能力声明或未实现的策略字段。

续做追溯上限由 [F_20](../features/F_20_continuation.md) D-06 确认；两个容量字段原为 PA-18 的最终请求容量检查新增，该检查已于 2026-10-19 移除（F_20 D-09），字段仅为可选兼容字段，不参与运行判断；PA-11 阈值由 [S_03](S_03_context-and-windows.md) 定义：

| 字段 | 约束与默认值 | 生效与失败边界 |
| --- | --- | --- |
| continuation_chain_limit | 正整数或 None，默认 None | 续做追溯上限；配置后所有 Execution 均声明 read_execution_record。正式续做开放后，未配置在任何事件之前拒绝；新任务和低层构造不要求提供执行链接口 |
| model_input_limit_tokens | 正整数或 None，默认 None | 旧容量配置兼容字段，仍序列化并参与 Spec 指纹；不控制执行准入或 PA-11 换窗 |
| capacity_safety_margin_tokens | 非负整数或 None，默认 None | 旧容量配置兼容字段，仍序列化并参与 Spec 指纹；不作为换窗配置门槛 |

正式 execute / resume 与启用 window_management 的构造均不要求这两个旧容量字段，也不校验它们与模型 context_window、max_tokens 的旧四项配置组合。单字段的类型与取值约束保持不变；给出的兼容值仍参与完整 Spec 指纹，不能静默删除或改写。PA-11 每次调用前从当前 ModelContext.context_window_tokens() 解析有效窗口，按其 80% 判断换窗；不要求额外配置模型独立输入上限、安全余量或输出预留，原生模型参数仍按 core 的配置契约处理。宿主须对同一 Request 传入同一发布版本和指纹。context_engine_config 经 JSON 快照后由 Persistent 装配边界校验并还原为 ContextEngineConfig，保留其 tokenizer 与缓存设置，不修改发布快照或指纹；PA-11 换窗计数读取当前 ModelContext.token_counter()，复用 core 已选择的计数器。

构造经 JSON 往返生成配置副本；`fingerprint()` 对父类及专属字段全部计算规范 JSON 的 SHA-256，能力集合先排序。配置版本、主体和权限上限由宿主固定，不能仅凭声明了开关推断能力可运行。

ExecutionContext 必须包含 `namespace_id, agent_id, session_id, request_id, execution_id, source_ref`，以及 `config_version, config_fingerprint, principal_ref, startup_scope, source_scope, policy_revision, capability_snapshot`。`recovery_ref` 可选，指向同一 Request 更早的待接续 Execution，不能等于本次 execution_id；新的 Request 不带它。该值本身不能证明跨执行归属，宿主须核对关联；本期没有执行代次、租约或执行权句柄。

InvocationRef 定义上述六元归属；响应须重新 `model_validate`，再检查归属及调用关联。ProtocolModel 为 frozen Pydantic 模型，拒绝额外字段、校验默认值并重新校验已有实例。不能用 `model_construct` 或 `model_copy(update=...)` 绕过消费边界。标识为 1～512 字符且不含空白或控制字符；文本、原因和摘要的完整约束引用源码，各业务规约只补充其语义。

## 3. 不变量

1. **I-1 单次绑定：** 一个构造上下文只用于一次构造尝试；claim 后即使装配失败也不能复用。派生上下文复制 extras 容器，但共享同一次绑定，不能绑定另一 Session、Execution 或端口集合。
2. **I-2 实例隔离：** 同一 Agent 的不同业务 Session 使用不同可变运行实例；Rail、有状态工具和带凭证客户端不能跨调用共享可变状态。
3. **I-3 owner 隔离：** 每个上下文自动分配随机唯一 tool_owner_id，不接受外部指定。进程级 Runner.resource_mgr 中只释放本 owner 的有状态工具，不清理其他调用或共享工具。
4. **I-4 固定配置：** 调用前及返回 / 流片段交接前复核原绑定和停止标记。原 live config、model、AbilityManager 身份或关键声明漂移时拒绝，不能解释成新的配置版本。
5. **I-5 占用收敛：** 本实例重叠调用立即失败；首次使用固定事件循环，跨循环复用拒绝。流生产者和原生 Session 收尾退出前保留占用，消费者结束或等待超时不等于后台已收敛。

## 4. 接口契约

### 4.1 `create_persistent_agent(spec, *, context) -> DeepAgent`

入口经 [外层包](../../__init__.py) 懒导出。调用方须提供真实 `openjiuwen.core.single_agent.Session`；其 Session ID、Spec Card ID、完整配置指纹和能力集合分别与 ExecutionContext 一致。CommitPort、BudgetPort 为必需异步端口，window_backend 必须为 BaseKVStore；可选端口提供后也校验方法形状。

| 集合成员 | 构造要求 | 方法契约维护位置 |
| --- | --- | --- |
| `commit` | 必需；异步 record、read_execution_log | [S_02](S_02_events-and-result-handoff.md) |
| `budget` | 必需；异步 admit、report | [S_01](S_01_public-api-and-execution.md#44-模型预算与用量) |
| `window_backend` | 必需；BaseKVStore 实例 | [S_03](S_03_context-and-windows.md) |
| `group_chat`、`knowledge` | 可选；提供时校验异步方法 | [S_09](S_09_group-chat-and-knowledge.md) |
| `workspace_access` | 可选；workspace_read 开启时必需 | [S_04](S_04_tools-and-workspace.md) |

工厂不调用模型或端口、不读写窗口后端、不发送消息，不提供假成功端口。WorkspaceSpec 的根目录须由宿主预建并关闭 auto_create_workspace。有状态 ToolCard 引用被拒绝，应由 BuiltinToolSpec provider 创建独立实例；stateless ToolCard 可保留共享 ID。自定义 provider 是可信扩展，须遵守构造期无外部业务副作用并为有状态工具 / Rail 返回独立实例。

Spec.build 委托本工厂，工厂在副本上调用父类 DeepAgentSpec.build 的共享装配路径，并非直接调用 create_deep_agent。装配注入 NativeStreamTaskLifecycle、PersistentModelCallRail 和 PersistentToolIntentRail；workspace_read 开启时另加授权读取与 share_file Rail，workspace_write 的依赖与装配见 S_04。配置 continuation_chain_limit 时另装配 ExecutionRecordRail，工具定义对该 Spec 的所有 Execution 一致，仅合法续做可查询本次已核验链中的记录，新任务调用返回不可用。模型 Rail 依赖正式入口 emitter 才生效，在 before_invoke 为本 ReActAgent 实例注入最终请求检查，顺序与暂停语义见 S_01 第 4.4 节；通用 core 接缝默认关闭、不引入 Persistent 类型。写意图 Rail 的低层调用限制见 S_08。

| 错误边界 | 结果与副作用 |
| --- | --- |
| 直接构造 Spec / DTO 的非法字段 | Pydantic ValidationError；尚未建立有效配置 |
| 归属、上下文、owner 或端口形状不符 | BaseError，DEEPAGENT_CONTEXT_PARAM_ERROR |
| 指纹、能力、工作空间配置、有状态 ToolCard 或装配失败 | BaseError，DEEPAGENT_CONFIG_PARAM_ERROR；已有 BaseError 原样传播，其他工厂异常包装为该码 |
| 部分注册后失败 | 只回收本次 owner 的有状态工具；保留共享工具和其他调用，不关闭宿主 Session；已 claim 的上下文不可复用 |

### 4.2 内部 `invoke_bound / stream_bound / abort_bound / close_bound`

定义位于 [binding.py](../../execution/binding.py)，不是外层业务 API。invoke / stream 校验原对象并固定传入原构造 Session，不直接完成授权、预算记账或业务终态确认；原生结果不能表示任务完成或送达。

```text
invoke_bound(agent, inputs, *, context)
stream_bound(agent, inputs, *, context, stream_modes=None)
abort_bound(agent, *, context) -> None
close_bound(agent, *, context) -> None
```

四者都是异步入口，stream_bound 返回原生异步流；agent 与 context 必须是原构造对象和绑定，inputs / stream_modes 沿用原生 API 的输入协议。输入经副本交给原生调用，不能通过传入新 context 重新绑定运行对象。

运行快照保留 Card ID、owner、系统提示词、cwd / project_root、循环及迭代配置、自动创建工作空间开关、超时、Skills 和工具 Card 身份等关键声明。可信模型或 Rail 句柄的内部变化不属于完整防篡改保证；固定调用不得直接热重配、加载插件或绕过接缝调用原生 API。

## 5. 资源与生命周期

流消费者提前退出须显式 aclose / aclosing；重复取消不能打断生产者的取消 checkpoint 和 Session.commit / close_stream。finally 使用原绑定释放占用；后台不合作时继续保留占用，不开放超时接管。

abort_bound 由独立控制任务请求停止、取消活动任务并调用原生 abort；停止后不重用绑定。控制或 native call 尚未退出时 close_bound 拒绝。空闲关闭仅清理捕获的 owner 工具和原生临时资源，不调用 Session.post_run、不删除文件或持久状态。服务端控制统一走 [S_05](S_05_confirmation-and-continuation.md) 的包装入口，不能直接以 abort_bound 代替业务收尾。

## 6. 适用限制与兼容性

- 构造成功及端口类型符合不能证明宿主授权、持久化、可用性或完整执行闭环；子代理全链路隔离和恢复尚未开放。
- 关闭不证明任意外部进程或副作用已收敛。通用 DeepAgent 默认不启用本绑定检查和流生命周期接缝，个人版行为保持原通用契约。
- 原 S_01 的配置、构造、绑定及公共协议原语由本文接管；S_01 只引用，不再维护第二份端口集合或绑定规则。

## 7. 关联规约与验证索引

正式执行消费本绑定，见 S_01；工作空间、工具事件和资料端口分别由 S_04、S_08、S_09 定义；公共构造与 Rail 机制引用 Harness 原规约。

| 条款 / 接口 | 检查入口 |
| --- | --- |
| 配置、工厂与失败回收；I-1～I-3 | [test_construction.py](../../../../../tests/unit_tests/harness/persistent_agent/test_construction.py) |
| DTO 与归属 | [test_protocol.py](../../../../../tests/unit_tests/harness/persistent_agent/test_protocol.py) |
| 固定配置、占用、流关闭及资源清理；I-4～I-5 | [test_binding.py](../../../../../tests/unit_tests/harness/persistent_agent/test_binding.py) |
| workspace_read 的装配依赖 | [test_workspace_read_rail.py](../../../../../tests/unit_tests/harness/persistent_agent/test_workspace_read_rail.py) |

测试入口用于定位检查；宿主真实授权、持久化和跨仓验证证据在关联 feature / [开发计划](../designs/persistent-agent-development-plan.md) 中维护。
