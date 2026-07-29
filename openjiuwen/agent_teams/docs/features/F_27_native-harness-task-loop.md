# F_27 NativeHarness over the full DeepAgent task loop

日期：2026-06-02

## 背景

`NativeHarness`（e05888fa1 由 SuperHarness 迁移而来）的 `_run_round` 直接调
`react_agent.invoke(_streaming=True)`，**绕过了 DeepAgent 的整个 task_loop
层**，丢失 18 项能力：task plan、stop-condition 评估、SESSION_SPAWN 异步子
agent、BEFORE/AFTER_TASK_ITERATION 事件、follow_up 续轮、interrupt/resume、
plan_mode、DeepAgentState 管理、evolution / context_evolution rail 等。

诉求：native harness 的**能力必须与 DeepAgent 完全一致**，只重新设计交互方式
（并发安全 + 易用），解决原 `steer / follow_up / abort` + generator 流式在并发
下的语义模糊（abort 不立即、steer 中间态丢失、generator yield 阻塞读不到下一条
输入）。

## 决策

`class NativeHarness(DeepAgent)` —— 继承 DeepAgent，复用 task_loop 全部内核
（TaskLoopController / LoopCoordinator / TaskLoopEventHandler /
TaskLoopEventExecutor / LoopQueues / task_plan / session_spawn），仅用单协程
supervisor 替换 `_run_task_loop` 的 `while should_continue` 自驱动。交互层重新
设计为：

- **单 supervisor + control channel**（asyncio.Queue[ControlEvent]）：唯一的
  HarnessInternalState writer，外部 API（send/abort/pause/stop）只入队
  ControlEvent + await ack Future，因此天然并发安全。
- **输入两语义**：`send(immediate=True)` 注入当前 round（push_steer）；
  `send(immediate=False)` 缓冲，round 结束后续轮。
- **分级中断**：graceful（`coordinator.request_abort`，当前 round 收尾即停）/
  immediate（`task_scheduler.cancel_task` 硬停 + 回滚 DeepAgentState+context 到
  round 边界 snapshot）/ pause（cancel + 回滚 pre-round baseline + 缓存 query，
  下次 send 拼接重启）。
- **输出走 asyncio.Queue**（forwarder 泵 `session.stream_iterator()` → output
  queue），不暴露 generator。
- **交互契约抽象为 `HarnessProtocol`**（见 S_18）：阶段 2 的
  StreamController-backed harness 实现同一 Protocol，对外表面统一。

每个 outer round 在独立 task 跑 `submit_round(task_id=...) +
wait_round_completion`（走真 executor → BEFORE/AFTER_TASK_ITERATION + react
invoke + task_plan + steering + session_spawn）；`_on_round_done` 复用原
`_run_task_loop` 的多轮决策（follow_up > pending > task_plan remaining）。
覆写 `schedule_auto_invoke_on_spawn_done` 让 SESSION_SPAWN 完成走 supervisor，
不绕过单写者。

## 命门验证（cancel 链）

immediate abort 必须真停 executor。`submit_round` 只 `add_task` 后立即返回，真
正的 `react_agent.invoke` 跑在 TaskScheduler 自己 `create_task` 的 exec_task
里——cancel「跑 wait_round_completion 的 task」只解除 await，executor 变孤儿，
LLM/工具继续跑（假 abort）。正确链：`controller.task_scheduler.cancel_task(
task_id)` → `executor.cancel`（`coordinator.request_abort` +
`task_plan.mark_cancelled`）+ `exec_task.cancel`；`react_agent` 的
`except asyncio.CancelledError` 清当前轮 messages 后 re-raise，干净停止。

为拿到 task_id，`TaskLoopController.submit_round` 加 kw-only `task_id` 写进
`event.metadata["task_id"]`（handler.handle_input 已读）——这是对 task_loop 的
**唯一侵入**（纯加法，team 路径不传则行为不变，deep_agent 回归 48 passed 证明
向后兼容）。

跨 round 复用同一 coordinator，故 immediate abort 后必须 `coordinator.reset()`，
否则 `is_aborted` 永久污染 `should_continue`（单测专项断言 reset 后下一轮能跑）。

## 拒绝的方案

- **直接接 `react_agent.invoke`（现状）**：丢 task_loop 全部能力。否决。
- **组合 + 走 `DeepAgent.stream`（如 TeamHarness）**：保留能力，但 stream 是
  generator + 内部 event 注入，无法根治并发/中断语义；immediate abort/pause 的
  分级回滚也难精确做。本次选继承覆写，以精确接管 outer round 驱动。
- **cancel 跑 wait 的 task 做 immediate abort**：留孤儿 executor，假 abort。否决。

## 已知遗留

- **task_plan 回滚语义**：immediate abort/pause 回滚 DeepAgentState 会撤销本
  round 的 task 进度标记，与「工具外部副作用不可撤销」存在固有不一致——状态说没
  做、文件已改。属 immediate 中断的固有代价，docstring 已声明。
- **阶段 2（后续）**：StreamController 接管 round 驱动、实现 HarnessProtocol、
  统一 team 与 native 两条路径。本次只交付独立 single-agent NativeHarness，接口
  按统一预留（HarnessProtocol + ActiveRound.task_id 统一 round 标识）。
- **team 路径预先存在债（与本特性无关，e05888fa1 迁移遗留）**：
  `agent_teams.harness.__init__` 未导出 `_MountedRails`，导致
  `test_harness.py` / `test_stream_controller.py` collection error；修复后又暴露
  2 个 stale test（patch 不存在的 `harness.Runner`）。本特性未碰 team 路径，建议
  单独修复。

## 验证基线

```
tests/unit_tests/agent_teams/harness/  28 passed
tests/unit_tests/harness/test_deep_agent.py  48 passed  (submit_round 向后兼容)
```

测试用**真 task_loop 内核 + FakeReactAgent 注入**（`set_react_agent(fake,
initialized=True)`，被真 TaskScheduler 调度），非 mock 整个 agent；覆盖单轮 /
follow_up 续轮 / cancel 链专项（fake invoke `cancelled_count==1`）/ pause 合并
重启 / graceful 不续轮 / immediate steer 注入 / stop 无后台 task 泄漏 /
`isinstance(harness, HarnessProtocol)`。
