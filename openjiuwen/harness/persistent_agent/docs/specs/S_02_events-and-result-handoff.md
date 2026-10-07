# S_02 统一事件与结果交接规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | 事件 DTO 和记账 / 执行日志端口契约已按 v2 实现（PA-22）；事件映射、记账调用和暂停收尾待 PA-04 |
| 关联模块 | 内部 `persistent_agent/{ports.py,schema/{protocol,events,port_results}.py}` |
| 最近一次修订日期 | 2026-10-07 |
| 关联 feature | [F_02 执行协议](../features/F_02_execution-protocol.md)、[F_04 按 v2 收敛契约](../features/F_04_v2-contract-convergence.md)；开发计划 PA-04、PA-07、PA-14、PA-16、PA-22 |
| 设计依据 | [Persistent Agent 设计（v2）](../designs/persistent-agent-design-v2.md) §9、§13 第 5、12 条、§14 |

当前可以构造 `AgentEvent` 及其 11 种类型化负载，并已定义 `CommitPort.record / read_execution_log` 协议。尚没有把原生流映射为事件的执行适配，也没有生产记账后端；下文的记账时序和交接约束是 PA-04 及服务端要满足的运行契约。

## 范围 / 边界

本规约规定执行侧产出的事件、交给服务端记账的方式、执行日志的读取，以及候选输出的交接。事件队列、持久化、执行日志存储、发送检查和渠道投递由 AgentServer 实现；本模块不在本地另写日志文件。

## 现有实现的复用边界

[`DeepAgent.stream`](../../../deep_agent.py) 已经产生模型、工具与交互输出。执行入口消费这些输出并映射为本规约事件，不重写模型 / 工具循环，也不复制一套原生流协议。展示片段可以沿用 DeepAgent 的流输出，但流结束不代表任务完成。

## 不变量

1. 同一 Execution 的所有事件按产生顺序进入服务端维护的同一个有序队列；关键事件不能越过尚未落盘的前序事件（v2 §9.1）。
2. `tool.intent`、`execution.paused`、`execution.final` 记账返回时，此前事件和本事件已全部落盘；记账失败就停止当前执行。其他事件入队即可继续。
3. `local_seq` 在一个 Execution 内递增，`event_id` 由完整调用归属和 `local_seq` 确定，重传复用原 `event_id`。
4. 事件流结束、文件存在、模型完成都不等于 Request 终态；终态以记账成功的 `execution.final` 或 `execution.paused` 为准。
5. 执行入口返回的事件流由 AgentServer 持续消费到结束，前端断开只结束展示订阅；停止或取消通过 `abort_bound` 发起，不通过关闭事件流（v2 §13 第 12 条）。
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

`occurred_at` 只用于展示，排序以服务端队列顺序为准。同一 `event_id` 对应不同负载是冲突，不能覆盖。`visibility` 是建议标签，最终展示由服务端按当前授权和脱敏策略决定。

`AgentEvent.create(execution, ...)` 从 ExecutionContext 复制归属。`schema_version` 只支持严格整数 1，`local_seq` 为严格正整数；`occurred_at` 必须带时区并转为 UTC。`event_id` 为 `evt_` 加完整归属（含 `source_ref`）和 `local_seq` 的规范 JSON SHA-256；完整提案摘要另覆盖时间、类型、visibility、关联引用和负载。DTO 不分配序号、不保存去重状态。

事件及嵌套负载不可变，按 `event_type` 选择对应模型，拒绝错配和额外字段。标识最长 512 字符，文本最长 65536 字符，原因最长 4096 字符，引用最多 128 项；规范 UTF-8 提案上限 256 KiB，超限改用受控引用。visibility 只能是 private / progress / candidate，不允许 delivered。工具参数只以脱敏后的可读摘要出现，不直接携带凭证或原始参数。

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
| `output.proposed` | 稳定 `output_id`、目标、候选正文或文件引用、来源依赖 | 只记录 |
| `execution.paused` | 暂停原因；等待确认时附被拦截的调用 | 关键 |
| `execution.final` | `completed / ignored / cancelled / failed`、产物和输出引用 | 关键 |

`execution.paused` 的原因固定为五种：`awaiting_confirmation / stopped / budget_denied / context_unfit / error`。只有 `awaiting_confirmation` 携带被拦截的调用列表，且至少一项；每项含 `tool_call_id`、工具名和脱敏后的可读参数，同一 `tool_call_id` 不能重复。`budget_denied`、`context_unfit`、`error` 必须说明原因。非完成终态和失败 / 未知的工具结果必须带原因；成功的工具结果必须带结果摘要或结果引用。

| 端口方法 | 契约 |
| --- | --- |
| `record(execution, event) -> None` | 普通事件进入服务端有序队列后返回；关键事件等此前事件和本事件全部落盘后返回。入队失败，或关键事件及其前序事件无法保存，都抛异常，执行侧停止当前 Execution |
| `read_execution_log(execution, execution_id) -> tuple[AgentEvent, ...]` | 按产生顺序返回该次执行已保存的事件；服务端只允许读取同一 Session、同一 Request 下的执行。读取失败抛异常；续做时读不到或没有事件，按“上一次记录缺失”处理（PA-16） |

执行适配须先对服务端返回的值重新 `model_validate`，再使用。

## 状态及数据生命周期

```text
执行入口产出事件 → record() 进入服务端有序队列
  → 普通事件：入队即返回
  → 关键事件：等此前事件和本事件全部落盘后返回
  → 服务端按 execution_id 保存，构成执行日志
```

服务端保存失败时可以重试，但不能跳过失败事件；重试仍失败时，等待中的关键记账调用抛异常，后续关键事件也不能返回成功。队列由服务端维护，不随 Agent 实例清理而取消。崩溃发生在下一次关键记账成功之前时，尚未落盘的普通事件可能缺失，续做按查证规则处理。

正常完成时，`execution.final` 记账成功即表示此前的 `output.proposed` 等事件也已落盘，服务端据此处理候选输出。每项输出有稳定 `output_id`，按 `output_id + target_ref + share_mode` 去重。只有文件结果的任务仍可 `completed`；若要群成员感知文件，`output.proposed` 须携带目标群、文件引用和共享方式（上传群空间，或作为群消息附件 / 可访问引用发送）。文件写入成功不自动形成群公开记录。

候选可先处于 `pending_check / held`。服务端发送检查与发送前复核通过才允许发送；平台回执才证明送达。投递等待或失败不重开业务 Execution，按原 `output_id` 重试交接。群回复沿相同的事件和结果链路，不允许通过普通 IM 工具绕过当前协作群的发送检查。Agent 停止后的旧候选不因重启自动补发，撤权内容不能继续投递。

## 与其它 spec 的关系

- [S_01](S_01_public-api-and-execution.md) 定义执行上下文、执行入口和宿主端口集合。
- [S_04](S_04_tools-and-workspace.md) 定义工具副作用与办公空间；写操作的意图记账见 v2 §9.1。
- 续做时的日志整理与恢复流程见 v2 §9.3、§11.2；S_05 尚按旧版架构编写，待 PA-16、PA-17 修订。
