# F_05 执行、恢复与控制入口

| 项 | 内容 |
| --- | --- |
| 类型 / 状态 | feature；PA-05，已实现（组件验收；正式入口在 PA-09、PA-10 接入前明确拒绝） |
| 日期 | 2026-10-08 |
| 负责人 | Song Haoran |
| 范围 | 内部 `persistent_agent/{schema/request.py, execution/{control,emitter,model_rail,seams,entries}.py, schema/spec.py}`、外层 `__init__.py` 导出及对应单测；依赖共享 Session 接缝 harness [F_07](../../../docs/features/F_07_session-checkpointer-injection.md) |
| 测试基线 | 改动前：PA 包 173 passed，F_01～F_03 共享回归集 213 passed。改动后：PA 包 224 passed（结局锁定及附录 C 验证点 5 补充后 229 passed），共享回归集 213 passed，`tests/unit_tests/core/session` 411 passed，`tests/unit_tests/harness` 5426 passed / 39 skipped（Python 3.11.17） |
| 关联 | [架构设计](../designs/persistent-agent-architecture.md) §5.1、§7.8、§9.1～9.2、§10.2、§10.4、§13、§14；[开发计划](../designs/persistent-agent-development-plan.md) PA-05；规约 S_01、S_02 |

## 背景

PA-01～PA-03 和 PA-04 提供了构造、实例隔离和当前契约，但还没有对外的执行入口。PA-05 把它们编排成三个入口：执行、确认恢复、停止 / 取消，并把原生流式输出映射为设计规定的事件。需要依赖其他特性的部分（参与判断 PA-07、窗口组装 PA-06、会话状态记录 PA-10、交互适配 PA-09、续做 PA-18）只保留接入点；在对应特性接入前，正式入口明确拒绝，不降级。

设计决策 D1～D7 已与用户确认，见架构设计 §5.1、§9.2、§10.4 和计划 PA-05。本文记录实现方案。

## 决策

### 1. 入口与值对象

```text
execute_persistent_request(spec, context, inputs: RequestInput) -> AsyncIterator[AgentEvent]
resume_persistent_request(spec, context, confirm_reply: ConfirmReply) -> AsyncIterator[AgentEvent]
abort_persistent_request(context, *, reason: "cancel" | "stop") -> None
```

`schema/request.py` 定义：

- **`RequestInput`**：`kind`、`text`、`source`、`reply_target`、`attachment_refs`、`continue_instruction`。
  - `group_message` 的 `source` 必须是 `GroupMessage`。
  - `direct_message` 的 `source` 必须是 `DirectMessageSource`（`message_id`、`sender_id`、`sender_name`、`received_at`）。
  - `scheduled` 没有 `source`。
  - `continue_instruction` 是否允许出现，取决于执行上下文里有没有 `recovery_ref`，由入口检查。
- **`ConfirmReply`**：`responses` 至少一项，`tool_call_id` 不重复。每项是二选一：
  - `PermissionResponse`：`choice: approve | approve_for_session | reject`，可附 `feedback`。
  - `AnswerResponse`：`outcome: answered | timed_out`，`answers` 为“问题文本 → 回答”；`timed_out` 不带回答。

### 2. 执行编排

每个执行入口返回一个异步生成器。第一次迭代时，生成器创建一个内部执行任务（runner），自己只负责把 runner 交出的事件逐个 yield 给服务端。

这样设计是因为 `abort_bound` 会取消占用实例的那个任务。如果直接在服务端的消费任务里运行原生流，取消信号可能落在服务端自己的代码里，入口就来不及收尾。改由 runner 占用实例后，取消只作用于 runner：runner 接住取消，完成收尾并交出结束事件，服务端的消费任务不受影响。

runner 的步骤：

1. **前置检查**（任何事件之前，失败抛校验拒绝 `DEEPAGENT_CONFIG_PARAM_ERROR` / `DEEPAGENT_INPUT_PARAM_ERROR`）：
   - 执行模式符合一期要求：`enable_task_loop=False`，不启用任何子代理。
   - Session 注入了 `NoopCheckpointer`。
   - 输入与执行上下文匹配：群消息在 PA-07 接入前拒绝；PA-07 接入后，缺少 `GroupChatPort` 的群消息仍在事件之前拒绝，已注入端口的群消息先判断再决定是否进入执行体。带 `recovery_ref` 的执行在 PA-18 接入前拒绝；`continue_instruction` 只能随 `recovery_ref` 出现。
   - 依赖的接入点可用：会话状态记录，以及恢复时的交互适配。例外：已注入 `GroupChatPort` 的群消息 Request 先完成 PA-07 参与判断，再取会话状态记录（PA-10）和窗口组装（含群消息读取 PA-23）接入点，它们未接入时的拒绝晚于判断事件。
2. 构造实例，并核对装配结果：`deep_config` 中 task-loop 和子代理确实都没有开启。
3. `execute` 记录 `execution.started`。
4. 加载会话状态记录（PA-10 接入点），写回 Session state；加载失败抛运行异常。`resume` 加载后核对等待现场、校验回复（见第 5 节）。
5. 检查控制原因：如果已经收到 cancel / stop，直接进入收尾。
6. 通过 `stream_bound` 运行原生流，并映射输出（见第 3 节）。
7. 判定结局（见第 4 节）；如果有最终回答，交出 `output.proposed`。
8. 整理并保存会话状态记录（PA-10 接入点）；保存失败抛运行异常，不记结束事件。
9. 记账 `execution.paused` 或 `execution.final`；失败抛运行异常。
10. `close_bound` 清理资源。如果结束事件已落盘后清理失败，仍然抛运行异常，让服务端知道旧执行没有完成清理；服务端看到已有结束记录，会保留结果、不重新执行。如果此前已有异常在抛出，清理失败只记诊断，不覆盖原异常。

当服务端关闭生成器或取消消费任务，且此时没有收到控制原因：生成器取消 runner，并在 shield 保护下等待原生流和资源清理结束，然后继续抛出。这种情况不产生 `paused` / `final`，服务端按第 13 节核对。入口用 `aclosing` 包住内部转交生成器：外层 `async for` 不会自动关闭内层生成器，否则提前关闭时 runner 会继续运行。

构造上下文仍只能使用一次：同一上下文再次进入执行入口时，在任何事件之前拒绝。

例外：PA-06 窗口组装中的兜底检查（加载到等待中的调用，或窗口有未配对的工具调用）发生在加载会话状态记录之后，所以其校验拒绝晚于 `execution.started`。服务端按校验拒绝处理，不续做。

### 3. 事件映射与记账

`execution/emitter.py` 的 `EventEmitter` 按 Execution 分配 `local_seq`。每个事件在锁内依次完成编号、构造 `AgentEvent`、`await record()`：

- 关键事件（`tool.intent`、`execution.paused`、`execution.final`）记账失败时，在控制对象上登记致命错误并抛出。
- 普通事件记账失败只记诊断日志。

事件按记账顺序进入展示队列，由生成器 yield 给服务端。

**事件容量**：所有事件共用两条限额，常量定义在 schema 中：文本字段最多 `TEXT_MAX_CHARS`（65536）个字符，完整事件按规范 JSON 计算最多 `EVENT_MAX_BYTES`（262144）个 UTF-8 字节，包括身份、序号、时间、目标等全部字段。只看字符数不够：65536 个四字节字符本身就超过字节上限，控制字符经 JSON 转义后每个占 6 字节。emitter 用同一套检查服务两种场景：

- **`emit_text`**（`reply.delta`）：每段按两条限额取最长前缀，按 Python 字符边界切分，各段拼接后与原文完全一致。
- **`emit_if_fits`**（最终回答的 `output.proposed`）：两条限额都满足才编号、记账、展示，否则返回空，由入口改判暂停。

试探只构造候选事件，不调用 `record`、不占用正式 `local_seq`。候选事件使用与正式事件相同的序号和时间，正文换成一个字符的占位；由于正文在规范 JSON 中只出现一次，完整事件的字节数等于占位事件的字节数减 1，再加正文转义后的字节数，结果精确。非法代理字符等字段错误不算容量超限，构造正式事件时按原规则抛出。

| 来源 | 事件 |
| --- | --- |
| runner 开始 | `execution.started`（目标为任务正文） |
| 原生 `llm_output` 块 | `reply.delta`，超过任一限额时拆成多段 |
| 模型调用结束且同时带工具调用的正文 | `progress.updated`（中间结论，截断到约 500 字） |
| 模型调用结束 | `model.completed`（`model_call_id`、配置版本、实际或未知用量） |
| 原生 `__interaction__` 块 | 收集被拦截调用，用于 `paused(awaiting_confirmation)` |
| 结局 | `output.proposed`、`execution.paused / final` |

模型调用相关的事件由 `execution/model_rail.py` 的 `PersistentModelCallRail` 产生。这个 rail 在 `PersistentAgentSpec.resolve_parts` 中加入 parts，优先级为 10：数值越大越先执行，10 保证它是模型调用前最后决定的那个钩子。它挂在三个模型调用钩子和一个工具调用钩子上：

- **调用前**：先检查控制对象，已有致命错误或 cancel / stop 时，请求 force-finish 并跳过本次调用。然后生成 `model_call_id` 并调用 `admit`：
  - 被拒绝：force-finish，标记为 `budget_denied`。
  - `admit` 抛异常：force-finish，标记为 `error`。
- **调用后**：用响应中的用量调用 `report`（异常只记诊断），再记 `model.completed`；正文和工具调用同时存在时，再记 `progress.updated`。
- **调用失败**：以未知用量调用 `report`，并记 `model.completed`。
- **工具调用前**：与模型调用前共用同一个检查。已有致命错误或 cancel / stop 时，请求 force-finish，原生跳过该工具的执行体，并带着这个标记结束循环。检查覆盖每个尚未开始的工具，只读工具也包括在内；已经开始的并行调用按现有取消机制收敛。这个标记只用于结束原生循环：有致命错误时，入口仍抛出最初登记的异常，不会降级为 `paused(error)`。

`tool.intent` / `tool.completed` 归 PA-15，`window.switched` 归 PA-11，`participation.decided` 归 PA-07。

### 4. 结局判定

按优先级从高到低：

1. 控制对象登记了致命错误 → 抛运行异常，不能降级为 `paused(error)`。
2. 结局锁定前接受了 cancel → `final(cancelled)`；接受了 stop → `paused(stopped)`。原生流被取消时，runner 先接住取消信号再收尾。
3. 原生结果 `result_type="interrupt"` → `paused(awaiting_confirmation)`，`intercepted` 来自中断块：`tool_call_id` 用交互 ID，参数按键名脱敏后呈现。
4. rail 的 force-finish 标记 → `paused(budget_denied)` 或 `paused(error)`。
5. 原生结果 `result_type="answer"` → `final(completed)`；回答正文非空时先交出 `output.proposed`，并列入 `output_refs`。检查的是 after_invoke rail 之后的最终答案：外层 DeepAgent 在 after_invoke 之后才交出 `answer` 片段。字符数或完整事件字节数超限时改判 `paused(error)`，再执行“保存 → 暂停记账”。答案不截断，完整放进 `ExecutionEnding.pending_answer` 交给会话状态记录保存；PA-10 必须保存该字段，后续交接据此继续，不重跑已完成的业务。原生窗口上下文只保存模型原始回复，不反映 after_invoke 的改写，所以需要这个字段。只有容量超限会改判暂停：字段错误、保存失败和关键记账失败仍按原异常规则处理。
6. 原生结果 `result_type="error"`（含最大迭代次数耗尽）、原生异常，或没有最终结果 → `paused(error)`。

`output_id` 由 `execution_id` 派生，同一 Execution 内保持稳定。`reply.delta` 只用于展示，超过任一限额的原生片段拆成多段事件，不截断。参数脱敏规则是：键名含 password、secret、token、key、credential、authorization 的值替换为 `***`。PA-15 的参数摘要复用同一函数。

### 5. 确认恢复

- `resume` 从会话状态记录取得等待现场：中断状态、所属 `request_id` 和 `reply_target`。核对 `request_id` 与执行上下文一致，再按架构设计 §5.1 校验回复。
- 校验失败抛校验拒绝，不记录任何事件、不保存、不改动现场。
- 回复内容到原生 `ConfirmPayload` / `AskUserPayload` 的转换（含“未回答”和“超时”标记）由 PA-09 的交互适配接入点完成。
- PA-05 只负责按交互 ID 组装 `InteractiveInput`，并编排恢复流程。
- 恢复不向窗口追加任何内容，也不记 `execution.started`。

### 6. 停止与取消

- `abort_persistent_request` 把原因记在构造上下文的控制对象上。这个对象在上下文创建时就存在，所以实例构造前到达的控制也能记下。
- 同一种原因重复到达时忽略；不同原因只保留第一次。
- **结局锁定**：执行入口在原生调用退出、确定执行结果后，同步锁定本次 Execution 的结局。锁定前接受的控制参与结局判定；锁定后不再接受活动执行的停止或取消，不修改控制原因，也不调用 `abort_bound`。结局锁定不代表持久化成功，状态保存和结束记账仍按原失败规则处理。实现上，`_run_native` 在每个确定结局的出口调用 `ExecutionControl.lock_outcome()`，它与最后一次检查控制原因之间没有让出点；锁定后 `request_abort` 返回 False，控制入口直接返回。此前，原生返回后、收尾保存期间到达的控制会被接受并调用 `abort_bound`，结局却仍按原生结果，状态自相矛盾。
- **异常退出同样锁定**：runner 因异常退出时（致命错误、加载失败、回复校验拒绝、服务端提前关闭等），`_run` 在进入任何异步清理之前同步调用 `lock_outcome()`。这只是关闭控制接收：保留已有的控制原因和原异常，不产生结束事件，也不表示持久化成功。清理失败仍按原规则处理，已有异常时只记诊断。此前，`close_bound` 虽会同步标记关闭中、不再调用 `abort_bound`，但迟到的控制仍会改写控制原因。
- 实例已构造且尚未关闭时，复用 `abort_bound`：runner 正在原生调用中则取消该调用；尚未进入原生调用则使之后的原生调用直接拒绝，入口按控制原因收尾。中断已在进行或实例已关闭时，不重复中断。
- `abort_bound` 在等待原生 `abort` 返回期间把绑定标为停止中，此时 `close_bound` 会拒绝。控制入口在调用 `abort_bound` 前登记一个完成信号，runner 清理前先等待它，避免清理与正在进行的原生停止重叠。
- 控制入口不发任何事件；结束事件统一由执行入口发出。

### 7. 接入点

`execution/seams.py` 集中放置后续特性的接入点：

- `session_state_store(context)`：会话状态记录的加载、整理和保存，PA-10。
  - `load` 把最新记录写回新 Session，返回 `LoadedRecord`：等待现场 `waiting`（仅等待确认时有）和群消息游标 `last_group_message_id`（服务端确认已扫描到的群消息位置）。没有记录时返回空的 `LoadedRecord`；返回其他类型按运行异常处理。
  - `save` 收到的 `ExecutionEnding` 含结局、所属 Request、回复目标、`output_refs`、容量超限时未交接的完整最终答案 `pending_answer`，以及本次的群消息游标 `last_group_message_id`。PA-10 须原样保存这两个字段，后者写进执行结束标记。
- `window_assembler(context)`：新 Request 的窗口组装，PA-06。执行入口在加载记录之后调用 `assemble(context, request, record)`，拿到 `WindowAssembly`：原生输入 `native_input`，以及读取后的群消息游标（服务端确认的扫描位置，没有可信进度时为加载时的游标），写入结束信息。组装抛出的非框架异常按运行异常处理，原生调用不会在不完整的窗口上运行。确认恢复不调用组装，结束信息沿用加载时的游标。
- `group_message_reader(context)`：本 Session 所在群的最近消息读取，PA-23。`recent_messages(context, after=游标, trigger_message_id=触发消息)` 返回 `GroupMessagesResult`：游标之后的增量与“触发消息之后至本次读取上界”的回补合并去重，不含触发消息；`scanned_through` 为服务端对增量读取确认的扫描位置，作为新游标，为空时保留旧游标。PA-06 组装背景通过它读取。PA-07 不调用该接入点，判断直接使用 `GroupChatPort`，以免等待 PA-23。
- `native_reply_adapter()`：交互回复到原生输入的转换，PA-09。

**窗口标记约定（PA-06 写，PA-10 整理）**：PA-06 在追加的消息元信息中设置 `WINDOW_PART_KEY`（`persistent_window_part`）。值为 `execution_header` 的执行头在结束时保留并压缩成一行；值为 `temporary_background` 的临时背景块在结束时删除。没有该标记的消息按普通窗口内容保留。

各接入点未接入时都抛校验拒绝，正式入口因此明确拒绝。单测通过替身验证 PA-05 自身的编排，替身不能替代正式实现。共享值对象、标记和协议集中定义在 `schema/integration.py`，`seams.py` 保留接入点并兼容再导出原有名称，供 PA-06、PA-07、PA-08、PA-10、PA-23 并行开发而不改动执行入口的同一段编排。此拆分由 F_06 的共享契约整理完成，不改变原契约的字段和语义。

### 8. 能力声明

按 F_01 的约定，`enabled_features` 暂不增加 `execution`。M1 集成接入 PA-06、PA-08、PA-09、PA-10 并验证后，再声明执行能力。

## 拒绝的方案及原因

| 方案 | 不采用的原因 |
| --- | --- |
| 直接在服务端的消费任务里运行原生流 | `abort_bound` 会取消占用实例的任务，取消信号可能落在服务端代码里，入口无法收尾并交出结束事件 |
| 让服务端直接调用 `abort_bound` | 服务端拿不到入口内部构造的实例，也无法区分 cancel 和 stop |
| 在模型调用前的 rail 里抛异常来拒绝预算 | 原生循环会把异常转成错误结果，丢失原因分类；force-finish 能带回结构化标记 |
| PA-10 接入前用新的空窗口执行 | 违反“加载失败不能降级为空窗口”，会让任务在没有会话记忆的情况下运行 |
| 在 PA-05 中实现回复到原生载荷的转换 | 已确认由 PA-09 负责原生交互适配；PA-05 只做校验和编排 |
| 扩展原生 `AskUserPayload` 表示超时 | 已确认改用 `ConfirmReply` 的 `outcome` 加标记文本，不改共享类型 |

## 验证

2026-10-08 审计修复验证：`tests/unit_tests/harness/persistent_agent` 249 passed；`tests/unit_tests/harness` 与 `tests/unit_tests/core/session` 联合回归 5863 passed / 39 skipped。新增 `test_emitter.py` 验证字符数与完整事件字节数边界、文本分段后原文保持、容量试探不占事件序号和字段错误分类；`test_entries.py` 补充致命错误阻止未开始的工具调用、异常清理期间拒绝控制、超限答案经结束信息完整交给存储接入点、after_invoke 改写答案和保存失败仍抛异常的用例。

测试文件：`tests/unit_tests/harness/persistent_agent/test_request.py`（值对象）与 `test_entries.py`（入口）。入口测试使用真实 DeepAgent、本地流式模型、注入 `NoopCheckpointer` 的 Session，会话状态记录与交互适配用替身（替身在两个 Session 间搬运白名单状态和等待现场）。

| 验证项 | 结果 |
| --- | --- |
| 1. 值对象 | 三种 kind 与 `source` 的匹配、非法输入；`ConfirmReply` 两种回复、空回复、重复 ID、永久允许选项、超时带回答、空回答均按预期 |
| 2. 前置拒绝 | task-loop、子代理开关、未注入 `NoopCheckpointer`、群消息、带 `recovery_ref`、`continue_instruction` 无 `recovery_ref`、非法输入、会话状态记录或交互适配未接入：都抛对应校验拒绝，不记事件、不调用模型、不构造实例 |
| 3. 正常完成 | 事件依次为 started → reply.delta ×2 → model.completed → output.proposed → final(completed)；展示事件与记账事件一致；`output_refs` 指向候选；保存在 final 之前；实例已关闭 |
| 4. 预算 | 拒绝准入不调用模型并产出 `paused(budget_denied)`；`admit` 异常产出 `paused(error)`；`report` 异常只记诊断，未知用量照常记 `model.completed` |
| 5. 错误 | 模型异常、最大迭代次数耗尽都产出 `paused(error)`，不交出候选；超长回答产出 `paused(error)`，展示片段拆分后完整 |
| 6. 记账与保存失败 | 普通事件失败仍展示、仍完成；final 记账失败与保存失败都抛运行异常，保存失败时不记结束事件；原生代码内部登记的致命错误照样抛出，不保存、不记结束事件；加载失败在模型调用前抛运行异常 |
| 7. 确认 | 权限中断产出 `paused(awaiting_confirmation)`，带交互 ID、工具名和脱敏参数，并有 `progress.updated`；新 Session、新实例恢复后工具执行并完成，回复目标取自等待现场；未知 ID、回答发给权限确认、所属 Request 不一致、没有等待现场都抛校验拒绝，不记事件、不保存、不调用模型 |
| 8. 停止与取消 | 执行中 cancel → `final(cancelled)`，stop → `paused(stopped)`，模型流被取消；构造前到达的控制在模型调用前收尾；重复或并发控制只保留第一个原因；原生停止较慢时清理等它返回；结局锁定后（收尾保存期间）到达的 cancel / stop 不被接受：控制原因不变、不调用原生 `abort`，结局仍为 `final(completed)`；锁定后保存失败照常抛运行异常、不记结束事件 |
| 9. 提前关闭流 | 原生流被取消，实例占用释放并关闭，不保存、不记结束事件 |
| 10. checkpointer 隔离 | 全局内存 checkpointer 中同 session_id 的旧状态不会进入执行，执行后也没有新快照 |
| 架构设计附录 C 验证点 5（部分） | 模型调用中途停止时，已有上下文（含已完成的工具调用及结果）经会话状态记录接入点保存后记 `paused(stopped)`；第一次模型调用中途停止时，原生在上下文末尾追加“[Request cancelled by user]”占位回复。工具执行中途停止的不完整工具调用归 PA-18 |
| 11. 回归 | PA 包 224 passed（新增 51），结局锁定及附录 C 验证点 5 补充后 229 passed、`tests/unit_tests/harness` 5432 passed / 39 skipped；共享回归集 213 passed；`tests/unit_tests/core/session` 411 passed；`tests/unit_tests/harness` 5426 passed / 39 skipped。同一套件连续运行 5 次结果一致 |

变异检查：去掉清理前的停止等待、去掉内层生成器的 `aclosing`、去掉原生调用后的致命错误检查、去掉 `request_abort` 的结局锁定检查，对应用例都会失败。

其他检查：改动文件 Ruff check / format 通过。mypy 在改动文件中报 2 个改动前已有的问题（`build_context.py` 的 `derive` 返回类型、`spec.py` 中 `build` 的参数类型覆盖），与本特性无关，未修改。原 PA-01 构造测试中“执行入口尚未导出”的断言改为检查三个入口已导出。

## 已知遗留

- 窗口组装（PA-06）、文件候选与补交（PA-08）、交互适配（PA-09）、会话状态记录（PA-10）、意图记账（PA-15）、续做（PA-18）接入前，对应能力在正式入口明确拒绝或不可用。群消息参与判断已由 [F_07](F_07_group-participation.md) 接入；忽略和判断失败不经过上述接入点。
- task-loop 与子代理由 PA-24～PA-26 开放。
- 原生 rail 之外的内部模型调用（例如 task planning 的独立模型）不经过本 rail 的预算准入；一期配置不启用这类 rail，开放时再评估。
- 字符数或完整事件字节数超限的最终回答目前以 `paused(error)` 结束，完整答案经 `ExecutionEnding.pending_answer` 交给 PA-10 保存。是否改为拆成多条候选或转为文件交接，以及如何从保存的答案继续交接，待 PA-08 设计时一并决定。
- 工具调用前的守卫与 `PersistentModelCallRail` 同一优先级（10），在其他 rail 的工具调用前钩子之后执行。PA-15 的意图记账若在更早的钩子中执行，须自行检查致命错误，或排在守卫之后，避免为不会执行的工具登记意图。
