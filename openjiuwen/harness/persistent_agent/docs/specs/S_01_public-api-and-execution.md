# S_01 Persistent Agent 执行入口与结局规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_01 / public-api-and-execution |
| 关联模块 | [entries.py](../../execution/entries.py)、[participation.py](../../execution/participation.py)、[model_rail.py](../../execution/model_rail.py)、[RequestInput](../../schema/request.py)、[预算结果](../../schema/port_results.py) |
| 最近一次修订日期 | 2026-10-24 |
| 关联 feature | [F_05](../features/F_05_execution-entries.md)、[F_07](../features/F_07_group-participation.md)、[F_15](../features/F_15_tool-intent-before-side-effect.md)、[F_18](../features/F_18_output-handoff.md)、[F_24](../features/F_24_permission-confirmation.md)；[F_20](../features/F_20_continuation.md)（PA-18 联动修订） |
| 设计依据 | [架构设计：执行入口](../designs/persistent-agent-architecture.md#42-执行入口与原生推理循环)、[主流程](../designs/persistent-agent-architecture.md#51-主流程)、[故障与结局](../designs/persistent-agent-architecture.md#81-故障影响错误分类与结局) |

本规约维护正式执行入口、群消息参与判断、模型预算及结局判定，不维护底层构造、事件 Schema 或窗口存储格式。

## 1. 范围、术语与适用条件

执行主体为原生 DeepAgent，入口组合现有 stream 和绑定接缝，不另建模型 / 工具循环。构造、Session / Execution 身份和端口集合由 [S_07](S_07_construction-and-binding.md) 定义；确认、控制及续做调用由 [S_05](S_05_confirmation-and-continuation.md) 定义。

| 行为 | 当前边界 |
| --- | --- |
| 执行编排、原生流映射、预算与结局；群参与判断 | 已有组件实现和测试入口 |
| 群消息忽略、参与判断失败或预算暂停 | 可在构造 DeepAgent 和加载窗口之前结束 |
| 正式任务执行与确认恢复 | PA-06 窗口组装、PA-10 会话记录与 PA-09 原生回复适配已接入并通过组件测试；员工审批身份与持久路由由 JiuwenSwarm JM-26 管理。真实飞书与更广泛的 AgentServer 验收仍待完成 |
| 带 recovery_ref 的续做 | PA-18 组件已实现；正式入口按 S_05 第 4.4 节继续拒绝，等待 PA-12 归档查询及 M2c 联合放行 |

一期只开放单轮执行；Spec 必须显式 `enable_task_loop=False`，`subagents` 为空，`enable_async_subagent / enable_subagent_runtime / add_general_purpose_agent` 为 false。声明和实际装配结果均须检查，不能在运行时悄悄关闭已发布开关。

宿主保证同一业务 Session 只有一个活动 Execution，每次提供新的空 runtime Session 并注入 NoopCheckpointer，不额外调用 pre_run / post_run。原生生命周期仍照常运行，checkpointer 不读写存储；跨 Execution 的状态由 [S_03](S_03_context-and-windows.md) 维护。排队、发布、渠道绑定、身份鉴别、预算账户和投递由 AgentServer 负责。

## 2. 数据结构与事实归属

RequestInput 是宿主已保存的任务输入值，定义在 [request.py](../../schema/request.py)。

| 字段 / 条件 | 语义与校验 |
| --- | --- |
| kind | group_message、direct_message、scheduled |
| text、reply_target | 非空白任务正文与受控回复目标 |
| source | 群消息须为 S_09 的 GroupMessage；私聊须为 DirectMessageSource；定时任务只能为空 |
| attachment_refs | 最多 128 项受控附件引用；引用不授予读取权限 |
| continue_instruction | 可选非空白继续指示；只有 ExecutionContext 带 recovery_ref 才合法 |

DirectMessageSource 携带消息 ID、发送者 ID / 名称及带时区的服务端接收时间，时间归一化为 UTC。`trigger_message_id` 用于关联来源并从背景排除触发消息。新消息属于新的 Request，不能当作旧请求的续写或用 follow_up / steer 绕过宿主队列。

ExecutionControl、emitter 和原生运行对象均为本次执行的内存状态。最终业务事实取决于 S_02 的记账确认，运行任务返回和 DTO 构造成功都不能替代它。

## 3. 不变量

1. **I-1 指定任务：** 本模块只执行宿主指定的 Session / Request / Execution；原 Request 从开始、确认恢复到续做固定同一版本的 Spec。
2. **I-2 分支先后：** 新群消息任务先完成独立参与判断；忽略或判断失败不构造正式 Agent、不加载或写入窗口。合法续做跳过参与判断，由模型根据进度决定查证、询问、结束或继续必要操作。私聊和已触发定时任务直接进入任务路径，不额外记 participation.decided。
3. **I-3 预算先行：** 每次真正模型调用先获得有效准入；真实重试使用新的 model_call_id；未知用量不伪造为零计数。
4. **I-4 结局真实：** 原生流结束、answer、文件存在或候选输出产生不等于已完成、已发送或已送达，确认语义以 S_02 为准。
5. **I-5 致命失败：** 关键失败不能被原生普通错误转换吞掉；尚未开始的模型及工具调用均被阻止，入口最终抛出原致命异常并清理。

## 4. 接口契约

### 4.1 `execute_persistent_request(spec, context, inputs) -> AsyncIterator[AgentEvent]`

入口经 [外层包](../../__init__.py) 导出，为服务端消费的异步生成器。调用方提供 S_07 的构造上下文和本规约输入；模式、隔离、输入源类型、群端口存在性及 recovery_ref 等基础条件在参与判断和构造前校验。

新群消息任务随后调用 participation；若已选择终结，直接结束。选择 respond / intervene 后才取得会话记录与窗口组装接入点。合法续做跳过这一轻量判断，execution.started 不带新的 decision_event_id。因而“记录组件不可用时无事件”只适用于尚未产生参与判断事件的任务路径，不能作为所有群消息的保证。

成功产出的流由宿主持久在线消费至结束，包括收到 paused / final 后的清理阶段；前端连接只订阅展示。提前关闭该流会取消内部任务并等待原生流与清理结束，不生成结束事件。停止和取消须走 S_05 的独立控制入口。

PA-06 已实现新 Request 的窗口组装，PA-18 在同一组装器接入续做分支。群 Session（注入 GroupChatPort）在 PA-23 接入前、注入 KnowledgePort 的执行在 PA-13 接入前明确拒绝。新任务加载到等待中的调用或未配对工具调用时，以 DEEPAGENT_CONTEXT_PARAM_ERROR 校验拒绝，不追加内容；合法续做按 S_05 第 5.3 节先核对旧等待现场归属、补位并清除过期中断，再追加材料。校验拒绝即使晚于 execution.started，服务端也不自动续做。Persistent 构造拒绝 ContextAssembleRail、PersonalContextRail、ContextProcessorRail（含子类）及显式启用的三个按条数截断参数；详见 S_03 / F_06。

### 4.2 群消息参与判断

GroupChatPort 的授权、消息与完整性规则由 [S_09](S_09_group-chat-and-knowledge.md) 维护。群消息都请求最近 8 条记录，排除触发消息，最多进行 3 次判断尝试；只提供职责、当前消息和少量群背景，`tools=None`，不加载正式窗口。若当前群消息明确 @ 当前智能体，模型不得忽略，结果记为 `participation.decided(respond)` 并进入正常执行；`effort` 为 `extended` 时附带等待提示。判断尝试耗尽时仍记 `respond` 且不附带，不记 failed。决策事件由 `execution.started` 引用。未明确 @ 时，@ 和回复关系作为模型判断证据。最近消息中已撤回、正文暂缺（unavailable）和普通但无正文的消息分别标注，正文暂缺不能报为已撤回。

| 情况 | 可观察结果 |
| --- | --- |
| 明确 @ 当前智能体 | 走同一参与判断；模型返回 ignore 或 intervene 时仍记 participation.decided(respond) 并进入 execution.started。`effort` 为 `extended` 才附带等待提示。三次尝试仍无合法输出时记 respond 且不附带，不记 failed。预算拒绝仍暂停 |
| ignore | model.completed、participation.decided 后记 final(ignored)，不交出输出 |
| respond / intervene | 保留决策事件引用，任务路径的 execution.started 携带该引用 |
| 群记录 incomplete、读取失败、模型或 JSON 判断失败 | 有限尝试；耗尽时 final(failed)，不能记为 ignored |
| 模型不可用 | final(failed)，不构造正式 Agent |
| budget denied / admit 异常 | 不调用判断模型，分别 paused(budget_denied) / paused(error) |

### 4.3 错误与当前接入门禁

| 边界 | 对外结果与副作用 |
| --- | --- |
| 非法 RequestInput | 入口包装为 BaseError，DEEPAGENT_INPUT_PARAM_ERROR；直接构造 DTO 的字段错误为 Pydantic ValidationError |
| 未开放模式、缺少群端口、续做门禁或接入点尚不可用 | BaseError，DEEPAGENT_CONFIG_PARAM_ERROR；不把未接入能力降级为新任务或空窗口；续做门禁在任何事件之前拒绝，群判断之后的其他 getter 拒绝不撤销已有判断事件 |
| recovery_ref 非法 | DEEPAGENT_CONTEXT_PARAM_ERROR，在任何事件之前拒绝，详见 S_05 第 4.4 节 |
| 启用 window_management 时容量配置不完整或组合不成立 | DEEPAGENT_CONFIG_PARAM_ERROR，构造时拒绝；配置契约见 S_07 |
| 非法构造上下文 / checkpointer | BaseError，DEEPAGENT_CONTEXT_PARAM_ERROR |
| 会话记录加载 / 保存失败或返回形态非法 | DEEPAGENT_RUNTIME_ERROR；加载失败不运行原生流，保存失败不记结束事件 |
| 会话记录无效（加载时） | 框架校验错误 DEEPAGENT_CONTEXT_PARAM_ERROR 原样透传；不运行原生流、不退回空窗口，服务端不续做（S_03 第 4.1 节） |
| Rail 注册后发现会卸载消息的上下文处理器 | DEEPAGENT_CONFIG_PARAM_ERROR，登记 fatal 后在原生退出时抛出；不调用模型、不保存、不记结束事件 |
| 窗口组装失败 | 已有 BaseError 保留；其他异常或非法返回包装为 DEEPAGENT_RUNTIME_ERROR，不运行半装配窗口 |
| 关键记账失败 | 按 S_02 登记 fatal 并抛出；不降级为 paused(error) |
| 实例清理失败 | 无先前异常时抛 DEEPAGENT_RUNTIME_ERROR；失败收尾不掩盖已有原异常 |

### 4.4 模型预算与用量

预算方法定义于 [ports.py](../../ports.py)，账户与结算由宿主维护。

| 方法 | 输入 / 结果 | 失败语义 |
| --- | --- | --- |
| `admit(execution, model_call_id) -> BudgetAdmission` | admitted 必须含 reservation_ref；denied 必须含 reason 且无 reservation_ref | 拒绝不调用模型并暂停；异常或归属 / DTO 错配为 paused(error) |
| `report(execution, model_call_id, usage) -> BudgetReport` | confirmed 含 receipt_ref；rejected 含 reason 且无 receipt_ref | 调用或校验异常只记诊断，不撤销已发生模型调用；宿主对账 |

两种响应均先重新校验，再匹配完整调用归属和 model_call_id。ModelUsage 的 known 必须含两个非负 token 计数；unknown 必须无计数且有原因。report 响应中的 rejected 由宿主回报，当前 Rail 不把它转换为执行暂停；不得凭 report 返回或 model.completed 日志推导结算完成。

原生模型预算由 PersistentModelCallRail 完成，优先级 10；它在正式 emitter 存在时工作，并在模型及工具调用前检查停止 / fatal。before_invoke 为本实例注入 core 的最终请求检查：系统提示、窗口消息、附件和工具定义全部装配后，依次执行换窗处理（启用 window_management 时，PA-11）、预算准入、模型调用。before_model_call 只保留停止 / fatal 检查，不再执行预算准入。每次实际重试都重新检查并申请新的 model_call_id；接缝返回的暂停结果不经模型异常重试。PA-18 不检查模型容量（2026-10-19 移除，见 [F_20](../features/F_20_continuation.md) D-09）。

参与判断独立执行预算协议，不通过上述原生最终请求接缝。正式 execute / resume（新任务、正常确认恢复与续做）不要求容量配置；模型容量只在启用 window_management 时由 PA-11 使用，见 S_03、S_07。低层构造及通用 ReActAgent 的默认行为不变。

停止、取消或致命错误后，控制 Rail 拒绝尚未调用的工具时，该调用的工具消息为“本次执行正在结束，该工具未调用。”；内部强制结束载荷只用于入口判断，不作为工具结果写入窗口。此说明不改变结局或承诺撤销已开始的外部操作，设计与独立验证见 [F_05](../features/F_05_execution-entries.md)。

## 5. 跨接口执行流程与结局

普通任务路径为：基础校验 → 必要的参与判断 → 获取接入点 → 构造并复核模式 → execution.started → 加载记录 → 组装输入 → 原生 stream → 判定并锁定结局 → 整批准备并交出候选 → 保存记录 → 记 paused / final → 关闭实例。确认恢复不追加窗口，不重复记 started，流程由 S_05 定义。

结局先处理 fatal，再采用锁定前接受的 cancel / stop；随后依次考虑被拦截调用、模型暂停标记、answer 和其他原生结果。cancel 为 final(cancelled)，stop 为 paused(stopped)，有拦截为 paused(awaiting_confirmation)，预算拒绝为 paused(budget_denied)，容量不足为 paused(context_unfit)，容量未知或计数失败为 paused(error)。原生错误、异常、迭代耗尽或无结果默认 paused(error)；不能把错误文本作为候选回答。明确判断失败可 final(failed)，普通工具失败不自动终结 Request。

结局锁定与控制截止由 S_05 定义；锁定不能证明状态保存或终态提交成功。锁定后按 [S_02](S_02_events-and-result-handoff.md#44-候选输出交接) 第 4.4 节整批准备候选：最终答案取 after_invoke Rail 之后的实际结果，非空白时与本 Execution 经 `share_file` 登记的文件一起准备、分段并派生身份；停止和取消不交接。任一项准备失败时整批不交，completed 改为 paused(error)，其他暂停保留原因并在 detail 中补充。超长答案拆成有序分段，不截断，也不再以暂停结束；非法字段或保存失败仍抛异常。随后保存记录（结束信息带交接摘要，见 S_03），再记 paused / final；结束事件首次记账失败时按原事件补交其引用的候选一次、再按原事件重试一次（S_02 第 4.2 节）。等待确认暂停的拦截调用参数按事件容量截断并标记。准备候选前先同步实时窗口，以最终窗口中的有效 `share_file` 结果确定文件；确认恢复在原生恢复之前把确认链的有效登记计入上限（S_04 第 4.3 节）。

原生片段映射为 reply.delta；正文与工具调用同现时产出最多 500 字符的 progress.updated；模型完成或失败产出 model.completed；交互片段汇入暂停载荷。具体事件字段、序号和容量处理仅在 S_02 维护，工具事件产生条件由 S_08 维护。

## 6. 当前限制与兼容性

WindowAssembler 与 SessionStateStore 已接入，容量验收仍待完成；GroupMessageReader 与续做的接入门禁仍见 [seams.py](../../execution/seams.py)，NativeReplyAdapter 已接入并见 S_05。组件测试用模拟接入不证明真实存储、跨仓授权、投递或续做已开放。enabled_features 不增加 execution，完整集成需按[唯一开发计划](../designs/persistent-agent-development-plan.md)验证。

本规约合并原 S_01 的执行规则与 S_02 的原生流映射、预算及结局编排。构造与绑定转由 S_07 维护，控制、确认回复和续做转由 S_05 维护；废止旧的执行权围栏和“所有暂停均占住队列直到终态”描述，不提供超时自动接管或精确程序恢复保证。

## 7. 关联规约与验证索引

| 规约 | 唯一负责的主题 |
| --- | --- |
| 本文 S_01 | RequestInput、执行编排、参与判断、BudgetPort 和结局 |
| [S_02](S_02_events-and-result-handoff.md) | AgentEvent、CommitPort、候选输出与交接确认 |
| [S_03](S_03_context-and-windows.md) | 窗口、会话状态记录、归档与本 Session 历史 |
| [S_04](S_04_tools-and-workspace.md) | 办公空间授权、路径及文件读取适配 |
| [S_05](S_05_confirmation-and-continuation.md) | ConfirmReply、确认恢复、控制截止与续做规则 |
| [S_07](S_07_construction-and-binding.md) | 配置、ExecutionContext、构造、端口集合和绑定 |
| [S_08](S_08_tool-effects-and-intent.md) | 副作用分类、写意图屏障和工具完成事件 |
| [S_09](S_09_group-chat-and-knowledge.md) | 群聊、KnowledgePort 及资料 DTO |

- 输入校验：[test_request.py](../../../../../tests/unit_tests/harness/persistent_agent/test_request.py)。
- 执行、参与判断、预算、失败及最终回答：[test_entries.py](../../../../../tests/unit_tests/harness/persistent_agent/test_entries.py)。
- 预算 DTO 与用量：[test_protocol.py](../../../../../tests/unit_tests/harness/persistent_agent/test_protocol.py)。
- 原生 DeepAgent 的既有行为引用 [Harness 架构规约](../../../docs/specs/S_02_deep-agent-architecture.md)；验证范围与实际结果在关联 feature / 开发计划中维护。
