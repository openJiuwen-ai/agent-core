# Persistent Agent 特性计划、开发顺序与进度跟踪（v2）

| 项 | 内容 |
| --- | --- |
| 用途 | 在一个文档中列出要开发的特性、先后顺序和进度 |
| 范围 | 仅限 `openjiuwen/harness/persistent_agent` 模块 |
| 设计依据 | [Persistent Agent 设计（v2）](persistent-agent-design-v2.md)；旧版[详细架构设计](persistent-agent-architecture.md)仅供参考 |
| 上一版计划 | [persistent-agent-development-plan.md](persistent-agent-development-plan.md)（按旧版架构编制，保留不改） |
| 更新日期 | 2026-10-07 |
| 交付分类 | 本期 20 项（PA-01～PA-17、PA-21～PA-23）；独立学习扩展 3 项（PA-18～PA-20）；后续执行模式扩展 3 项（PA-24～PA-26） |
| 当前进度 | 共 5/26 项完成：本期 5/20、学习扩展 0/3、执行模式扩展 0/3；PA-01～03 已按各自独立验收边界完成，PA-22 已按 v2 收敛已实现的契约，M0 完成；PA-04 已用测试替身完成组件验收，正式入口在 PA-08、PA-09 接入前明确拒绝，M1 集成仍待开发 |

## 1. 怎么读这份计划

Persistent Agent 接收一次已经确定归属的任务，在对应会话中运行 Agent，并把执行过程和结果交还给调用方。本模块提供运行对象的构造入口及会话内隔离能力；**AgentServer 决定何时、为哪个 Session 创建实例**，并负责实例调度。同一业务 Agent 可以拥有多个 Session；一个运行实例在同一时刻只处理一个 Session，并行的 Session 使用不同实例。业务 Agent 的身份创建、配置发布、会话排队、群内模型调用额度管理、管理查询、定时计划的创建与触发、结果投递和产品页面均属 AgentServer 等其他层，不列入本清单。定时触发已形成的普通任务可由本模块执行。

**本期目标：支持指定 Session 内执行、按 Execution 生命周期管理上下文与换窗、会话状态记录、记账与执行日志、发起人确认、群聊与历史检索、知识使用和结果交接；崩溃续做与非确认暂停后继续统一由新的 Execution 读取执行日志、由 Agent 自行判断进度，不做程序层面的精确崩溃恢复。** 本文是 [Persistent Agent 设计（v2）](persistent-agent-design-v2.md)的分期交付计划；旧版架构中的 Checkpoint、执行代次与条件更新、提交结果未知查询、事件背压等要求已由 v2 删除，不再作为验收依据。

- 每个 Execution 开始时加载同一 Session 的会话状态记录，结束时按“整理窗口 → 保存记录 → 记账”收尾；新 Session 不继承其他 Session 的私有窗口和未完成工作。
- 用户确认复用 `PermissionInterruptRail` 和原生中断恢复：等待确认时保存含中断状态的记录并结束本次 Execution，发起人点击确认卡片后由新实例恢复；停止和取消由 AgentServer 直接调用执行侧函数，非确认暂停不阻塞后续 Request；补充消息一律作为新的 Request，明确继续的指示由服务端关联到原任务。
- Persistent Agent 运行在 AgentServer 进程内，直接调用记账接口：所有事件统一 await record，按 Execution 提交，服务端按 local_seq 维护日志顺序；普通日志入队或保存失败只记诊断，允许缺失及序号缺口，不阻塞后续关键事件；`tool.intent`（仅写操作）、`execution.paused`、`execution.final` 等待自身落盘才继续，final 还须确认 output_refs 引用的候选输出已可靠保存，关键记账失败停止当前执行；不设计记账“结果未知”状态，队列不随 Agent 实例清理而取消。
- 进程崩溃后，AgentServer 对最近一次已派发的 Execution 既无 `final` 也无 `paused` 且未被主动停止的 Request 自动发起续做；被停止的任务须明确要求继续，停止收尾失败也不自动续做；预算、容量或可恢复错误导致的暂停，可以明确继续，或由服务端确认对应条件恢复后发起，条件未变化时不反复自动续做。上述续做均沿用原 Request，通过 `recovery_ref` 定位该任务最近一次待接续的 Execution，加载 Session 最新窗口、读取日志和归档，再按续做规则继续，不新增 `continuation` 字段；写操作已登记意图但无结果的先查证，不盲目重做。
- 执行或确认恢复入口抛异常（包括记账失败）时，AgentServer 核对本次 Execution 已成功落盘的 final / paused；已有结果按对应状态处理，无结束记录且未被主动停止或取消时按崩溃中断走统一续做，不必等服务重启。续做时机由服务端决定，沿用原 Request，recovery_ref 指向本次中断执行，发起前确认旧执行退出和相关清理完成；停止或取消的收尾异常不自动续做。
- 续做加载到旧中断状态时，先补齐缺失的工具结果、清除 `__react_agent_interruption__`，保留已保存的 `__interrupt_auto_confirm__`，再追加续做内容，防止确认恢复后崩溃导致旧写工具调用被直接重放。正常确认恢复保留中断现场。这部分由 PA-17 实现、M2c 验收，不作为 PA-22 的完成前置。
- 同一 Request 的所有 Execution（含确认恢复、崩溃续做、非确认暂停后继续）使用同一版本的 Spec，包括确认规则和工具定义；由 AgentServer 保留并传入原配置（v2 §13 第 4 条），本模块按执行上下文中的配置指纹校验。资料授权和知识版本则在每个 Execution 开始时重新解析，二者不混用。
- DeepAgent 的构造、任务循环、模型与工具调用、流式输出、中断恢复、权限确认和子代理运行时作为复用基础；AgentServer 提供调度、单一执行者保证、预算、记账与执行日志存储、群聊记录、窗口存储后端和投递。本模块实现上下文管理及这些能力的执行侧适配。

每行只回答三件事：**做什么、怎样证明做成、开工前必须完成什么**。验收场景可以先用模拟调用方和模拟存储验证；“完成”仍需留下测试或联调证据。“开工硬前置”只列缺少它就无法独立实现当前特性的本模块能力，不把最终联调所需的所有特性都列为开工条件。可并行启动时间和联调汇合点见第 3 节。当前特性 ID 为 `PA-01`～`PA-26`，保留原 PA-01～PA-21 编号；历史变更记录中的旧编号不用于当前认领。PA-21 单列跨资料来源的失效管理，避免重复建设知识读取或通用上下文引擎。PA-22 承接 v2 对已实现契约的调整；PA-23 由原 PA-11 拆出，负责群聊记录检索。PA-24～PA-26 承接 PA-04 一期未开放的执行模式。

本文使用的几个词：

| 词 | 在本文中的意思 |
| --- | --- |
| Session（会话） | 一段独立的对话或工作；不同 Session 不共享未完成任务和私有记录 |
| Request（请求） | Session 中的一次具体任务；确认恢复、崩溃续做、停止后继续都会产生新的 Execution，仍关联原 Request |
| 运行实例 | AgentServer 通过本模块工厂为一次活动调用创建的 DeepAgent、专用工具 / Rails 及控制状态；实例只接受指定 Session，用完后清理，不另建 Agent 类型 |
| Window（窗口） | 当前送给模型的工作上下文；容量达到 70% 时先归档旧窗，再生成新窗 |
| 会话状态记录 | 跨 Execution 需要保留的 Session 状态（当前窗口、中断状态、本会话确认选择等），按白名单一次写入；替代旧版的 Checkpoint |
| 执行日志 | AgentServer 保存的事件流，记录每次 Execution 做过什么；崩溃续做时是进度的唯一来源 |
| 宿主端口 | AgentServer 提供的记账与执行日志、群聊记录、预算、知识和窗口存储后端等接口；本计划只实现 Persistent Agent 对这些接口的使用 |

状态使用“待开始、进行中、阻塞、待验收、完成”。“完成”须有对应验收证据；仅写了代码但没有完成验证时标“待验收”。交付分类与开发状态分别记录：第 2 节 A、B、C 组属于本期，D 组属于独立学习扩展，E 组属于后续执行模式扩展；未排入本期不等于阻塞或完成。

构造约束：只继承配置类，不继承运行类。PersistentAgentSpec 继承 DeepAgentSpec，使用单份扁平配置；不新增 PersistentAgent 运行类、DeepAgent 运行子类或 Agent 门面。PA-02 返回原生 DeepAgent，专属字段及调用依赖见 [F_01](../features/F_01_spec-and-deep-agent-construction.md)。PA-01～03 已在本模块约定范围内完成；这不表示宿主运行时、存储后端或 PA-04 执行链路已经接入。

## 2. 要开发的特性

### A. 本期：能创建、能执行一次任务

| ID | 特性：要做什么 | 怎样证明做成 | 开工硬前置 | 状态 | 负责人 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| PA-01 | 定义本期调用及宿主端口契约 | Agent、Session、Request、Execution 与来源关联可校验；缺身份、权限、有效执行权或必需接口时得到明确错误；超时和结果未知不被当作“没有数据”。接口覆盖窗口、资料版本、控制、预算与提交确认，恢复扩展不作为本期完成条件 | 无 | 已完成 | yan_zj | 契约层完成：执行身份含稳定来源和不进入业务 DTO 的宿主 authority handle；基础端口缺失 / 错形状会在 DeepAgent 构造前失败；事件、提交、控制、预算、窗口与版本化资料结果区分 unknown 和有完整查询依据的 empty / not_found。代码：`openjiuwen/harness/persistent_agent/persistent_agent/schema/execution.py:13,19,23`、`openjiuwen/harness/persistent_agent/persistent_agent/schema/protocol.py:27,33,43`、`openjiuwen/harness/persistent_agent/persistent_agent/schema/context.py:16,37,60,82,109,150,164`、`openjiuwen/harness/persistent_agent/persistent_agent/schema/events.py:181,206,225`、`openjiuwen/harness/persistent_agent/persistent_agent/schema/port_results.py:18,63,102,129,152`、`openjiuwen/harness/persistent_agent/persistent_agent/ports.py:26,36,41,45,50,63,68,73,79,89`；测试：`tests/unit_tests/harness/persistent_agent/test_protocol.py:81,91`、`tests/unit_tests/harness/persistent_agent/test_construction.py:152,167`。真实端口后端、运行时权限核验、超时转换和受控写入属于 PA-04 / PA-09～12，不包含在本项。v2 影响：v2 附录 E 所列端口、事件、执行上下文及窗口 / 资料结构的调整由 PA-22 承接，本项完成状态与证据保留。 |
| PA-02 | 提供 Persistent Agent 运行对象的构造入口 | 复用 `DeepAgentSpec` 的 parts / Config 装配及原生 `DeepAgent` 运行类，校验宿主固定配置并绑定本次执行上下文；有效配置可创建对象，缺少必需能力时创建失败；构造本身不运行任务，不另建运行类 | PA-01 | 已完成 | yan_zj | 有效配置返回原生 `DeepAgent`；缺失四个基础端口或注入错形状 ContextStore 时，在 `DeepAgentSpec.build` 前失败；构造不调用模型 / 端口。代码：`openjiuwen/harness/persistent_agent/persistent_agent/schema/spec.py:21,70`、`openjiuwen/harness/persistent_agent/persistent_agent/factory.py:20`、`openjiuwen/harness/persistent_agent/persistent_agent/schema/build_context.py:73,93`、`openjiuwen/harness/persistent_agent/persistent_agent/ports.py:79,89`、`openjiuwen/harness/schema/deep_agent_spec.py:524,600,635`；测试：`tests/unit_tests/harness/persistent_agent/test_construction.py:110,152,167`。正式宿主 providers 与执行入口不属于构造验收，仍待 PA-04。 |
| PA-03 | 固定实例的 Session 归属并隔离内部状态 | 实例接收指定 Session 的调用后，消息、工具上下文、取消信号和清理范围只归该 Session；向活动实例传入另一 Session 会报错，不会读写原 Session 的状态 | PA-02 | 已完成 | yan_zj | 内部原生调用接缝校验固定 Session / Execution / Agent / owner，拒绝错配和同实例并发；隔离取消、owner 工具清理，并等待 Persistent 专用流生产者及原生 Session 收尾退出。代码：`openjiuwen/harness/persistent_agent/persistent_agent/execution/binding.py:41,57,70,97,116,126,137,161`、`openjiuwen/harness/persistent_agent/persistent_agent/execution/stream_lifecycle.py:9,17,57`、`openjiuwen/harness/deep_agent.py:3025,3411`、`openjiuwen/core/single_agent/agents/react_agent.py:2939,3057`、`openjiuwen/core/common/stream_lifecycle.py:8`；测试：`tests/unit_tests/harness/persistent_agent/test_binding.py:188,250,301,327,376`。个人版默认不注入该生命周期分支。PA-04 的正式入口以及通过该入口的 task-loop / 惰性子代理集成验收仍待完成。 |
| PA-04 | 提供执行、恢复与控制入口，复用 DeepAgent 单轮执行并交接过程事件 | `execute_persistent_request(spec, context, inputs: RequestInput)` 与 `resume_persistent_request(spec, context, confirm_reply: ConfirmReply)` 组合构造、`stream_bound` 与 `close_bound`，不另写推理循环；`abort_persistent_request(context, reason=cancel / stop)` 先记录原因再复用 `abort_bound` 请求中断，结束事件统一由执行入口发出：cancel 产出 `final(cancelled)`，stop 产出 `paused(stopped)`，重复控制幂等、首个原因保留，尚未进入原生调用时在模型或工具调用前检查并收尾。`RequestInput`、`ConfirmReply` 按 v2 §5.1 校验：群消息在 PA-06 接入前明确拒绝；`continue_instruction` 只能随 `recovery_ref` 出现，带 `recovery_ref` 的执行在 PA-17 接入前明确拒绝；交互回复只校验结构、交互 ID 属于当前等待现场且不重复、答案键对应原问题，所属 Request 与回复目标取自等待现场，无效回复只拒绝本次恢复，不保存、不记账。一期只开放单轮执行：Spec 须显式填写 `enable_task_loop=False`，不启用任何子代理（含 `add_general_purpose_agent`），配置声明与装配结果都检查，不支持的模式在执行前拒绝。checkpointer 按 Session 注入 `NoopCheckpointer` 隔离，执行前检查，不符合就拒绝开始；正常完成、确认暂停与恢复、停止、取消、异常及提前关闭流时，checkpointer 旧状态不覆盖 WindowStore 加载的状态，不经 checkpointer 恢复或保存，原生生命周期与清理照常，个人版不变。原生流式输出映射为 `execution.started`、`reply.delta`、`progress.updated`、`model.completed`、`output.proposed`、`execution.paused / final` 并带 Request / Execution 关联；所有事件统一 await record，普通事件失败只记诊断，关键记账失败停止当前执行并由入口抛出异常。每次模型调用前 `admit`、调用后 `report`：拒绝准入时不调用模型并产出 `paused(budget_denied)`，`admit` 抛异常产出 `paused(error)`，`report` 抛异常只记诊断。最终回答生成一条 `output.proposed`（稳定 output_id、target 为 `reply_target`、share_mode=message）并列入 `final.output_refs`，收尾顺序为“交出候选 → 整理窗口 → 保存会话状态记录 → 记账 final → 清理”；中间结论、工具日志和错误文本不作为回答。原生运行错误（含 `result_type="error"`、最大迭代次数耗尽）默认 `paused(error)`，`final(failed)` 只用于明确终结依据；会话状态加载 / 保存失败及关键记账失败向外抛运行异常，原生内部吞掉的关键失败仍保留分类、阻止后续执行并抛出；输入、Spec 或能力不合法抛校验拒绝。调用方契约见 v2 §13 第 12、13 条（持续消费事件流、入口异常核对）。正式入口只开放已接入并验证的能力：会话状态记录（PA-09）接入前不能执行、不降级为空窗口；组件验收使用测试替身，与 M1 集成验收分开，M1 集成后再声明 `execution` 能力 | PA-02、PA-22 | 已完成 | Song Haoran | 组件验收完成：三个入口、`RequestInput` / `ConfirmReply`、事件映射与记账、预算准入与用量回报（`PersistentModelCallRail`）、结局判定、最终回答交接、确认恢复校验、停止与取消、提前关闭流和 checkpointer 隔离均有测试；会话状态记录与交互适配经接入点用替身验证，正式入口在 PA-09、PA-08 接入前明确拒绝，`execution` 能力待 M1 集成后声明。代码：`openjiuwen/harness/persistent_agent/persistent_agent/execution/{entries,emitter,control,model_rail,seams}.py`、`openjiuwen/harness/persistent_agent/persistent_agent/schema/{request,spec}.py`、`openjiuwen/harness/persistent_agent/__init__.py`；共享接缝 `openjiuwen/core/session/agent.py`、`openjiuwen/core/session/checkpointer/noop.py`；测试：`tests/unit_tests/harness/persistent_agent/{test_entries,test_request}.py`，PA 包 224 passed，共享回归 213 passed，`tests/unit_tests/harness` 5426 passed / 39 skipped。设计与验证见 [F_05](../features/F_05_execution-entries.md)，共享 Session 接缝见 harness [F_07](../../../docs/features/F_07_session-checkpointer-injection.md) |
| PA-05 | 按 Execution 生命周期组织上下文 | 不挂载 ContextAssembleRail、PersonalContextRail 和 ContextProcessorRail 的默认压缩链；一次 Execution 内系统提示词和工具定义不变。Execution 开始时，在已保存的窗口末尾依次追加本次执行头、临时背景块（最近群消息，含被忽略的消息并排除触发消息；知识导航；续做日志）和任务输入；新任务按正常输入追加，崩溃续做与非确认暂停后继续统一使用“续做”执行头并注明 recovery_ref 指向的 execution_id，重新注入原任务输入，有继续指示时一并追加；旧中断状态由 PA-17 在追加前处理，正常确认恢复不追加任何内容；执行中只追加，动态内容经 PromptAttachmentManager 的增量通道追加；排队中的消息只作背景，不成为当前指令 | PA-22 | 待开始 | 待分配 | — |
| PA-06 | 判断是否参与一条群消息 | 人类与其他 Agent 消息走同一判断流程，保留发送者、@、回复关系及附件依据；被 @ 可忽略，未被 @ 可回复或介入。判断是一次独立的轻量模型调用，只带职责、当前消息和少量最近群消息，不加载或写入窗口，不开放写工具；忽略时记账 `execution.final(ignored)`，不产生公开回复、正式任务或改写当前工作目标，被忽略的消息不进入窗口；判断失败有限次重试后报失败，不记为忽略；私聊和已受理定时任务走直接执行路径 | PA-22 | 待开始 | 待分配 | — |
| PA-07 | 可靠交接候选回复和文件结果 | 群聊与其他回复从同一事件出口返回，以稳定 output_id 关联候选内容、来源和目标；私有判断、工具明细与模型片段不直接作为群公开回复。文件须注明目标群及“上传群空间”或“发到群内”的共享方式；`output.proposed` 入队后返回，`execution.final` 记账成功时其 output_refs 引用的候选内容与交接信息已可靠保存；缺少被引用候选时 final 失败，可按原 event_id / output_id 重交，不得删除引用后宣告完成；无关普通日志缺失不阻塞交接，再由服务端处理；交接不等于已送达，办公空间保存不等于群内可见；与宿主验证重复交接不重复投递，投递失败只重试发送、不重跑任务 | PA-01 | 待开始 | 待分配 | — |
| PA-08 | 复用 PermissionInterruptRail 处理用户确认，并响应停止与取消 | 确认完全复用 harness 的 `PermissionInterruptRail` 与原生中断恢复，不新写确认 rail；Spec 新增 `permissions` 字段（复用 `PermissionsSection`，构造时传入 DeepAgent 配置并纳入指纹），提供 `describe_confirmable_operations(spec)`；判定 ask 时保存含中断状态的会话状态记录，并同步记账 `execution.paused(awaiting_confirmation)`（附被拦截的工具名和参数）；`resume_persistent_request` 由新实例放回记录、以 `ConfirmReply` 作为恢复输入，不向窗口追加任何内容；`ConfirmReply` 的权限确认项在恢复入口统一转换：同意为 `approved=True`，同意并本会话记住为 `approved=True, auto_confirm=True, persist_allow=False`，拒绝为 `approved=False` 并附反馈，其他组合（含 `auto_confirm=True` 而 `persist_allow` 为空或为真）一律拒绝，不能进入永久允许分支；同意时执行被拦截的调用（写操作照常先记账），拒绝时把反馈作为工具结果，权限确认超时按拒绝处理；主动提问回复（AskUserRail）转换为 `AskUserPayload.answers`，允许部分回答和自由文本，漏答的问题填“（未回答）”，`outcome=timed_out` 时每个问题填“（超时未获得回答）”，原生 rail 不重复提问，不扩展原生类型，Agent 不猜测缺失信息；本会话记住的选择写入 `INTERRUPT_AUTO_CONFIRM_KEY` 并随记录保存，同一 Session 在新 Execution、新实例中仍有效，其他 Session 不继承，不写入全局权限配置或权限 YAML；取消（`abort_persistent_request(reason="cancel")`）时保存记录并记账 `final(cancelled)`，停止（`reason="stop"`）时保存记录并记账 `paused(stopped)`；补充消息一律作为新 Request，不使用 follow_up / steer；续做中的询问复用 AskUserRail | PA-04、PA-09 | 待开始 | 待分配 | — |
| PA-22 | 按 v2 收敛已实现的契约 | `ExecutionContext` 按 v2 简化 `epoch`、`authority_handle`，保留 `recovery_ref`，明确崩溃续做与非确认暂停后继续均引用原任务待接续的 Execution，不新增 `continuation`；`PersistentPorts` 移除 `ExecutionAuthority` 与 `ControlPort`，`CommitPort` 改为 `record(event)`，普通事件接收入队后返回，普通日志允许缺失、不阻塞关键事件；关键事件等待自身落盘，final 还须确认所引用候选输出已可靠保存，并增加按产生顺序读取的 `read_execution_log(execution_id)`；`ContextStore`、`HistoryPort` 不再作为对外端口、改为注入必需的窗口存储后端（`BaseKVStore`，构造时校验契约，不读写存储；专用 SysOperation 文件系统后端不在本期范围），`GroupMaterialPort` 替换为 `GroupChatPort`（`recent_messages`、`search_messages` 及 v2 §7.9 的消息结构），保留预算与知识端口；`AgentEvent` 按 v2 §9.2 收敛为 11 类，`tool.intent`、`tool.completed`、`execution.paused` 补齐 v2 要求的字段；暂停原因固定为 `awaiting_confirmation / stopped / budget_denied / context_unfit / error` 并校验负载，暂停收尾与事件映射留给 PA-04；`window.switched` 只定义旧窗归档引用和新窗序号；`schema/context.py` 删除带版本和条件更新的窗口结构，新增群消息结构，`MaterialQuery / MaterialSnapshot / MaterialReadResult` 只用于知识端口；完整会话状态记录和归档结构分别随 PA-09、PA-10 定义，不在本项预设；不再使用的端口结果同步删减，群聊和知识端口仍区分“确定没有”与“没查完或失败”；构造的必需端口检查和现有测试随之更新，个人版行为不变。只调整契约，不实现事件队列或 PA-17 的恢复行为 | PA-01 | 已完成 | Song Haoran | 契约按 v2 收敛：执行上下文去掉 epoch / authority_handle，recovery_ref 不能指向本次执行；端口收敛为必需的 commit（record / read_execution_log）、budget、window_backend（BaseKVStore）和可选的 group_chat、knowledge；事件收敛为 11 类并固定五种暂停原因；新增群消息结构，删除旧窗口、提交、执行权和控制结构。代码：`openjiuwen/harness/persistent_agent/persistent_agent/ports.py`、`openjiuwen/harness/persistent_agent/persistent_agent/schema/{execution,protocol,events,port_results,context}.py`、`openjiuwen/harness/persistent_agent/__init__.py`；测试：`tests/unit_tests/harness/persistent_agent/{test_protocol,test_construction,test_binding}.py`，PA 包 173 passed（协议 99、构造 40、绑定 34）；F_01～F_03 共享回归 213 passed；改动范围 Ruff check / format 通过。设计与验证见 [F_04](../features/F_04_v2-contract-convergence.md)。事件映射、记账调用、暂停收尾及窗口存储读写不属于本项，分别归 PA-04、PA-09、PA-10 |

PA-02 只验证构造入口；PA-03 验证实例内部的归属校验与状态隔离；PA-22 先把已实现的契约收敛到 v2；PA-04 验证执行与恢复入口到事件出口的完整链路。PA-04 不重写 DeepAgent 的任务循环或另建一套流式输出；事件映射及按事件级别等待入队或落盘由 Persistent Agent 适配。AgentServer 负责调用执行与恢复入口、保证同一 Session 同时只有一个执行者、为并行 Session 分配不同实例并管理实例生命周期；PA-03 不实现这些调度工作。群内模型调用额度由 AgentServer 管理，本模块仅遵守宿主准入结果并回传实际用量，不维护预算账户。

### B. 本期：能记住工作内容并读取资料

| ID | 特性：要做什么 | 怎样证明做成 | 开工硬前置 | 状态 | 负责人 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| PA-09 | 加载与保存会话状态记录 | 实现内部组件 `WindowStore` 的 `load_state / save_state`，后端使用服务端注入的 `BaseKVStore`，不放在办公空间或 Agent 工具可访问的 SysOperation 中；不使用 checkpointer（执行路径的隔离由 PA-04 实现），加载后的状态不被 checkpointer 中的旧状态覆盖；在本项定义会话状态记录的结构、白名单字段和序列化方式，保存当前窗口、中断状态、本会话确认选择及可选的 DeepAgent 运行状态，所有字段一次写入；Session 首次执行创建空窗口，后续 Execution 加载同一 Session 的当前窗口，新 Session 不继承其他 Session 的记录；结束时整理窗口（删除临时背景块、执行头压缩为一行、追加执行结束标记；等待确认时末尾的工具调用不动、结束标记记在记录元信息），覆盖保存后再记账；加载失败不开始执行，保存失败按崩溃续做处理 | PA-22 | 待开始 | 待分配 | — |
| PA-10 | 容量达到 70% 时归档并换窗 | 在本项定义完整归档记录与归档索引的结构和序列化方式；在两次模型调用之间、工具调用与结果成对完整后触发；先把旧窗同步归档到 `WindowStore`（带 session_id、窗口序号、execution_id、覆盖的 request_id，并写入归档索引），归档失败不换窗；再申请额度生成旧窗摘要，组装新窗（摘要、task_plan、未完成写操作、归档引用、重新注入的执行头和任务输入、最近几轮原文）替换 ContextEngine 中的上下文，记录 `window.switched`；可重新获取的内容只存来源引用，无法重新获取的保存原文；新窗最小必要内容仍放不下时产出 `paused(context_unfit)` 并说明原因，不静默截断；新窗随本次 Execution 结束保存 | PA-09 | 待开始 | 待分配 | — |
| PA-11 | 检索本 Session 的历史窗口归档 | 提供 `search_session_history` 工具，经 `WindowStore` 的 `search` 只检索本 Session 的归档，本期按关键词匹配（读取索引后逐份扫描）；`list_archives` 可按 execution_id 查出归档供续做使用；结果区分找到、确定没有、没查完或失败，不以摘要替代原文证据；不与群聊检索合并 | PA-10 | 待开始 | 待分配 | — |
| PA-23 | 读取与检索群聊记录 | 经 `GroupChatPort` 在 Execution 开始和参与判断时读取最近群消息，以上次执行结束标记中的最后消息 ID 为游标增量获取，并排除触发本次执行的消息；提供 `search_group_messages` 工具供模型按需搜索；消息带稳定发送者身份、服务端接收时间、@、回复、附件引用和状态，已撤回的不返回正文，排队中的请求只作背景；结果区分找到、确定没有、没查完或失败；私聊 Session 不需要该端口 | PA-22 | 待开始 | 待分配 | — |
| PA-12 | 读取 PersonalContext 的授权知识版本 | 不挂载 PersonalContextRail；经 `KnowledgePort` 在 Execution 开始时解析一次授权范围和知识版本，知识导航放入临时背景块，执行中不刷新，正文由模型按需读取；无权限与不存在可区分；发布或授权变化在下一个 Execution 生效，并向 PA-21 提供来源依赖及失效信息 | PA-22 | 待开始 | 待分配 | — |
| PA-13 | 按调用授权读取办公空间文件 | 复用 Workspace 与文件工具，能列出和读取当前授权范围内文件并保留来源及观察到的内容版本；越界路径、其他 Session 私有目录被拒绝；权限未知不读取 | PA-01 | 待开始 | 待分配 | — |
| PA-14 | 写操作执行前同步记账意图 | 以工具声明的副作用属性判定写操作，未声明的一律按写操作处理；写操作执行前产出 `tool.intent`（operation_id、工具名、目标、参数哈希及敏感参数脱敏后的可读摘要）并同步记账，按 v2 §9.1 等待本条意图落盘，不因前序普通日志缺失而失败，成功才执行，失败停止当前执行；所有工具调用结束后产出 `tool.completed`（工具名、可读参数摘要、成功 / 失败 / 未知、必填的非空白结果摘要（所有状态均需提供，约 500 字以内）、可选的结果引用）；确认恢复后的写操作同样先记账；Persistent 不另加查询或重试，结果不明交 PA-17 的续做规则处理。与原生 ToolCallResilienceRail 衔接：该 rail 只重试声明 `idempotent=True` 的工具，未声明或非幂等的写操作在超时、断连后不被再次调用；重试会再次触发工具调用前的回调，同一工具调用沿用原 operation_id，不重复登记意图 | PA-22 | 待开始 | 待分配 | — |
| PA-15 | 适配办公空间文件修改 | 复用文件工具按授权创建和修改文件，写入前按 PA-14 记账意图；会话草稿放在 `sessions/<session_id>/` 下，其他 Session 不能访问；两个任务同时改同一文件时冲突可识别；文件落盘不等于任务完成，也不等于群内可见，任务失败不自动回滚文件 | PA-13、PA-14 | 待开始 | 待分配 | — |
| PA-21 | 跟踪上下文来源版本并处理失效内容 | 对消息、历史归档、文件、知识及派生摘要记录来源依赖；资料更新和撤权在下一个 Execution 生效：临时背景块每次按最新版本重新注入，归档中可重新获取的内容只存来源引用、读取时重新检查权限；执行中不改写系统提示词和工具定义，执行中立即生效列为后续扩展（v2 附录 D）；权限或版本无法确认时不读取 | PA-22 | 待开始 | 待分配 | — |

文件只读与写入分开：PA-13 可以先交付；PA-15 在 PA-14 的意图记账就绪后再启用。写操作结果不明时，执行侧不查询、不自动重试，由 PA-17 的续做规则先查证，查不清再问发起人。

PA-11 与 PA-23 分开交付：历史窗口归档是 Agent 自己的工作过程，属于 Session 私有；群聊记录属于群。两个检索工具不合并，避免把私有工作过程当作群公开内容引用。

PA-12 负责“授权知识如何读进来”，采集与发布由 PersonalContext 负责；PA-21 负责“已进入上下文的各类内容何时失效”，也覆盖非知识资料和派生摘要。PA-21 可先使用模拟片段与版本端口开发，与 PA-05、PA-11、PA-12、PA-23 在 M2a 验证整条失效链，与 PA-13～PA-15 在 M2b 验证文件变化和撤权；它不实现权限策略或另建知识库。续做时的资料装配随 PA-17 在 M2c 验证。

### C. 本期：执行日志与续做

v2 删除了 Checkpoint 和程序层面的精确恢复。PA-16、PA-17 保留编号，内容改为“执行日志 + 续做规则”，纳入本期。

| ID | 特性：要做什么 | 怎样证明做成 | 开工硬前置 | 状态 | 负责人 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| PA-16 | 读取并整理执行日志 | 事件流即执行日志，由服务端按 execution_id 和产生顺序保存，本模块不另写本地日志文件；崩溃续做和非确认暂停后继续都由程序调用 `read_execution_log(recovery_ref)`，只能读取同一 Session、同一 Request 的日志；读取已保存日志，按 local_seq 排序，允许缺口且非空不保证完整；缺少 tool.completed 的写操作标为有意图、无结果，不推断未执行；按 v2 §9.3 整理成简短执行记录：丢弃 `reply.delta`、`model.completed`，按 operation_id 或工具调用 ID 合并意图与结果，保留中间结论，列出按 execution_id 查到的归档，过长时写操作一条不省略；读不到时提示“上一次记录缺失”，由 Agent 向发起人确认 | PA-22 | 待开始 | 待分配 | — |
| PA-17 | 崩溃续做与非确认暂停后继续 | 进程崩溃、符合 v2 §13 异常续做条件的入口中断及非确认暂停后继续，统一按 v2 §11.2，由服务端沿用原 Request、新 execution_id 和指向该任务待接续执行的 `recovery_ref` 调用 `execute_persistent_request`，不新增 continuation 字段；加载 Session 最新会话状态记录，若带旧中断状态，先为每个缺少结果的工具调用补齐匹配 tool_call_id 的查证说明，已有真实结果不重复补齐，清除 `__react_agent_interruption__`、保留已保存的 `__interrupt_auto_confirm__`；再读取日志和归档，追加含恢复引用的续做执行头、进度摘要、原任务输入及继续指示。补结果只修复上下文，不记成实际工具执行的 tool.completed；正常确认恢复保留中断现场。续做规则写入提示词：已成功的步骤不重做，已登记意图但无结果的写操作（包括 tool.completed 日志缺失）先查证、查不清用 AskUserRail 问发起人，已产出最终结果的直接整理交出；停止后继续须明确请求；预算、容量或可恢复错误导致的暂停，在明确继续或服务端确认条件恢复后沿用同一流程，不新增恢复入口；所有续做即使窗口保存成功也读日志，中间执行过其他任务时仍加载最新窗口并查证当前状态；引用无法定位或日志缺失明确处理，不当作新任务直接重做 | PA-05、PA-09、PA-16 | 待开始 | 待分配 | — |

PA-16 负责把上一次 Execution 的事件整理成 Agent 可读的执行记录；PA-17 负责统一续做、旧中断现场处理、注入内容和续做规则。两项分开，便于分别验证“日志整理”和“续做判断”。PA-22 只调整恢复引用与日志读取契约，旧中断状态处理不作为其完成前置。正常确认恢复由 PA-08 借助会话状态记录完成，不属于本组；子代理沿用 DeepAgent 现有实现，其写操作同样遵守 PA-14 的意图记账。AgentServer 负责停止记录、继续指示与原 Request 的关联及恢复引用选择，服务端事件队列也在对端实现。

### D. 独立扩展：主动学习

| ID | 特性：要做什么 | 怎样证明做成 | 开工硬前置 | 状态 | 负责人 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| PA-18 | 从工作记录中识别学习目标 | 只使用专门授权且已确认的记录；同一问题不反复创建目标 | PA-01 | 待开始 | zhaominjun1997 | — |
| PA-19 | 跟踪学习内容的处理结果 | 能关联目标、来源和 PersonalContext 的处理批次；处理失败不能显示为知识已更新 | PA-18 | 待开始 | zhaominjun1997 | — |
| PA-20 | 根据后续工作反馈调整学习目标 | 能记录“新知识是否有帮助”及依据；反馈只更新学习目标，不改写原任务记录 | PA-18 | 待开始 | zhaominjun1997 | — |

PA-18～PA-20 在独立学习作业中运行，不挤占业务会话，不作为本期核心交付前置。PA-12 的基础知识使用与 PA-21 的知识失效处理不依赖学习扩展完成。

### E. 后续：执行模式扩展

PA-04 一期只开放单轮执行。以下执行模式各自验证生命周期、预算、记账、确认恢复和 checkpointer 隔离后逐项开放，不计入本期交付门槛。

| ID | 特性：要做什么 | 怎样证明做成 | 开工硬前置 | 状态 | 负责人 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| PA-24 | 开放 task-loop 执行模式 | 通过正式入口验证 task-loop 下的实例隔离、取消与停止收敛、确认恢复、异常清理、预算与记账，确认后台任务退出、其他 Session 不受影响，checkpointer 隔离成立；验证通过后 Persistent Spec 才允许 `enable_task_loop=True` | PA-04 | 待开始 | 待分配 | — |
| PA-25 | 开放同步子代理 | 子代理在父 Request / Execution 内执行，预算、事件、意图记账（PA-14）和确认恢复经父执行汇聚；停止与取消收敛子代理，清理后无残留后台任务；验证通过后才允许配置同步子代理 | PA-04、PA-14 | 待开始 | 待分配 | — |
| PA-26 | 开放异步及持久子代理 | 异步或持久子代理的后台任务归属、取消收敛、跨 Execution 状态及续做规则有明确设计并通过验证；验证通过后才允许 `enable_async_subagent` / `enable_subagent_runtime` | PA-25 | 待开始 | 待分配 | — |

## 3. 开发计划

以下按**交付节点和并行工作线**组织开发，不排具体日期或工期。本期安排 PA-01～PA-17、PA-21～PA-23；PA-18～PA-20 按独立扩展安排，PA-24～PA-26 作为后续执行模式扩展安排。节点表示可以交付、演示和验收的结果；工作线表示可以交给不同开发者同时推进的工作。本期特性满足第 2 节开工硬前置即可启动，不必等同批其他工作结束；扩展满足依赖不意味着自动纳入本期。

### 3.1 先交付接口基线

| 节点 | 开发内容 | 交付与放行标准 |
| --- | --- | --- |
| M0：接口基线 | PA-01（已完成，按旧版架构）；PA-22 按 v2 收敛：普通事件入队、关键事件等待落盘与执行日志读取、群聊记录、预算、知识、窗口存储后端注入及 11 类事件，并准备模拟调用方、模拟端口和内存版存储后端 | 无效输入、普通事件失败只记诊断 / 关键事件失败停止、普通日志允许缺失、关键事件自身落盘、final 所引用候选输出可靠保存、群聊 / 知识端口区分“确定没有”与“没查完或失败”等语义明确；本期各线能独立实现和测试。依赖新契约的特性在 PA-22 完成后启动，其余特性满足第 2 节开工硬前置即可启动 |

### 3.2 本期并行开发工作线

| 工作线 | M0 后立即做 | 本线后续安排 | 向哪个节点交付 |
| --- | --- | --- | --- |
| A：实例与执行 | PA-04 执行与恢复入口（PA-02、PA-03 已完成，PA-22 在 M0 内完成） | PA-04 与 B、C 线的 M1 特性汇合 | M1：一次指定 Session 的任务能运行并返回结果 |
| B：上下文与交互 | PA-05 上下文、PA-06 群消息判断、PA-07 响应交接可分别开发 | PA-04、PA-09 就绪后做 PA-08 确认与控制；PA-05、PA-07、PA-08 随 A 线汇入 M1；PA-06 随群消息场景汇入 M2a | M1、M2a |
| C：窗口、检索与上下文失效 | PA-09 会话状态记录、PA-23 群聊检索、PA-12 知识读取、PA-21 来源版本与失效可并行开发 | PA-09 随 A 线汇入 M1；PA-09 完成后做 PA-10 换窗，PA-10 完成后做 PA-11 历史归档检索；PA-21 与 B 线的 PA-05 及本线资料读取汇合，再与 D 线验证文件来源失效 | M1；M2a：群消息与长上下文；M2b 的文件失效联调 |
| D：办公空间 | PA-13 文件读取与 PA-14 写操作意图记账并行开发 | 两项都完成后做 PA-15 文件修改 | M2b：办公空间读写 |
| R：执行日志与续做 | PA-16 执行日志整理 | PA-05、PA-09、PA-16 就绪后做 PA-17 崩溃续做与停止后继续 | M2c：崩溃后续做原任务 |

每条线先用 M0 的模拟端口完成本线特性验证。A 线优先保证 M1 可运行，B～D 线及 R 线同时开发；PA-10、PA-11、PA-15、PA-17 分别等待本线必要能力，不等待其他整条工作线。若人手不足，先完成 M0（含 PA-22），再优先安排 A、B 线完成 M1（含 PA-09），同时保证 PA-14、PA-16、PA-21 的实现与对接，避免意图记账、执行日志和失效处理遗漏。本期任一工作线均不以 PA-18～PA-20 为前置。

| 扩展工作线 | 启动条件与安排 | 交付节点 |
| --- | --- | --- |
| E：学习（独立扩展） | 扩展单独安排；PA-01 后开发 PA-18，再并行开发 PA-19、PA-20，使用专门授权与学习端口 | M2d |
| X：执行模式扩展（后续） | PA-04 完成后依次安排 PA-24 task-loop、PA-25 同步子代理、PA-26 异步及持久子代理，逐项验证后开放 | 不设本期节点，各项单独验收 |

### 3.3 按可交付结果逐个汇合

| 节点 | 交付分类 | 汇合时机与参与特性 | 必须演示并留下的验收证据 |
| --- | --- | --- | --- |
| M1：可执行任务 | 本期 | M0 完成后，A 线 PA-02～PA-04，B 线 PA-05、PA-07、PA-08 与 C 线 PA-09 汇合 | 私聊或定时任务可以执行、取消、停止并返回结果；会话状态记录在 Execution 之间加载和保存，结束顺序为“整理 → 保存 → 记账”，各执行路径不经 checkpointer 读写状态；用带旧状态的 checkpointer 替身验证不恢复或保存，旧状态不覆盖 WindowStore 加载的记录，流关闭、取消收敛及资源清理照常，个人版默认行为不变；需要确认时保存记录并暂停，发起人确认后由新实例恢复，本会话记住只在本 Session 生效，超时按拒绝处理；预算准入被拒时产出 `paused(budget_denied)`，可恢复错误产出 `paused(error)`，已确定任务无法完成则产出 `final(failed)`；与 AgentServer 验证等待确认阻塞 Session、其他暂停不阻塞后续 Request；演示普通事件入队失败及异步保存失败后，后续关键事件不因 local_seq 缺口失败；关键事件自身保存失败停止执行；final 所引用候选输出未保存时不得完成，按原标识重交后不重复投递；无关普通日志缺失不阻塞 final；实例拒绝另一 Session 的输入。一期只开放单轮执行，task-loop 与子代理在 PA-24～PA-26 验证后逐项开放。与 AgentServer 联调：前端断开后任务继续运行，收到 final / paused 后仍消费至流结束，让入口完成资源清理，主动停止或取消通过独立控制任务调用 abort_persistent_request；E1 暂停后发布新版配置，确认恢复仍使用原 Request 的 Spec，新 Request 使用新版。未接入 PA-10 时上下文放不下明确暂停，不静默截断 |
| M2a：群消息与长上下文 | 本期 | M1 后汇入 PA-06、PA-10～PA-12、PA-21、PA-23；可在 M1 前独立开发 | 群参与判断、忽略语义及候选回复符合约束，被忽略的消息不进入窗口；容量达到 70% 时先归档再换窗，归档带 execution_id；群聊检索与历史归档检索分开，结果区分找到、确定没有、没查完或失败；知识导航在 Execution 开始时注入，消息、知识及派生摘要的更新 / 撤权在下一个 Execution 生效。群文件交接联合 PA-07 验证 |
| M2b：办公空间读写 | 本期 | M1 后汇入 PA-13～PA-15，并与 PA-21 验证文件来源失效；不等待 M2a 整体完成 | 授权读写成功，越界访问和其他 Session 的草稿目录被拒绝；写操作执行前意图记账成功才执行，记账失败即停止；并发修改同一文件可识别；文件更新或撤权影响后续 Execution 的上下文及操作。写能力按第 3.4 节条件放行 |
| M2c：崩溃后续做原任务 | 本期 | PA-16、PA-17 与 M1 执行链、PA-10 归档、PA-14 意图记账就绪后汇合；文件写操作续做再联合 M2b | 崩溃续做与非确认暂停后继续统一加载 Session 最新记录，注入 recovery_ref 对应的执行日志、归档引用和原任务输入；已成功步骤不重做，已登记意图但无结果的写操作先查证、查不清问发起人，已产出最终结果的直接交出；整个日志读不到或为空时提示并向发起人确认；普通日志部分缺失时先结合窗口、归档查证必要事实，不能推断操作未执行；被停止的任务不自动续做，也不阻塞后续任务；预算、容量或可恢复错误暂停后，明确继续或服务端确认条件恢复时复用统一流程，条件未变化时不反复自动续做。必须演示：tool.intent 已保存但 tool.completed 缺失，非空日志仍按不完整记录处理，续做先查证、不直接重做；关键记账失败使入口抛异常，服务端不等重启，确认旧执行退出和清理完成后续做原 Request，恢复引用指向本次中断执行，更早 Execution 的 paused 不作为本次结局；final / paused 已落盘后清理报错，保留已有结果并按对应状态处理，主动停止或取消后收尾报错不自动续做；E1 等待确认并保存中断状态，E2 同意后登记写意图、可能已执行但无结果记录时崩溃，E3 加载旧记录后补齐工具结果、清除旧中断状态，先查证而不直接重放写工具；已保存的会话授权保留，正常确认恢复不受影响。与 AgentServer 联调：审计 A 停止、审计 B 停止、审计 C 完成后继续 A，沿用 A 的 Request 和恢复引用，加载最新窗口并读取 A 的日志；发布新版配置后，崩溃续做与停止后继续仍使用原 Request 的 Spec |
| M2d：主动学习 | 独立学习扩展 | E 线 PA-18～PA-20 完成后独立汇合；不作为本期节点前置 | 仅从授权记录创建目标；处理批次和反馈可追溯；处理失败不显示为知识更新 |

M2a、M2b、M2c 可并行汇合，本期完成要求 M0、M1、M2a、M2b、M2c 均通过；M2d 单独跟踪，不阻塞本期。AgentServer、PersonalContext 和办公空间提供本模块所需接口与验证环境，它们自身的开发不在本计划内。对端尚未就绪时先记录模拟端口验证，真实对端联调列为节点待验收项；模块验证通过不等于产品联合交付完成。当前 M0 已完成：PA-01 按旧版架构定义契约，PA-22 按 v2 收敛；M1、M2a、M2b、M2c、M2d 尚未完成。

### 3.4 验收范围与能力开放

v2 不再预先展开验证矩阵，各特性实现时按自身“怎样证明做成”补充测试（v2 附录 B）。[旧版架构 V01～V41 验证矩阵](persistent-agent-architecture.md#19-验证矩阵与落地顺序)只作参考，不作为本期验收项。下表把 v2 附录 C 中“实现阶段需要验证”的事项落到具体特性，须在对应特性完成前给出结论并登记证据：

| v2 附录 C 验证点 | 主责特性 | 交付节点 |
| --- | --- | --- |
| 1. `ToolInterruptionState` 序列化后能正确还原，包括其中的工具调用消息和子代理中断 | PA-08、PA-09 | M1 |
| 2. 卡片回复能转换成 DeepAgent 识别的恢复输入（`InteractiveInput`，按工具调用 ID 对应），task loop 能识别这是一次恢复 | PA-08 | M1 |
| 3. 中断时上下文末尾的结构（工具调用消息是否在上下文里、有没有占位的工具结果） | PA-08、PA-09 | M1 |
| 4. 调用方传入 Session 时，正常结束的执行是否会把上下文写回 Session state | PA-09 | M1 |
| 5. 停止（`abort_bound`）路径能否拿到当时的上下文并保存，以及是否清掉了不完整工具调用 | PA-08、PA-17 | M1、M2c |
| 6. 换窗阈值与新窗保留的原文轮数 | PA-10 | M2a |
| 7. 确认恢复后崩溃，续做加载旧中断记录时能否补齐工具结果、清除旧中断状态并保留会话授权，正常确认恢复不受影响 | PA-17 | M2c |

v2 附录 C 的待确认事项在相关特性开工前与对端确认：本期不支持跨进程部署；归档保留期（影响 PA-10、PA-11）和确认超时时长（影响 PA-08）由 AgentServer 按 Agent 配置决定；系统提示词中 `tools` 片段随工具列表变化的问题，在 PA-05 中以固定工具列表规避。

v2 尚未覆盖、需先补充设计并确认的事项：

- **容量边界**（影响 PA-05、PA-10、PA-16、PA-17）：70% 阈值只在两次模型调用之间检查，挡不住单个或多个工具结果一次性超限、续做时注入的日志和背景超限，以及摘要调用自身的输入超限。需在 v2 中补充：容量计算包含哪些开销、各检查点放在哪里、超大结果如何以引用加必要内容进入窗口。确认前，上述特性的容量相关验收不能关闭。
- **写操作的声明来源**（影响 PA-14）：ToolCard 目前只有 `idempotent`，没有只读 / 有副作用的字段。PA-14 开工前确定声明从哪里来。

能力开放遵循以下条件：

- M1 可先演示私聊和定时任务、会话状态记录、确认恢复、取消和停止；未接入 PA-06 时不处理群消息，未接入 PA-10 时上下文放不下明确暂停。
- 持续使用可变或可撤权资料时，须完成 PA-21 与相应读取、装配路径联调；撤权在下一个 Execution 生效，不能只验证 KnowledgePort 已撤权而仍向模型传旧内容。
- 本期写工具开放须通过 PA-14、PA-15 适用场景：写操作执行前意图记账成功才执行，需要确认的操作经 PA-08 由发起人确认；未声明副作用属性的工具一律按写操作处理；结果不明时不另加重试，原生只重试声明幂等的工具。
- M2c 通过前，AgentServer 不自动续做未完成的 Request，由服务端明确处置；被停止的任务任何时候都不自动续做。一期不开放 task-loop 与子代理（PA-24～PA-26 验证后逐项开放）；子代理开放后，其写工具未接入 PA-14 意图记账时禁用。
- 会话状态记录保存失败按崩溃续做处理；普通日志入队或保存失败只记诊断，缺失不阻塞后续关键事件；关键事件自身或 final 必需候选输出保存失败则停止当前执行；不能把未保存或未记账的结果当作已完成。

## 4. 进度怎么更新

实现或验证有变化时，修改第 2 节对应特性的“状态”“负责人”“证据”三列和下方交付节点的状态，并在变更记录中追加一条。分别统计本期 20 项、独立学习扩展 3 项、执行模式扩展 3 项及总计 26 项；范围变更不能记为完成。硬前置尚未完成的特性保持“待开始”；已经开工但自身验收或必要联调未过的特性标“进行中”或“待验收”，并写明待验证项及其交付范围。扩展未排期仍为“待开始”，不因此标为“阻塞”。完成项附上实现和测试或联调链接；v2 附录 C 的验证点按第 3.4 节登记结论。调用方及 PersonalContext 自身的开发进度在各自目录维护。后续用户交由我修改的特性，负责人统一填写“Song Haoran”；此前已登记为 yan_zj 的条目保持不变。

| 交付节点 | 交付分类 | 状态 | 负责人 | 验收证据或待办 |
| --- | --- | --- | --- | --- |
| M0：接口基线 | 本期 | 已完成 | yan_zj | PA-01 按旧版架构定义调用身份与宿主端口协议；PA-22 按 v2 收敛执行上下文、端口、事件与群消息结构，PA 包 173 passed、共享回归 213 passed，见 [F_04](../features/F_04_v2-contract-convergence.md)。实际宿主适配和存储后端不属于接口基线，列在 PA-04、PA-09～12、PA-23 |
| M1：可执行任务 | 本期 | 待开始 | 待分配 | 执行与恢复入口、会话状态记录、确认与控制、结果交接 |
| M2a：群消息与长上下文 | 本期 | 待开始 | 待分配 | 群消息判断、换窗与归档、群聊与历史检索、知识与 PA-21 失效联调 |
| M2b：办公空间读写 | 本期 | 待开始 | 待分配 | 文件读写、写操作意图记账、并发修改及 PA-21 文件失效联调 |
| M2c：崩溃后续做原任务 | 本期 | 待开始 | 待分配 | 执行日志整理、崩溃续做与停止后继续 |
| M2d：主动学习 | 独立学习扩展 | 待开始 | zhaominjun1997 | 单独安排；目标、处理批次与反馈关联 |

| 日期 | 条目 | 变更 | 证据或阻塞原因 |
| --- | --- | --- | --- |
| 2026-09-24 | PA-01～PA-26 | 建立仅覆盖本模块的计划；全部待开始 | `persistent_agent/` 目前只有 `docs/`，尚无本模块实现和测试 |
| 2026-09-24 | PA-01～PA-26 | 将特性描述改为具体行为和验收场景，补充术语说明；进度不变 | 文档修订 |
| 2026-09-24 | PA-20～PA-26 | 将协作、管理和学习分组及阶段验收分开；进度不变 | 文档修订 |
| 2026-09-24 | 原 PA-20～PA-26 | 移除子代理开发项，后续特性重新编号为 PA-20～PA-25；进度仍为 0/25 | 子代理实现直接复用 DeepAgent |
| 2026-09-24 | 原 PA-21～PA-25 | 移除管理查询和定时计划两项，学习特性改为 PA-21～PA-23；进度仍为 0/23 | 两项由 AgentServer 层承载 |
| 2026-09-24 | 原 PA-20～PA-23 | 移除单独的群内公开协作项，学习特性改为 PA-20～PA-22；进度仍为 0/22 | 群回复复用普通 Agent 响应链路 |
| 2026-09-24 | PA-02～PA-03、原 G0 | 明确业务 Agent 身份可跨 Session，运行实例同一时刻只绑定一个 Session；进度不变 | 文档约束统一 |
| 2026-09-24 | PA-02～PA-03、原 G0 | 将实例创建时机与分配归回 AgentServer；PA-03 仅负责实例内部会话绑定及状态隔离，进度不变 | 职责边界修订 |
| 2026-09-24 | PA-03、原 G0 | 验收聚焦单实例归属校验和内部状态隔离，移除服务端实例分配场景；进度不变 | 特性边界细化 |
| 2026-09-24 | 原 PA-04～PA-22 | 合并任务执行与过程回传为 PA-04，后续编号调整为 PA-05～PA-21；进度仍为 0/21 | 优先复用 DeepAgent 的任务循环、模型与工具调用及流式输出 |
| 2026-09-24 | 原 PA-07～PA-21 | 移除群内模型调用额度管理项，后续编号调整为 PA-07～PA-20；进度仍为 0/20 | 额度账户、准入与结算归 AgentServer；本模块仅遵守准入并回传用量 |
| 2026-09-24 | PA-07 | 明确文件仅在上传群空间或发送到群内后才对群成员可见；进度不变 | 文件共享与办公空间保存分开验收 |
| 2026-09-24 | PA-10、原 G2 | 明确新窗口生效后不切回旧窗口，后续故障在新窗口核对与恢复；进度不变 | 换窗语义修订 |
| 2026-09-24 | PA-11 | 将“缺页”明确为历史检索分页读取不完整；进度不变 | 验收描述澄清 |
| 2026-09-24 | PA-01～PA-20 | 将开发顺序改为硬前置解锁、并行工作线与组合验收；进度不变 | 依赖与并行计划明确化 |
| 2026-09-24 | PA-01～PA-20 | 将第 3 节改为接口基线、并行工作线和交付节点计划，增加节点进度跟踪；进度不变 | 开发安排与验收汇合点明确化 |
| 2026-09-28 | PA-01～PA-20 | 补充 JiuwenSwarm 代码归属分析与跨仓迁移汇合顺序；未修改能力契约，进度仍为 0/20 | 迁移分析 `jiuwenswarm-migration-analysis.md`（未入库，仓库历史中也不存在，仅作历史记录）；仅完成静态梳理，未迁移代码 |
| 2026-09-28 | PA-02～PA-04 | 按用户要求明确沿用 DeepAgent 类，以 Spec 和工厂装配 Persistent 能力，不新增 Agent 门面；进度不变 | 迁移分析第 6.1～6.2 节（文件未入库，仅作历史记录）；已有架构 / S_01 接口表述待同步 |
| 2026-09-28 | PA-01～PA-04 | 新增 F_01，细化 Spec / 构造上下文 / owner 接缝 / 薄执行适配并同步架构、概览及 S_01；进度仍为 0/20 | 文档设计，未实现或运行新增测试 |
| 2026-09-28 | PA-01～PA-02 | 按用户确认将嵌套配置组合改为 PersistentAgentSpec 继承 DeepAgentSpec；专属字段平铺，运行类仍为 DeepAgent；进度不变 | F_01、S_01、架构、概览及迁移分析同步；仅文档变更 |
| 2026-09-28 | PA-01～PA-02 | 开始配置与构造子范围，Spec / context / factory / owner 接缝已实现；完整特性仍为进行中 | 27 项新单测 + 77 项现有定向回归通过；独立审查问题经 RED → GREEN 修复；全 Harness 缺 OpenTelemetry exporter，见 F_01 |
| 2026-09-28 | PA-01 协议子范围 | 稳定 AgentEvent、类型化负载、Commit / Authority / Control / Budget 结果和端口类型已实现；PA-04 仍未开始 | 63 项协议 + 104 项构造 / 现有回归通过；独立审查空白片段问题经 RED → GREEN 修复；全仓 82 项收集错误，见 F_02 |
| 2026-09-28 | PA-03 内部绑定子范围 | 普通调用归属、占用、停止和 owner 清理已实现；原生流后台生命周期未安全接入，内部流接缝失败关闭，完整特性仍进行中 | 25 项绑定测试 + 167 项回归通过；独立审查 P1 经 RED → GREEN 禁止不安全流启动；未修改个人版，见 F_03 |
| 2026-09-28 | PA-03 单轮流补齐 | 经用户确认新增仅 Persistent 启用的原生生命周期接缝，恢复内部 stream_bound；默认个人版路径保留，完整正式入口验收仍待 PA-04 | 34 项 binding 与原回归合计 593 passed；独立审查两项 P1 经 RED → GREEN 修复，全仓仍 82 项收集错误，见 F_03 / 原 Harness F_06 |
| 2026-09-29 | PA-01～PA-21 | 保留已有编号，新增 PA-21 来源版本与上下文失效管理；扩充窗口、归档、检索、判断/执行上下文及可靠交接验收，明确 DeepAgent 复用和宿主职责 | 仅修订计划；无实现或测试完成证据 |
| 2026-09-29 | 本期范围、M0～M2d | 本期为 PA-01～PA-15、PA-21 共 16 项；PA-16～PA-17 中断恢复暂缓，PA-18～PA-20 学习单独扩展；同步工作线、验收分类和能力开放条件 | 总计 0/21：本期 0/16、恢复 0/2、学习 0/3；完整架构目标保留 |
| 2026-09-29 | PA-18～PA-20、M2d | 主动学习特性及交付节点负责人统一登记为 zhaominjun1997 | 负责人登记；状态仍为待开始，进度不变 |
| 2026-09-29 | PA-01～PA-03 | 负责人登记名更新为 yan_zj；当日远端调整后的状态为进行中 | 本行为历史进度记录，当前闭环状态见下行 |
| 2026-09-29 | PA-01～PA-03 | 同步 main/jiuwenmate 的新版 21 项计划并按当前代码校准实现状态；PA-02 明确复用 DeepAgentSpec 的 parts / Config 装配而非误写为调用 create_deep_agent | PA-01～03 子范围已实现；正式执行闭环和全项验收仍待后续阶段 |
| 2026-09-29 | PA-01～PA-03、M0 | 按各项可独立验证的边界关闭接口契约、DeepAgent 构造和实例内 Session 隔离；未将宿主后端或执行集成提前计入完成 | PA 包 132 passed；共享 Harness 定向回归 582 passed；ReAct / 默认个人版定向回归 136 passed；Ruff 与格式检查通过。PA-04、窗口 / 资料后端及正式入口集成仍待开发 |
| 2026-10-06 | PA-01～PA-23、M0～M2d | 按 [v2 设计](persistent-agent-design-v2.md)附录 E 编制 v2 计划：PA-05、PA-08～PA-12、PA-14、PA-21 按 v2 改写；原 PA-11 拆出群聊检索为新增 PA-23；新增 PA-22 承接已实现契约的调整；PA-16、PA-17 改为执行日志与续做并纳入本期；PA-04、PA-06、PA-07、PA-15 同步验收描述；第 3.4 节改为 v2 附录 C 验证点映射；M0 因 PA-22 改为进行中 | 仅修订计划，PA-01～03 状态与证据不变；总计 3/23：本期 3/20、学习 0/3 |
| 2026-10-06 | PA-04、PA-08、PA-09、PA-14、M0～M2c | 按评审补充：PA-04 增加 checkpointer 隔离及 task-loop / 子代理正式入口验收；PA-08 明确确认回复转换，“本会话记住”不进入永久允许分支；PA-14 与原生工具重试衔接；同一 Request 跨 Execution 固定 Spec 写入第 1 节并列入 M1、M2c 联调；修正 M0 与 A 线顺序；容量边界和写操作声明来源列为待确认；迁移分析标为未入库的历史资料 | 仅修订计划；进度不变 |
| 2026-10-06 | PA-04、PA-07、PA-14、PA-22 | 同步 v2 §9.1 的事件有序保存：普通事件入队后返回，关键事件等待前序事件和自身全部落盘，失败不越过；队列由 AgentServer 维护，PA-22 只定义端口契约 | 仅修订计划；进度不变 |
| 2026-10-06 | PA-05、PA-16、PA-17、PA-22、M2c | 崩溃续做与停止后继续统一使用 recovery_ref，不新增 continuation；停止不阻塞后续任务，明确继续关联原 Request；旧中断状态处理归 PA-17，M2c 增加确认恢复后崩溃及停止后执行其他任务再继续的验收，正常确认恢复保留原流程 | 仅修订计划；不新增 PA-22 的运行时前置，进度不变 |
| 2026-10-07 | PA-09、PA-10、PA-22 | 本期窗口存储后端只支持 `BaseKVStore`：PA-22 定义必需注入位并校验契约，PA-09、PA-10 基于同一后端实现保存、加载和归档；专用 SysOperation 文件系统后端不在本期范围 | 同步 D3 决策；持久化与隔离由服务端保证，进度不变 |
| 2026-10-07 | PA-09、PA-10、PA-22 | PA-22 新增群消息结构和事件必需字段，知识端口保留 Material 三类结构；完整会话状态记录及序列化归 PA-09，完整归档记录和索引及序列化归 PA-10，`window.switched` 只定义归档引用和新窗序号 | 存储格式随使用方定义；不在 PA-22 预设完整存储结构，进度不变 |
| 2026-10-07 | PA-04、PA-05、PA-08、PA-10、PA-16、PA-17、PA-22、M1、M2c | 固定五种暂停原因及负载校验，PA-22 定契约、PA-04 做暂停收尾与事件映射；非确认暂停不阻塞后续任务，继续复用 PA-17，主动停止须明确请求，其余原因可由服务端确认条件恢复后发起；区分 paused(error) 与 final(failed) | 补齐预算、容量和可恢复错误暂停的语义与验收；不新增恢复流程，进度不变 |
| 2026-10-07 | PA-04、M1 | 明确 AgentServer 持续消费事件流的调用方契约，final / paused 后仍读到结束；前端断开只结束展示订阅，停止或取消通过独立控制任务调用 abort_bound；M1 增加断线及收尾联调验收 | 避免前端断开或提前结束消费取消原生执行；不增加 PA-22 的前置，进度不变 |
| 2026-10-07 | PA-04、M1 | 同步 checkpointer 隔离说明：服务端不额外调用 pre_run / post_run，原生生命周期中的状态恢复和保存由 PA-04 隔离；M1 增加旧 checkpointer 状态不覆盖 WindowStore 记录及清理照常的验收 | 跨 Execution 状态只由 WindowStore 管理；保留原生生命周期清理和个人版默认行为，进度不变 |
| 2026-10-07 | PA-04、PA-17、M2c | 入口异常（含记账失败）由服务端接住并核对本次 Execution 的结束记录，无 final / paused 且未被主动停止或取消时按崩溃中断续做；已有结果保持，续做前确认旧执行退出和清理完成，M2c 增加对应验收 | 避免 Request 等待服务重启才处理；沿用原 Request 和统一流程，进度不变 |
| 2026-10-07 | M0、第 2、5 节 | 将记账的概括表述统一为普通事件入队、关键事件等待落盘，保留写操作等关键事件的同步记账要求 | 对齐已确认的事件级别与有序保存契约；只修正表述，进度不变 |
| 2026-10-07 | PA-22、M0 | 按 v2 收敛已实现的契约：执行上下文、宿主端口、11 类事件与五种暂停原因、群消息结构；删除旧窗口、提交、执行权和控制结构；同步 S_01～S_03 并新增 F_04 | PA 包 161 passed；共享回归 213 passed；Ruff 通过。总计 4/23：本期 4/20、学习 0/3；M0 完成 |
| 2026-10-07 | PA-22、PA-04、PA-14、M0 | 按审阅确认恢复 D1：普通事件失败只记诊断，关键事件失败停止，前序未保存时屏障不能成功；tool.completed 在成功 / 失败 / 未知状态下均要求非空白结果摘要，引用不能替代 | 代码、测试和契约同步修订；PA 包 173 passed（协议 99、构造 40、绑定 34），共享回归 213 passed，合计 386 passed；Ruff check / format 通过；完成状态不变 |
| 2026-10-07 | PA-04、PA-07、PA-14、PA-16、PA-17、PA-22、M0～M2c | 取消全部前序落盘屏障：普通日志允许缺失、不阻塞关键事件；关键事件保证自身落盘，final 额外确认 output_refs 引用的候选输出已可靠保存；日志允许 local_seq 缺口，续做先查证缺失结果 | 同步已确认策略、端口说明及待实现的运行验收；事件结构和函数签名不变；PA 包 173 passed，Ruff check / format 通过；PA-22 / M0 状态不变；运行行为仍待对应 PA 与 AgentServer 实现 |
| 2026-10-07 | PA-04、PA-08、PA-24～PA-26、M1 | 按 PA-04 设计决策（D1～D7）修订：执行 / 恢复 / 控制三个入口及 `RequestInput`、`ConfirmReply`；主动提问回复的未回答与超时标记；checkpointer 按 Session 注入 `NoopCheckpointer`；一期只开放单轮执行，新增 E 组 PA-24 task-loop、PA-25 同步子代理、PA-26 异步及持久子代理；错误与结局映射、最终回答交接；正式入口只开放已接入能力。PA-04 开工，负责人 Song Haoran | 仅修订计划；总计 4/26：本期 4/20、学习 0/3、执行模式扩展 0/3；PA-04 进行中 |
| 2026-10-07 | PA-04、M1 | PA-04 组件验收完成：三个入口、事件映射与记账、预算准入、结局判定、最终回答交接、确认恢复校验、停止与取消、提前关闭流和 checkpointer 隔离；超长最终回答以 `paused(error)` 结束、不截断，拆分或转文件交接待 PA-07 决定；M1 联调的停止与取消改为调用 `abort_persistent_request` | PA 包 224 passed，共享回归 213 passed，`tests/unit_tests/harness` 5426 passed / 39 skipped；总计 5/26：本期 5/20、学习 0/3、执行模式扩展 0/3 |

## 5. 从 JiuwenSwarm 迁移时的补充安排

本节只补充跨仓接入顺序，不改变第 2～3 节的模块内硬前置和并行计划。

1. **M0 先确认责任和差异。** 明确单次调用固定 Session、事件按序入队与关键事件等待落盘、窗口存储后端注入、群聊记录权限，以及切窗策略。切窗时机已由 v2 确定为“两次模型调用之间、容量达到 70%”，与此前产品讨论的“整轮结束后切窗”不同，不能直接搬旧控制器。
2. **宿主适配与纯算法提取可并行准备。** 公共群归档、附件 / 云文档、Skill 安装选择、飞书身份和渠道投递保留 JiuwenSwarm；core 经端口消费。字面原文匹配、消息转换等可复用，但补充调用权限、稳定引用和完整性检查。
3. **优先形成 M1 的新入口闭环。** 使用 Harness 公共工厂，验证实例隔离、完整装配、统一事件、控制及文本 / 文件结果提案；不复制旧应用 Runtime 或直接 SDK 发送链路。个人版入口保持原逻辑。
4. **窗口与续做按独立确认点接入。** PA-09 先支持会话状态记录的加载和保存，PA-10 再处理换窗和归档；现有窗口加载器不是 PA-17 的崩溃续做。PA-14 的意图记账从 M0 后准备，与窗口开发并行。
5. **能力放行与开工条件分开。** PA-04 可用模拟端口开发，但 M1 须合并验证 PA-03、05、07、08、09；PA-17 可在 PA-16 后开发，但 M2c 放行必须满足第 3.3 节的日志整理、归档查询和意图记账要求。缺少意图记账时不能以兼容模式开放写工具。
6. **知识和学习保留原归属。** core 消费 PersonalContext 的授权版本，学习作业只管理目标、批次关联与反馈；采集 / 发布实现不迁入 Persistent Agent。M2d 仍可独立交付。

后续实现前按 `docs/AGENTS.md` 创建对应 feature 设计，完成代码和验证后再更新 PA 状态。实现目录沿用现有的 `persistent_agent/persistent_agent/` 结构（见 v2 §4 与 [F_01](../features/F_01_spec-and-deep-agent-construction.md) 第 10 节），新组件按职责放入 `execution/`、`schema/` 或新增子目录；不创建空执行接口或将文档梳理计为实现完成。

## 6. 第一阶段具体产物与评审门槛

本节是按旧版架构完成第一阶段（PA-01～03）时的交付记录，保留原文。其中列为“尚未实现”的有界交接、持久化去重 / 围栏、超时查询等在 v2 下不再需要；`execute_persistent_request` 的签名以 v2 §5.1 为准；按 v2 对已实现代码的调整见 PA-22。

细化设计见 [F_01](../features/F_01_spec-and-deep-agent-construction.md)，按以下顺序展开，不新增 PA 编号：

| 顺序 | 范围 | 产物与检查点 |
| --- | --- | --- |
| A | PA-01 | PersistentAgentSpec 配置继承、专属字段、PersistentBuildContext、端口协议与测试替身；扁平序列化、专属字段校验与指纹、调用身份验证 |
| B | PA-02 / PA-03 接缝 | tool_owner_id 在 Spec provider / parts / Config 注册前贯穿；原 Card 不变，缺省路径回归 |
| C | PA-02 | create_persistent_agent 返回 DeepAgent；无模型 / 投递副作用；部分构造失败仅清理本调用资源 |
| D | PA-04 接入准备 | execute_persistent_request 薄适配及并发消费 / 确认测试骨架；内部复用 DeepAgent API，不当作 PA-04 已完成 |

F_01 已经用户确认并启动配置与构造实现。A～C 的构造子范围已落地；D 暂不提供空执行函数，以免误用为可靠执行入口。构造测试覆盖返回类型、错配 Session / 配置、工具注册隔离、浅复制污染和失败清理，并有默认 Harness 定向回归。原配置恢复、未知提交、隐藏写能力及模型前动态授权须在后续执行接入时验证；窗口、群判断和恢复不能随工厂空挂载后标为实现。

### 6.1 第一阶段交付记录

- 实现目录：`persistent_agent/persistent_agent/` 下的 factory.py、ports.py、schema/{spec,build_context,execution,context}.py；外层 `__init__.py` 懒导出构造和协议符号。
- 通用接缝：BuildContext.tool_owner_id（缺省 None）→ DeepAgentSpec.resolve_parts → parts / Config；不改个人版默认行为。
- 测试：`tests/unit_tests/harness/persistent_agent/test_construction.py`；实际结果见 F_01 第 10 节。
- 进度：PA-01 契约层、PA-02 构造和 PA-03 实例内 Session 隔离已分别完成。下一步为 PA-04 正式执行适配、事件生产与授权 / 预算 / 确认闭环，不重新实现模型循环。
- 未提交代码；仍在用户指定 checkout 的当前分支。

### 6.2 第二步：执行协议值对象

- 实现目录：内部 schema/{protocol,events,port_results}.py；ports.py 使用类型化事件和结果，外层包继续懒导出，不新增运行类。
- AgentEvent 稳定编号、完整提案摘要、13 类负载；Commit / Authority / Control / Budget 结果明确区分未知、拒绝和确认，并验证归属与引用。
- 验证及独立审查结果见 [F_02](../features/F_02_execution-protocol.md)；新增协议测试在 test_protocol.py。
- PA-01 / M0 的接口契约已完成，不等于宿主端口已有生产实现。尚未实现：事件生产与 local_seq 分配、有界交接、持久化去重 / 围栏、超时查询、原生 DeepAgent 执行映射。PA-04 / M1 仍待开始，不把 DTO 当成执行闭环。
- 本步不改 JiuwenSwarm、个人版逻辑或通用 Harness 代码，不提交代码；架构文件未修改。

### 6.3 第三步：内部执行绑定与隔离

- 内部 execution/binding.py 提供 native invoke / stream 的绑定占用，以及作用于目标绑定的停止与空闲关闭；工厂捕获配置及资源归属快照。Persistent 构造注入独立流任务管理器，通过 DeepAgent 可选分支取消并等待单轮流生产者及 Session 收尾退出。默认个人版不注入、不启用。
- 测试使用真实 DeepAgent、Session、Runner 工具注册与本地模型，覆盖并行消息 / 工具隔离、错配拒绝、停止、清理、异常释放和流提前关闭。
- 测试、审查及范围裁定见 [F_03](../features/F_03_invocation-binding-and-isolation.md)。不对外新增 Agent 类或可靠执行函数，不启用执行 capability。
- PA-03 已在内部绑定 / 原生单轮调用边界完成并有测试。仍未覆盖的是 PA-04 正式执行入口接入后的 task-loop、惰性子代理端到端联调；不把单轮接缝验证推广为任意可信扩展的沙箱保证。共享原生层只增加默认不启用的接缝，个人版默认行为保留，架构文件未改，未提交代码。
