# S_05 Checkpoint、控制与恢复规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 状态 | 拟议契约；尚未实现 |
| 关联模块 | `openjiuwen/harness/persistent_agent/{execution/checkpoint.py,execution/recovery.py,schema/execution.py}`（拟议路径） |
| 最近一次修订日期 | 2026-10-08 |
| 关联 feature | N/A；开发计划 PA-09、PA-17～PA-18 |
| 设计依据 | 旧版详细架构 §6.3、§14（见 Git 历史）；当前设计为 [Persistent Agent 详细架构设计](../designs/persistent-agent-architecture.md) |

当前没有本模块的 Checkpoint 或 RecoveryCoordinator 实现；本规约整理待实现的恢复契约。

## 范围 / 边界

本规约定义安全点快照、控制等待、原 Request 的恢复和外部操作核对。宿主负责持久化原始输入、登记新 Execution、围栏旧写入者、维护请求顺序和决定恢复调度；本模块只在获准的指定 Session 内重建可验证现场。

## 现有实现的复用边界

[`DeepAgent.save_state/load_state`](../../../deep_agent.py) 已将 [`DeepAgentState`](../../../schema/state.py) 的可序列化部分写入/读出当前 Session；复用该状态表示内部任务计划、迭代和模式等，不复制这些字段到另一套并行状态机。现有 [`TaskLoopEventHandler/TaskLoopEventExecutor`](../../../task_loop/) 处理活动调用内的交互、暂停与取消，恢复时优先接回其可验证的状态和控制入口。

这些实现尚不提供本规约的跨 Store 原子确认、Checkpoint 引用提交、原 Request 事件尾部归约、旧 epoch 围栏或外部操作回执核对。新增 `CheckpointBody` 只保存业务恢复所需的精确引用和边界，可引用已保存的 DeepAgent 状态；不能把一次 `save_state`、TaskLoop 的暂停结果或旧子代理线程直接当作已提交恢复点。

## 不变量

1. Checkpoint 正文保存成功不等于恢复点有效；只有宿主确认 `checkpoint.proposed` 的引用提交，才替换上一个有效恢复点。
2. Window 决定当前模型上下文，Checkpoint 加已提交事件尾部决定已确认业务进度，外部回执决定副作用真实结果。三者互不替代。
3. 恢复仍属于原 `request_id`，新 Execution 保留前次执行和恢复命令关联。原 Request 已有终态时不重开 Loop，只查询结果及投递交接。
4. 旧执行失权后不能提交进度或窗口；其可信在途操作回执仍须核对。不得通过重启重新生成 `operation_id` 执行一次非幂等操作。
5. 持久化等待、暂停、停止不释放同 Session 原 Request 的普通队列位置；只有已确认终态才可推进下一普通请求。
6. 恢复只重建可序列化且可验证的目标、待办、消息组、等待条件与操作状态；不反序列化旧线程、锁、连接、隐藏推理或凭证。

## 接口契约与错误语义

`CheckpointBody` 拟包含 `checkpoint_id, request_id, execution_id, config_version, applied_request_event_seq, window_ref, state, operation_refs, work_file_refs, pending_interactions`，并携带 schema 版本与完整归属。`applied_request_event_seq` 不得超过宿主已提交水位；Checkpoint 自身提交事件可在该边界之后。展示用 `reply.delta` 不参与状态归约。跨 Request 的 Window 来源须携带独立 `request_id`，不得比较两个 Request 的事件序号。

安全点包括已确认工具步骤后、等待控制前、主动暂停前和可恢复阶段结束。顺序为：先保存正文与所引对象，核验可读性、哈希及归属，再发 `checkpoint.proposed`，由宿主检查有效 epoch 和事件边界并确认。失败保留上一个恢复点。快照中未确认结论或工具状态标为 pending，不混入已确认进度。

控制须由宿主鉴权、去重、持久化并定位到 `interaction_id + request_id + execution_id`，审批还绑定操作目标、参数摘要、授权范围和有效期。匹配控制消费一次并进入已提交事件；过期或错配返回逻辑错误 `CONTROL_MISMATCH`，不能降级为已同意。短等待可在原有效 Execution 中继续；运行对象已释放的持久化等待，由新恢复 Execution 消费已持久化控制。普通新消息不得插入当前 Request。

| 恢复准入事实 | 行为 |
| --- | --- |
| 原 Request 已有终态 | 不启动业务 Loop；只核对结果和待投递项 |
| 原 Execution 有效且短等待 | 匹配控制在原调用继续，不建新 Execution |
| 持久化等待且运行对象已释放 | 控制已鉴权并持久化后，新 Execution 沿原 Request 继续 |
| 基础设施中断，引用完整且操作可核对 | 宿主可按 `automatic` 策略退避调度 |
| Agent 停止或人工暂停 | `explicit`，再次启动不自动唤醒 |
| 副作用结果不明、必要引用缺失或 schema 不支持 | `none`，保留原位置，等待核对或迁移 |

`automatic / explicit / none` 是恢复策略，不是 Request 终态或服务端状态枚举。`REFERENCE_UNAVAILABLE`、`OPERATION_UNKNOWN`、`EVENT_HISTORY_INCOMPLETE`、`SCHEMA_UNSUPPORTED`、`STALE_EXECUTION` 等为拟议逻辑错误，实施时映射项目错误体系。必要事实不明时必须暂停，不能用旧快照猜测、跳过原 Request 或把外部操作当作未执行。

## 状态及数据生命周期

宿主先核对原 Request 终态并围栏旧执行者，再以原配置版本与启动权限上限登记恢复 Execution。RecoveryCoordinator 按以下顺序重建：

1. 读取最近已提交 Checkpoint，验证归属、schema、正文与引用；没有 Checkpoint 时仅凭持久化输入和已提交事实且能证明安全，才可从初始状态恢复。
2. 从 `applied_request_event_seq + 1` 读到权威已提交水位，顺序归约控制消费、操作回执及结构化进度；尾部缺失时阻塞，不能只加载旧快照。
3. 读取当前有效 Window，并以当前权限核对必要工作文件及其状态；新 Window 若比 Checkpoint 新，保留有效指针，排除其中未确认业务事实。
4. 用原 `operation_id` 查询未决外部效果，核对等待条件及候选最终结果；已形成可核验结果时优先完成提交，不重新生成。
5. 排除撤权或过期资料，重新构造完整消息组和当前知识导航，从最近已确认的安全步骤继续。

恢复命令以原 Request、前次 Execution 和稳定命令标识去重，避免扫描反复创建 Execution。自动恢复前重新检查 Agent 启停、当前授权、执行权、配置版本、预算及未决操作。旧操作回执可由专门核对流程归档，但旧 epoch 不能直接提交新终态。停止和取消先收敛在途操作；取消完成需宿主条件提交，与并发完成竞态只能有一个终态胜出。工具超时或退出不保证补偿，需按 [S_04](S_04_tools-and-workspace.md) 的原操作协议核对。

## 与其它 spec 的关系

- [S_01](S_01_public-api-and-execution.md) 定义执行阶段和 Session 绑定。
- [S_02](S_02_events-and-result-handoff.md) 定义 Checkpoint 与终态事件的确认。
- [S_03](S_03_context-and-windows.md) 定义活动 Window 指针及普通保存。
- [S_04](S_04_tools-and-workspace.md) 定义外部操作的稳定身份和查询能力。
