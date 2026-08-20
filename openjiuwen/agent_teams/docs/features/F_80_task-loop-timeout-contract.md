# F_80 Task-loop timeout contract

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-13 |
| 范围 | `openjiuwen/harness/task_loop`、`NativeHarness`、`DeepAgentSpec` |
| 测试基线 | `103 passed`（agent-core 专项）+ `47 passed`（JiuwenSwarm 直接影响回归） |

## 背景

普通 DeepAgent 的 task-loop 在 `completion_timeout` 到期后只返回
`{"error": "completion_timeout"}`。回答流适配器只保留 `output` 与
`result_type`，因此调用方看到空白正常结束；scheduler 中的真实任务未取消，继续在后台运行。
长任务由上层误配固定超时时，这个缺陷会稳定表现为“前台停止、后台继续”。

## 决策

- `TaskLoopEventHandler` 按 `round_id` 保存已注册的 scheduler task id。
- `TaskLoopController.submit_round` 使用既有 EventQueue 同步发布 API，待 input handler 完成任务注册后
  才返回，随后才启动完成等待；这既消除短超时先于 task id 注册的竞态，也保留事件顺序、异常包装
  和观测扩展点。
- 普通 DeepAgent 超时时请求 coordinator abort，并通过 `TaskScheduler.cancel_task` 取消当前任务；
  清理等待最多 1.5 秒，慢清理转后台完成，前台及时收到标准 error result。
- 没有独立错误帧的超时和提交失败返回 `output/result_type/error`；已有
  `controller_output/task_failed` 的执行失败保持原结果，避免重复错误提示。
- `DeepAgentConfig` 与 `DeepAgentSpec` 接受 `completion_timeout=None`，表示普通 DeepAgent 不设
  单轮硬超时。NativeHarness 延续原契约：同字段只做慢轮次告警，`None` 禁用告警 task。

## 拒绝的方案

- **上层运行时 monkey patch SDK 类方法**：依赖私有字段和当前方法签名，全局生效，升级易失效。
- **只把默认超时调大**：仍会在更晚时刻复现，而且裸错误和后台孤儿任务继续存在。
- **超时后只返回错误、不取消 scheduler task**：保留后台副作用和资源泄漏，正是原故障的一半。
- **无限等待取消完成**：底层 LLM I/O 不响应取消时会让超时错误本身无法及时返回。

## 验证

- handler 单测覆盖标准错误结果、scheduler cancel、提交注册顺序。
- 真实 task-loop 内核测试覆盖慢模型超时、前台错误输出和 executor 实际取消。
- DeepAgent interaction 与 NativeHarness 状态机/失败重试回归覆盖正常、失败和慢轮次路径。
- JiuwenSwarm 使用 `completion_timeout=None` 的回归覆盖由上层仓库执行。

## 已知遗留

- `completion_timeout` 在普通 DeepAgent 与 NativeHarness 的语义不同，属于既有兼容契约；未来若统一，
  应拆成 `round_timeout` 与 `slow_round_warning_after` 两个字段并走弃用周期。
