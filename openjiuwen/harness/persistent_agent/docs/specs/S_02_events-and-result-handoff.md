# S_02 统一事件与结果交接规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | 拟议契约；尚未实现 |
| 关联模块 | `openjiuwen/harness/persistent_agent/{execution/event_emitter.py,schema/events.py}`（拟议路径） |
| 最近一次修订日期 | 2026-09-25 |
| 关联 feature | N/A；开发计划 PA-04、PA-07 |
| 设计依据 | [详细架构设计](../designs/persistent-agent-architecture.md) §8 |

本规约描述目标协议，当前没有可调用的 `AgentEvent`、`EventEmitter` 或 `CommitPort` 实现。

## 范围 / 边界

本规约规定执行器向宿主交付事件、等待权威提交确认、处理背压以及交接候选输出。SessionStore 的事件顺序与终态、群预算账户、发送检查和渠道投递由 AgentServer 实现；本模块不维护第二份权威业务日志。

## 现有实现的复用边界

[`DeepAgent.stream`](../../../deep_agent.py) 和现有任务循环已经产生模型、工具与交互输出；`LoopAdapter` 应消费这些输出并映射为本规约事件，不重写模型/工具循环，也不复制一套原生流协议。现有流片段本身没有宿主分配的 `request_event_seq` 或权威提交确认；稳定 `event_id`、有界交接队列、`CommitPort` 查询与 `output_id` 投递关联仍是本模块及宿主的新增适配。展示片段可沿用 DeepAgent 流输出，不能直接充当已提交进度。

## 不变量

1. 事件被生成或流被消费只表示“已提出”；只有宿主确认才成为业务事实。流结束、文件存在、模型完成均不等于 Request 终态。
2. `local_seq` 在一个 Execution 内递增，重传复用原 `event_id`；宿主的 `request_event_seq` 在原 Request 的已提交流内递增，可跨多个恢复 Execution。三者互不替代。
3. 依赖参与决策、执行开始、工具意图或回执、Checkpoint、等待点或终态的下一步，须等对应提交确认。确认未知先查询，不重做模型或外部副作用。
4. 事件生产和宿主消费并行，输出队列有界且满时背压；不能丢弃工具回执和最终事件，也不能因 Web 订阅断开自动取消后台业务执行。
5. 已接收、处理完成、允许发送、渠道送达分别确认。候选群消息或文件引用在服务端授权检查和投递回执前不是公开记录。
6. 旧 epoch 事件不能覆盖当前进度；迟到的可信工具回执仍可进入独立核对流程。

## 接口契约与错误语义

拟议事件信封至少包含：

```text
AgentEvent {
  schema_version, event_id, event_type,
  namespace_id, agent_id, session_id, request_id, execution_id,
  epoch, local_seq, occurred_at, visibility,
  correlation_refs, payload
}
```

`occurred_at` 只用于展示；排序依据为宿主提交序号。同一 `event_id` 对应不同负载是冲突，不能覆盖。`visibility` 是建议标签，最终展示仍由宿主按当前授权和脱敏策略决定。

| 事件 | 负载重点 | 提交语义 |
| --- | --- | --- |
| `participation.decided` | 判断、简要原因、证据版本、模型调用关联 | 已确认后才能进入正式执行或完成忽略分支 |
| `execution.started` | 本次执行目标及决策引用 | 正式任务投影的起点；不等于模型执行成功 |
| `progress.updated` / `reply.delta` | 可展示的脱敏进度或片段 | 不参与恢复状态归约，群聊默认不逐片投递 |
| `output.proposed` | 稳定 `output_id`、目标、候选正文或文件引用、来源依赖 | 只是发送检查交接，不表示已公开 |
| `model.completed` | `model_call_id`、版本与实际用量 | 宿主预算对账依据 |
| `tool.intent` / `tool.completed` | `operation_id`、目标、参数摘要、受控结果引用与回执 | 副作用前后确认；详见 S_04 |
| `checkpoint.proposed` | 可读正文引用及事件边界 | 宿主确认后才成为有效恢复点 |
| `control.waiting` / `execution.suspended` | 等待条件、恢复引用、原因 | 不释放原 Request 的普通队列位置 |
| `window.activated` | 已生效窗口版本与 head 修订 | 只关联已生效指针，不决定窗口是否生效 |
| `execution.final` | `completed / ignored / cancelled / failed-final`、产物和输出引用 | 宿主条件提交后才是终态 |

`CommitPort` 逻辑上提供按 `event_id`、Request 和 `operation_id` 查询确认结果及已提交水位。确认拒绝则停止依赖步骤；确认超时或断连是 `COMMIT_UNKNOWN`，不能推断失败。已确认前缀可由宿主续传。生产者丢失后，新 Execution 使用自己的 `local_seq` 接续原 Request 的持久化流，不伪造旧事件或补齐无法恢复的本地序号。

## 状态及数据生命周期

```text
EventEmitter 稳定编号 → 有界输出流 → 宿主校验、去重与持久化
  → CommitPort 确认或查询 → 执行器推进依赖步骤
```

执行器可用内部生产协程实现并行消费，但不可在事件尚未交给消费者前同步等待其提交，也不可要求宿主在调用结束后才提交。宿主消费链路断开时停止新副作用并收敛在途调用；内存队列本身不是持久化保证。

正常完成时，服务端须在同一事务或可恢复提交单元内保存最终结果、可查询产物引用及必要的候选输出交接记录。每项输出有稳定 `output_id`，按 `output_id + target_ref + share_mode` 去重。只有文件结果的任务仍可 `completed`；若要群成员感知文件，`output.proposed` 须携带目标群、文件引用和共享方式（上传群空间，或作为群消息附件／可访问引用发送）。文件写入成功不自动形成群公开记录。

候选可先处于 `pending_check / held`。服务端发送检查与发送前复核通过才允许发送；平台回执才证明送达。投递等待或失败不重开业务 Execution，按原 `output_id` 重试交接。群回复沿相同事件和结果链路，不允许通过普通 IM 工具绕过当前协作群的发送检查。Agent 停止后的旧候选不因重启自动补发，撤权内容不能继续投递。

## 与其它 spec 的关系

- [S_01](S_01_public-api-and-execution.md) 定义执行阶段与终态的业务含义。
- [S_04](S_04_tools-and-workspace.md) 定义 `tool.intent / tool.completed` 与外部写入核对。
- [S_05](S_05_checkpoint-and-recovery.md) 定义 Checkpoint 确认和事件尾部归约。
