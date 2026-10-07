# S_02 统一事件与结果交接规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | 事件 DTO 和记账 / 执行日志端口契约已按 v2 实现（PA-22）；事件映射、记账调用、结局判定和最终回答交接已实现（PA-04）；`tool.intent / tool.completed`（PA-14）、`window.switched`（PA-10）、`participation.decided`（PA-06）待接入 |
| 关联模块 | 内部 `persistent_agent/{ports.py,schema/{protocol,events,port_results}.py,execution/{emitter,model_rail,entries}.py}` |
| 最近一次修订日期 | 2026-10-08 |
| 关联 feature | [F_02 执行协议](../features/F_02_execution-protocol.md)、[F_04 按 v2 收敛契约](../features/F_04_v2-contract-convergence.md)、[F_05 执行、恢复与控制入口](../features/F_05_execution-entries.md)；开发计划 PA-04、PA-07、PA-14、PA-16、PA-22 |
| 设计依据 | [Persistent Agent 设计（v2）](../designs/persistent-agent-design-v2.md) §9、§13 第 5、12 条、§14 |

当前可以构造 `AgentEvent` 及其 11 种类型化负载，并已定义 `CommitPort.record / read_execution_log` 协议。PA-04 的执行入口已把原生流映射为事件并按下文时序记账（见“事件映射与结局”）；生产记账后端由服务端实现，下文的记账时序和交接约束也是服务端要满足的运行契约。

## 范围 / 边界

本规约规定执行侧产出的事件、交给服务端记账的方式、执行日志的读取，以及候选输出的交接。事件队列、持久化、执行日志存储、发送检查和渠道投递由 AgentServer 实现；本模块不在本地另写日志文件。

## 现有实现的复用边界

[`DeepAgent.stream`](../../../deep_agent.py) 已经产生模型、工具与交互输出。执行入口消费这些输出并映射为本规约事件，不重写模型 / 工具循环，也不复制一套原生流协议。展示片段可以沿用 DeepAgent 的流输出，但流结束不代表任务完成。

## 不变量

1. 同一 Execution 的事件按产生顺序调用 record；服务端按 local_seq 维护已保存日志，允许普通日志缺失，不以序号连续性作为关键记账成功条件（v2 §9.1）。
2. 所有事件统一 `await record()`。`tool.intent`、`execution.paused`、`execution.final` 等自身落盘后返回，失败停止当前执行；final 还须确认 output_refs 所引用的候选输出已可靠保存。普通事件入队后即可继续，失败只记诊断，不阻塞后续关键事件。
3. `local_seq` 在一个 Execution 内递增，`event_id` 由完整调用归属和 `local_seq` 确定；重传复用原 local_seq、event_id 和内容，服务端按 event_id 去重。
4. 事件流结束、文件存在、模型完成都不等于 Request 终态；终态以记账成功的 `execution.final` 或 `execution.paused` 为准。
5. 执行入口返回的事件流由 AgentServer 持续消费到结束，前端断开只结束展示订阅；停止或取消通过 `abort_persistent_request` 发起，不通过关闭事件流（v2 §13 第 12 条）。提前关闭事件流时入口等待原生流和清理结束，不产生结束事件。
6. 已接收、处理完成、允许发送、渠道送达分别确认。候选群消息或文件引用在服务端授权检查和投递回执前不是公开记录。

## 接口契约与错误语义

事件信封：

```text
AgentEvent {
  schema_version, event_id, event_type,
  namespace_id, agent_id, session_id, request_id, execution_id, source_ref,
  local_seq, occurred_at, visibility,
  correlation_refs, payload
}
```

`occurred_at` 只用于展示，日志读取按 local_seq 排序，序号缺口只记诊断，不要求全部补齐。同一 `event_id` 对应不同负载是冲突，不能覆盖。`visibility` 是建议标签，最终展示由服务端按当前授权和脱敏策略决定。

`AgentEvent.create(execution, ...)` 从 ExecutionContext 复制归属。`schema_version` 只支持严格整数 1，`local_seq` 为严格正整数；`occurred_at` 必须带时区并转为 UTC。`event_id` 为 `evt_` 加完整归属（含 `source_ref`）和 `local_seq` 的规范 JSON SHA-256；完整提案摘要另覆盖时间、类型、visibility、关联引用和负载。DTO 不分配序号、不保存去重状态。

事件及嵌套负载不可变，按 `event_type` 选择对应模型，拒绝错配和额外字段。标识最长 512 字符，文本最长 65536 字符（`TEXT_MAX_CHARS`），原因最长 4096 字符，引用最多 128 项；规范 UTF-8 提案上限 256 KiB（`EVENT_MAX_BYTES`，按规范 JSON 计算完整事件，含身份、序号、时间、目标等全部字段），超限改用受控引用。两条限额相互独立：字符数合法的文本，完整事件仍可能超出字节上限（例如四字节字符、经 JSON 转义变长的控制字符）。`AgentEvent.proposal_size()` 返回完整事件的规范字节数。visibility 只能是 private / progress / candidate，不允许 delivered。工具参数只以脱敏后的可读摘要出现，不直接携带凭证或原始参数。

`reply.delta` 使用 ReplyDeltaPayload，保留非空的空格、换行、制表符片段；完整正文和进度使用非空白文本。空字符串片段由流适配忽略，不生成事件。

| 事件 | 负载 | 级别 |
| --- | --- | --- |
| `participation.decided` | 判断、简要原因、证据引用、模型调用关联 | 只记录 |
| `execution.started` | 本次执行目标；群消息有参与判断时带决策事件引用，私聊和定时任务没有 | 只记录 |
| `progress.updated` / `reply.delta` | 可展示的脱敏进度、中间结论或片段 | 只记录 |
| `model.completed` | `model_call_id`、配置版本与实际用量 | 只记录 |
| `tool.intent` | `operation_id`、`tool_call_id`、工具名、可选目标、参数哈希、脱敏后的可读参数摘要 | 关键（仅写操作） |
| `tool.completed` | `tool_call_id`、工具名、参数摘要、成功 / 失败 / 未知、结果摘要、结果引用；写操作带对应的 `operation_id` | 只记录 |
| `window.switched` | 旧窗归档引用、新窗序号 | 只记录 |
| `output.proposed` | 稳定 `output_id`、目标、候选正文或文件引用、来源依赖 | 只记录（入队后返回；final 成功前检查其引用的候选输出已保存） |
| `execution.paused` | 暂停原因；等待确认时附被拦截的调用 | 关键 |
| `execution.final` | `completed / ignored / cancelled / failed`、产物和输出引用 | 关键 |

`execution.paused` 的原因固定为五种：`awaiting_confirmation / stopped / budget_denied / context_unfit / error`。只有 `awaiting_confirmation` 携带被拦截的调用列表，且至少一项；每项含 `tool_call_id`、工具名和脱敏后的可读参数，同一 `tool_call_id` 不能重复。`budget_denied`、`context_unfit`、`error` 必须说明原因。非完成终态和失败 / 未知的工具结果必须带原因；所有工具结果都必须有非空白的可读结果摘要，结果引用不能替代摘要。

| 端口方法 | 契约 |
| --- | --- |
| `record(execution, event) -> None` | 所有事件统一 await record；普通事件入队后返回，失败只记诊断，日志缺失不阻塞关键记账；关键事件等待自身落盘，final 还须确认 output_refs 所引用的候选内容与交接信息已可靠保存，关键调用失败停止当前 Execution |
| `read_execution_log(execution, execution_id) -> tuple[AgentEvent, ...]` | 按 local_seq 返回该次执行已保存的事件，允许缺口，非空不保证完整；服务端只允许读取同一 Session、同一 Request 下的执行。读取失败抛异常；续做时读不到或没有事件，按“上一次记录缺失”处理（PA-16）；工具结果缺失时先查证 |

执行适配须先对服务端返回的值重新 `model_validate`，再使用。

`record` 是唯一持久化通道，入口返回的事件流只用于实时展示，AgentServer 消费流时不再保存同一事件。

### 事件映射与结局

每个 Execution 由 `execution/emitter.py` 的 `EventEmitter` 唯一调用 `record`：在锁内分配 `local_seq`、构造事件、等待 `record` 返回，再放入展示队列，因此展示顺序与记账顺序一致。普通事件记账失败时仍会展示，只是日志出现缺口。容量检查由 emitter 统一完成：按两条限额拆分展示文本，或在记账前判断事件能否容纳；试探不调用 `record`，也不占用 `local_seq`。字段错误不算容量超限，仍按原规则抛出。

| 来源 | 事件 |
| --- | --- |
| 入口开始执行（恢复时不记） | `execution.started`，目标为任务正文 |
| 原生 `llm_output` 片段 | `reply.delta`；超过字符或完整事件字节上限的片段按字符边界拆成多段，拼接后与原文一致，不截断 |
| 模型调用结束且正文与工具调用同时存在 | `progress.updated`，正文截断到 500 字符 |
| 每次模型调用结束或失败 | `model.completed`：本次 `model_call_id`、配置版本，响应带用量时为实际用量，否则为未知 |
| 原生 `__interaction__` 片段 | 收集为 `paused(awaiting_confirmation)` 的被拦截调用：交互 ID、工具名、脱敏参数 |
| 结局 | `output.proposed`、`execution.paused` 或 `execution.final` |

模型调用的预算准入与用量回报由 `PersistentModelCallRail` 完成。它在 `PersistentAgentSpec.resolve_parts` 中加入，优先级 10，是模型调用前最后执行的钩子；没有执行入口时不生效，个人版和其他 DeepAgent 不受影响。每次调用生成新的 `model_call_id` 并先调用 `admit`：被拒绝或已收到停止 / 取消时，通过 force-finish 跳过模型调用并带回结构化标记；`admit` 抛异常时同样跳过，结局为 `paused(error)`。`report` 抛异常只记诊断，执行继续。同一个 rail 在工具调用前做相同的检查：已有致命错误或停止 / 取消时，force-finish 跳过每个尚未开始的工具（含只读工具），已经开始的并行调用按现有取消机制收敛；这个标记只用于结束原生循环，致命错误仍由入口原样抛出。

结局按优先级判定：

1. 关键事件记账失败：抛运行异常，不降级为 `paused(error)`。
2. 结局锁定前接受了 cancel：`final(cancelled)`；接受了 stop：`paused(stopped)`。原生调用退出、确定执行结果后同步锁定结局，此后到达的控制不被接受，不改变结局；锁定不代表持久化成功，会话状态记录保存和结束记账失败仍按原规则抛运行异常（见 [S_01](S_01_public-api-and-execution.md) 的执行入口编排）。
3. 有被拦截调用：`paused(awaiting_confirmation)`。
4. 预算拒绝或准入异常：`paused(budget_denied)` / `paused(error)`。
5. 原生结果为 answer：`final(completed)`。正文非空时先交出 `output.proposed`（`output_id` 由 `execution_id` 派生，`target_ref` 为回复目标，`share_mode=message`），并列入 `output_refs`。检查的是 after_invoke rail 之后的最终答案。字符数或完整事件字节数超限时改为 `paused(error)`，不截断；完整答案经会话状态记录保存（`ExecutionEnding.pending_answer`，PA-09），供后续交接继续使用。
6. 原生错误结果（含迭代次数耗尽）、原生异常或没有结果：`paused(error)`，错误文本不作为候选输出。

最终回答交接、会话状态记录保存、结束事件记账按此顺序进行。工具参数脱敏规则为：键名（不区分大小写）含 password、secret、token、key、credential、authorization 的值替换为 `***`，嵌套对象和列表同样处理。

## 状态及数据生命周期

```text
执行入口产出事件 → record() 进入服务端有序队列
  → 普通事件：入队即返回
  → 关键事件：等自身落盘后返回；final 另查所引用候选输出已保存
  → 服务端按 execution_id 保存，按 local_seq 返回已有日志，允许缺口
```

普通事件的调用失败由执行侧只记诊断并继续，异步保存失败由服务端记诊断，可以重试或跳过，不能卡住后续关键记账。不要求在关键事件前补回所有普通日志。关键事件自身保存失败，或 final 必需的候选输出未保存时，关键调用抛异常，执行侧停止当前 Execution。队列由服务端维护，不随 Agent 实例清理而取消；关键事件成功也不保证前序日志完整。续做时缺少 tool.completed 的写操作先查证，查不清询问发起人，不把缺失记录当作未执行。

需要交接的候选回复或文件必须列入 final.output_refs。`execution.final` 记账成功时，其引用的候选内容、目标和共享方式已可靠保存，服务端据此处理候选输出；其他普通日志无需全部保存。缺少被引用候选时 final 必须失败，不能删除输出引用后宣告完成。PA-04 / PA-07 可按原 event_id / output_id 重交候选，服务端去重，不重跑业务任务；沿用现有引用字段，不把完整正文加入 final。每项输出按 `output_id + target_ref + share_mode` 去重。只有文件结果的任务仍可 `completed`；若要群成员感知文件，`output.proposed` 须携带目标群、文件引用和共享方式（上传群空间，或作为群消息附件 / 可访问引用发送）。文件写入成功不自动形成群公开记录。

候选可先处于 `pending_check / held`。服务端发送检查与发送前复核通过才允许发送；平台回执才证明送达。投递等待或失败不重开业务 Execution，按原 `output_id` 重试交接。群回复沿相同的事件和结果链路，不允许通过普通 IM 工具绕过当前协作群的发送检查。Agent 停止后的旧候选不因重启自动补发，撤权内容不能继续投递。

## 与其它 spec 的关系

- [S_01](S_01_public-api-and-execution.md) 定义执行上下文、执行入口和宿主端口集合。
- [S_04](S_04_tools-and-workspace.md) 定义工具副作用与办公空间；写操作的意图记账见 v2 §9.1。
- 续做时的日志整理与恢复流程见 v2 §9.3、§11.2；S_05 尚按旧版架构编写，待 PA-16、PA-17 修订。
