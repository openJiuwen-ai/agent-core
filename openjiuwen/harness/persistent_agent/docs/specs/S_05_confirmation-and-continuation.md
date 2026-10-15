# S_05 用户确认、执行控制与续做规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_05 / confirmation-and-continuation |
| 关联模块 | [request.py](../../schema/request.py)、[entries.py](../../execution/entries.py)、[control.py](../../execution/control.py)、[确认接入协议](../../execution/seams.py) |
| 最近一次修订日期 | 2026-10-15 |
| 关联 feature | [F_05](../features/F_05_execution-entries.md)、[F_15](../features/F_15_tool-intent-before-side-effect.md)、[F_19](../features/F_19_execution-log-record.md)；接入与验证见[开发计划](../designs/persistent-agent-development-plan.md) PA-09、PA-17、PA-18 |
| 设计依据 | [架构设计：确认](../designs/persistent-agent-architecture.md#521-确认卡片等待与原生恢复)、[控制](../designs/persistent-agent-architecture.md#523-停止取消与补充消息)、[统一续做](../designs/persistent-agent-architecture.md#524-崩溃续做与非确认暂停后继续的统一流程)、[恢复边界](../designs/persistent-agent-architecture.md#83-根据什么恢复什么时候可以继续) |

本规约维护 ConfirmReply、确认恢复、停止 / 取消、控制截止及原 Request 的继续方式。原 Checkpoint 文档改名后，状态保存转由 S_03 唯一负责。

## 1. 范围、术语与适用条件

确认回复 DTO、恢复与控制入口及结局锁定已有组件实现；NativeReplyAdapter 和会话记录接入仍不可用，完整确认恢复不开放。崩溃续做与非确认暂停后继续为已确认待实现的契约，带 recovery_ref 的执行当前明确拒绝。

确认使用既有 PermissionInterruptRail / AskUserRail 与原生中断恢复，不另写确认 Rail，不用 TaskLoop 的活动 follow-up 队列替代宿主业务队列。构造 / 绑定、执行编排、事件可靠性、记录格式及副作用查证条件分别引用 S_07、S_01、S_02、S_03、S_08。

宿主鉴别发起人、生成确认卡片、保存调度及停止事实。入口的 Request / 交互校验不能代替服务端“只有发起人能回复”的鉴权。

## 2. 确认输入与等待现场

| 对象 | 字段与约束 | 事实持有者 |
| --- | --- | --- |
| ConfirmReply | responses 1～64 项，按 kind 判别回复类型，同一 tool_call_id 不重复 | 宿主提供的本次回复值 |
| PermissionResponse | kind=permission；tool_call_id；choice 为 approve / approve_for_session / reject；feedback 可选、最多 4096 字符 | 发起人的权限选择 |
| AnswerResponse | kind=answer；outcome 为 answered / timed_out；answers 按原问题文本为键、最多 64 项 | 发起人的回答或明确超时 |
| WaitingScene | request_id、reply_target | 会话记录加载结果中的等待归属 |
| 原生中断状态 | 被拦截调用及问题、交互 ID；位于 Session state | 原生中断机制，跨执行由 S_03 保存 |

answered 至少有一个回答；timed_out 不得携带回答。直接构造非法 DTO 抛 Pydantic ValidationError；入口校验包装为 DEEPAGENT_INPUT_PARAM_ERROR。等待对象只是定位和回复目标，不保存另一份 Checkpoint 或第二套中断状态机。

## 3. 不变量

1. **I-1 确认归属：** 卡片只从记账成功的 paused(awaiting_confirmation) 生成，只接受原发起人回复；恢复校验同 Request、等待交互及回复类型，不能将无效回复视为授权。
2. **I-2 现场保留：** 正常确认恢复保留中断现场，不追加执行头、背景或任务消息；回复目标取等待现场，不由新回复任意覆盖。
3. **I-3 授权边界：** 同意并本会话记住只更新本 Session 的原生确认选择；不提供永久允许或 persist_allow_rule，不改变发布的 Spec。
4. **I-4 控制单源：** abort 包装入口只请求停止；等待原生退出、保存、结束事件及资源清理由执行入口完成，不产生第二个终态写入者。
5. **I-5 继续不重放：** 新恢复 Execution 仍属于原 Request，沿用原 Spec，加载 Session 最新记录并先查证已发生的写操作；缺少日志不是“未执行”的证据。

## 4. 接口契约

### 4.1 `resume_persistent_request(spec, context, confirm_reply) -> AsyncIterator[AgentEvent]`

调用方为已鉴权的宿主；须满足 S_01 的模式 / 隔离与 S_07 的新构造上下文要求。取得会话记录和 NativeReplyAdapter 后构造实例、加载现场，再校验 WaitingScene、原生中断、Request 和每项回复的 tool_call_id。answer 只能回复问题调用，其键不得超出原问题集合；permission 不能回复问题调用。

校验通过后，以 tool_call_id 组装原生 InteractiveInput，不重复记 execution.started，不组装新窗口，也不读取新群背景；结束保存沿用原群消息游标。原生恢复执行被拦截工具，写操作仍须经过 S_08 的意图屏障，拒绝结果作为工具反馈交给模型。

| 条件 | 对外结果 / 状态与副作用 |
| --- | --- |
| 非法 DTO、没有等待现场、Request / ID / 类型 / 问题错配 | DEEPAGENT_INPUT_PARAM_ERROR；不记业务事件、不保存回复，保持等待。已经尝试构造的上下文按 S_07 不能复用 |
| 原生回复适配或记录 getter 未接入 | DEEPAGENT_CONFIG_PARAM_ERROR，当前正式恢复门禁拒绝 |
| 加载、保存或关键记账失败 | 按 S_01 / S_02 抛运行异常；保存失败不确认结束 |
| 有效回复 | 交给原生流并按 S_01 结局处理；不是收到回复即证明工具成功 |

### 4.2 发起人选择与超时（宿主交接契约）

卡片依据暂停载荷中的工具名与脱敏参数生成，不直接展示 Rail 默认提示语。权限确认超时以 reject 并附“确认超时”反馈调用恢复；主动提问超时以 timed_out 调用，不猜测答案或视为授权。确认规则由 Spec 发布，窗口保存 INTERRUPT_AUTO_CONFIRM_KEY 的实际常量以原生定义为准，不通过点击修改全局规则。

### 4.3 `abort_persistent_request(context, *, reason) -> None`

reason 仅 cancel / stop；非法原因为 DEEPAGENT_INPUT_PARAM_ERROR，非法上下文为 DEEPAGENT_CONTEXT_PARAM_ERROR。独立控制任务首先在本次 ExecutionControl 保存首次接受原因；之后到达的原因忽略。实例已经构造且尚未关闭时复用内部 abort_bound；尚未构造时由后续执行边界检查原因。

入口返回只表示控制请求处理，不证明原生调用、在途工具、状态保存或记账已经收敛；它不发事件。cancel 对应 final(cancelled)，stop 对应 paused(stopped)，都由执行入口在收尾时产生。已发生外部效果不因取消自动撤销，补偿需另行授权，不在本接口自动执行。

### 4.4 续做调用（已确认待实现）

崩溃续做及非确认暂停后继续走 execute_persistent_request，沿原 Request 使用新的 execution_id、原输入与 Spec，并以 recovery_ref 指向该任务最近一次待接续的 Execution。继续指示通过 RequestInput.continue_instruction 提供；引用不能取同 Session 其他任务的 Execution。当前入口在基础校验时拒绝带 recovery_ref，不能按新任务重跑。

### 4.5 `NativeReplyAdapter.to_native(response, request) -> Any`（内部接入，待实现）

该同步协议把已经匹配的 PermissionResponse / AnswerResponse 与原生 InterruptRequest 转换为 `InteractiveInput.user_inputs[tool_call_id]` 对应的值；它不提供另一套鉴权或确认决策。当前 native_reply_adapter getter 明确拒绝。转换失败不能继续进入原生恢复，不返回假同意；异常按入口现有边界传播。

## 5. 控制、调度与恢复生命周期

### 5.1 结局锁定与控制截止

原生调用退出且执行结果确定后，同步锁定结局。锁定前接受的控制参与 S_01 优先级；锁定后不再改变原因或调用 abort_bound。异常退出进入清理前也关闭控制接收，保留已有控制和原异常，不产生结束事件。锁定不代表持久化完成，后续保存与关键记账仍可能抛异常。

控制与 native call 未退出时不能提前释放绑定。宿主持续消费事件流至结束 / 异常，确认旧执行退出、相关清理完成后才能安排同 Session 下一个 Execution；清理失败不能凭超时自动接管。

### 5.2 Request 调度规则（宿主须保证）

| 已确认状态 / 原因 | 当前 Session 后续请求 | 原任务继续条件 |
| --- | --- | --- |
| awaiting_confirmation | 阻塞其他 Request，保持窗口等待现场 | 有效发起人回复或按超时规则恢复 |
| stopped | 旧执行清理后不占住普通队列位置 | 明确继续 / 结束；重启不自动继续 |
| budget_denied / context_unfit / 可恢复 error | 旧执行清理后可处理后续 Request | 明确继续，或宿主确认相应条件恢复；条件未变不反复自动续做 |
| final(completed / ignored / cancelled / failed) | 旧执行清理后推进 | 不重开业务 Loop；只处理结果与投递 |

补充消息始终是新的 Request，普通排队，不能活动 steer / follow_up 到当前任务。有其他任务经过暂停期间执行时，继续仍加载 Session 最新窗口，不回退旧快照。

### 5.3 确认与统一续做的区别（已确认待实现）

正常确认恢复完整保留现场；崩溃及非确认暂停继续按以下顺序：

1. 宿主提供新 Execution、原 Request / Spec / 输入及正确恢复引用，确认旧执行已退出。
2. 加载 S_03 的 Session 最新会话记录到新的空 Session。
3. 若记录有旧中断状态，为窗口中缺少结果的调用补匹配 ID 的“上次中断，真实结果需查证”说明，已有真实结果不改；清除旧中断状态，保留本 Session 已记住的确认选择。
4. 读取 S_02 的原执行日志，并按 execution_id 查 S_03 归档；先处理事实不明，不启动旧中断工具的自动重放。
5. 追加续做执行头、临时记录背景、原任务输入和必要继续指示，交由 DeepAgent 先查证进度。

补结果仅修复上下文，不能伪造 tool.completed 或表示副作用成功 / 失败。原生恢复可能在模型循环前执行被拦截工具，因此必须在续做进入原生调用前处理过期中断；正常确认恢复不能执行上述清理步骤。

日志整理由 PA-17 提供（[F_19](../features/F_19_execution-log-record.md)）：意图与结果只在能唯一关联时合并，operation_id 复用（第二条意图，或结果之后出现的意图）与调用身份不一致时分开列出并注明；保留 progress.updated、工具的调用 ID、目标与结果引用、候选及其是否被 paused / final 引用；丢弃 reply.delta、model.completed、execution.started 与参与判断。当前不裁剪、不聚合、不省略，能否注入由续做按实际容量判断（待“容量边界”对齐）；归档暂只列日志中的换窗引用并注明未按 execution_id 查询（待 PA-12）。读不到、空日志或契约不符时提示“上一次记录缺失”；序号缺口与缺少结束记录照常整理并写明，不能据此推断未执行。已成功步骤不重做，不确定只读步骤可重读；有意图无结果的写操作先查证，查不清复用 AskUserRail 问发起人。

### 5.4 异常后的宿主核对

入口异常后先查本 Execution 已落盘的 final / paused。已有结束记录则保留结果，不因后续清理错误重做；没有结束记录且未主动停止 / 取消，符合条件时作为崩溃中断调度续做，不必等服务重启。主动控制事实由宿主保存，收尾失败不能误触发自动继续。

## 6. 当前限制与替代关系

本期不承诺跨进程租约围栏、完整事件尾部归约、精确程序恢复、未知结果自动补偿或外部系统去重。ConfirmReply 和入口组件存在不表示真实状态、交互适配及续做已接入；参见当前 seams 门禁和唯一开发计划。

原 S_05 的 CheckpointBody、checkpoint.proposed、applied_request_event_seq、epoch 及“所有暂停阻塞到终态”规则不再是现行契约。状态保存并入 S_03；原 S_01 的确认回复、abort 及续做调度责任并入本文。S_05 编号保留，旧文件名引用同步改为本文件；不复用已删除的 S_06。

## 7. 关联规约与验证索引

S_01 负责实际结局编排，本文负责控制截止及继续方式；S_02 定义终态 / 暂停和日志的可靠性；S_03 定义记录的唯一状态来源；S_08 定义副作用意图、未知结果与重试边界；S_07 定义实例收敛和不可复用绑定。

- ConfirmReply 与回复约束：[test_request.py](../../../../../tests/unit_tests/harness/persistent_agent/test_request.py)。
- 恢复校验、控制竞争及锁定：[test_entries.py](../../../../../tests/unit_tests/harness/persistent_agent/test_entries.py)。
- 流生产者、取消及关闭：[test_binding.py](../../../../../tests/unit_tests/harness/persistent_agent/test_binding.py)。
- 确认后写意图与取消后未决工具：[test_tool_intent_rail.py](../../../../../tests/unit_tests/harness/persistent_agent/test_tool_intent_rail.py)。
- 执行日志整理：[test_execution_log.py](../../../../../tests/unit_tests/harness/persistent_agent/test_execution_log.py)（PA-17 组件验证）。
- 会话状态记录已由 PA-10 接入（组件验证，等待现场含中断请求类型）；真实确认适配与跨执行续做验证尚待 PA-09、PA-18 接入；测试入口与模拟结果不证明生产恢复能力。
