# NativeHarness 中断结果输出前未持久化恢复状态

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-12 |
| 严重程度 | 高：进程重启后已展示的交互卡片可能无法恢复 |
| 范围 | `core/session/agent.py`、`agent_teams/harness/native_harness.py` |
| 测试基线 | `tests/unit_tests/agent_teams/harness/`：104 passed；`tests/unit_tests/core/session/test_session.py`：6 passed |
| 关联 spec | 无；补齐既有 interrupt checkpointer 契约 |

## 现象

`NativeHarness` 收到 `result_type="interrupt"` 的 round 结果后会立即把可恢复交互卡片写入输出流，
但不会调用 checkpointer 的 interrupt 保存入口。若进程在卡片输出后、常规 session 收尾提交前重启，
相同 session id 创建的新 `Session` 无法恢复 `ToolInterruptionState`，已展示的卡片因而没有可续接状态。

## 根因

常规单 Agent 执行路径在产生交互中断时会调用
`Checkpointer.interrupt_agent_execute()`；`NativeHarness` 自己驱动 outer round，绕过了该调用点，
并直接使用 `_write_round_result_to_stream()` 暴露结果。`Session` 公开表面只有 completion 语义的
`commit()`，harness 没有可复用的 interrupt 语义入口。

缺失的不只是一次保存，还包括顺序保证：如果先输出卡片再保存，保存失败或进程在两步之间退出时，
调用方仍会看到一个实际上无法恢复的交互入口。

## 决策

1. 在 `Session` 增加 `commit_interrupt()`，只代理当前 session checkpointer 的
   `interrupt_agent_execute()`，不关闭 stream。
2. `NativeHarness` 集中处理 round 结果：控制结果保持不输出；interrupt 结果先等待
   `commit_interrupt()` 成功，再写入输出流；普通结果仍直接写流。
3. interrupt checkpoint 失败时不吞异常，也不输出卡片，由现有 round failure 通道处理，避免暴露
   不可恢复的 UI 状态。

## 拒绝的方案

- **复用 `Session.commit()`**：该入口表达正常完成提交，无法保留 checkpointer 对中断原因的专用处理与日志语义。
- **在 JiuwenSwarm 收到卡片后补保存**：落盘责任会泄漏到调用方，其他 `NativeHarness` 消费者仍会复现，
  且无法消除输出与保存之间的竞态。
- **保存失败后仍输出卡片**：会把持久化故障伪装成可恢复交互，重启后只能得到陈旧或空状态。

## 验证

回归测试覆盖以下行为：interrupt 保存严格早于卡片输出；普通结果不增加 interrupt 保存；保存失败不
输出卡片；SQLite checkpointer 关闭并重建后，同 session id 的全新 `Session` 能恢复完整
`ToolInterruptionState`。

## 已知遗留

本修复只约束 `NativeHarness` 产生的 agent interrupt 结果。workflow interrupt 继续走既有
`post_workflow_execute()` 持久化路径，不在本次范围内。
